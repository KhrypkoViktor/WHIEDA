"""Single release gate for WHIEDA partner pilot deploys.

Gates:
  - no active TEMP workflows
  - external canary green
  - P0 smoke >= 30 cases, 0 failed
  - unified smoke 100%
  - regression >= 98%
  - SQL p95 <= 3s (when performance report present)
  - optional: require two consecutive green runs via --history-file
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
EXPORT_DIR = BASE_DIR.parent / "live-exports" / datetime.now(timezone.utc).date().isoformat()
HISTORY_DEFAULT = BASE_DIR.parent / "live-exports" / "WHIEDA_release_gate_history.jsonl"


def run_script(name: str, script: str, *, timeout: int = 900) -> dict:
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, str(BASE_DIR / script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    return {
        "name": name,
        "script": script,
        "exit_code": completed.returncode,
        "duration_sec": round(time.perf_counter() - started, 1),
        "stdout_tail": (completed.stdout or "")[-1500:],
        "stderr_tail": (completed.stderr or "")[-1500:],
    }


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def temp_workflow_count() -> int:
    result = run_script("temp_count", "run_whieda_count_temp_workflows_2026-08-02.py", timeout=120)
    if result["exit_code"] != 0:
        return -1
    try:
        payload = json.loads(result["stdout_tail"].splitlines()[-1])
        return int(payload.get("temp_active", payload.get("temp_count", -1)))
    except (ValueError, json.JSONDecodeError, IndexError):
        return -1


def evaluate_p0(report: dict | None) -> tuple[bool, dict]:
    meta = (report or {}).get("meta", {})
    total = int(meta.get("cases_total") or meta.get("total") or 0)
    failed = int(meta.get("failed") or 0)
    ok = total >= 30 and failed == 0
    return ok, {"total": total, "failed": failed, "required_min": 30}


def evaluate_unified(report: dict | None) -> tuple[bool, dict]:
    meta = (report or {}).get("meta", {})
    total = int(meta.get("total") or 0)
    passed = int(meta.get("passed") or 0)
    failed = int(meta.get("failed") or 0)
    ok = total > 0 and failed == 0 and passed == total
    return ok, {"total": total, "passed": passed, "failed": failed}


def evaluate_regression(report: dict | None) -> tuple[bool, dict]:
    meta = (report or {}).get("meta", {})
    total = int(meta.get("total") or meta.get("cases_total") or 0)
    passed = int(meta.get("passed") or 0)
    failed = int(meta.get("failed") or 0)
    rate = round((passed / total) * 100, 2) if total else 0.0
    ok = total > 0 and rate >= 98.0
    return ok, {"total": total, "passed": passed, "failed": failed, "pass_rate": rate}


def evaluate_performance(report: dict | None) -> tuple[bool, dict]:
    if not report:
        return True, {"skipped": True, "reason": "no_performance_report"}
    p95 = report.get("p95_ms") or report.get("p95")
    if p95 is None:
        return True, {"skipped": True, "reason": "no_p95_metric"}
    ok = float(p95) <= 3000
    return ok, {"p95_ms": p95, "limit_ms": 3000}


def consecutive_green(history_file: Path, required: int = 2) -> tuple[bool, list[dict]]:
    if not history_file.exists():
        return False, []
    rows = []
    for line in history_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    recent = [row for row in rows if row.get("pass") is True][-required:]
    return len(recent) >= required, recent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-heavy", action="store_true", help="Skip P0/regression (canary + unified only)")
    parser.add_argument("--require-consecutive", type=int, default=0, help="Require N consecutive green runs in history")
    parser.add_argument("--history-file", type=Path, default=HISTORY_DEFAULT)
    parser.add_argument("--no-append-history", action="store_true")
    args = parser.parse_args()

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    gates: dict[str, object] = {"checked_at": datetime.now(timezone.utc).isoformat()}
    failures: list[str] = []

    temp_cleanup = run_script("temp_cleanup", "run_whieda_deactivate_temp_workflows_2026-08-01.py", timeout=180)
    gates["temp_cleanup"] = temp_cleanup
    temp_count = temp_workflow_count()
    gates["temp_active"] = temp_count
    if temp_count != 0:
        failures.append(f"temp_workflows_active={temp_count}")

    canary = run_script("canary", "whieda_external_canary_2026-08-02.py", timeout=300)
    gates["canary"] = canary
    if canary["exit_code"] != 0:
        failures.append("canary_failed")

    unified = run_script("unified_smoke", "whieda_unified_smoke_pack_2026-08-01.py", timeout=600)
    gates["unified_smoke"] = unified
    unified_report = load_json(EXPORT_DIR / "WHIEDA_unified_smoke_pack_2026-08-01.json")
    unified_ok, unified_meta = evaluate_unified(unified_report)
    gates["unified_meta"] = unified_meta
    if unified["exit_code"] != 0 or not unified_ok:
        failures.append("unified_smoke_failed")

    lead_ref = run_script("lead_ref_pilot", "whieda_lead_ref_pilot_smoke_2026-08-02.py", timeout=300)
    gates["lead_ref_pilot"] = lead_ref
    if lead_ref["exit_code"] != 0:
        failures.append("lead_ref_pilot_failed")

    if not args.skip_heavy:
        p0 = run_script("p0", "whieda_live_p0_smoke_2026-07-13.py", timeout=1200)
        gates["p0"] = p0
        p0_report = load_json(EXPORT_DIR / "WHIEDA_live_p0_smoke_report.json")
        p0_ok, p0_meta = evaluate_p0(p0_report)
        gates["p0_meta"] = p0_meta
        if p0["exit_code"] != 0 or not p0_ok:
            failures.append("p0_failed")

        regression = run_script("regression", "run_whieda_live_regression_suite_v1_2026-07-15.py", timeout=1800)
        gates["regression"] = regression
        regression_report = load_json(EXPORT_DIR / "WHIEDA_live_regression_suite_v1.json")
        regression_ok, regression_meta = evaluate_regression(regression_report)
        gates["regression_meta"] = regression_meta
        if regression["exit_code"] != 0 or not regression_ok:
            failures.append("regression_failed")

        perf_report = load_json(EXPORT_DIR / "WHIEDA_live_sql_performance_smoke.json")
        perf_ok, perf_meta = evaluate_performance(perf_report)
        gates["performance_meta"] = perf_meta
        if not perf_ok:
            failures.append("sql_p95_exceeded")

    passed = not failures
    gates["pass"] = passed
    gates["failures"] = failures

    if args.require_consecutive > 0:
        if passed and not args.no_append_history:
            args.history_file.parent.mkdir(parents=True, exist_ok=True)
            with args.history_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"checked_at": gates["checked_at"], "pass": True}, ensure_ascii=False) + "\n")
        ok_hist, recent = consecutive_green(args.history_file, args.require_consecutive)
        gates["consecutive_required"] = args.require_consecutive
        gates["consecutive_recent"] = recent
        if not ok_hist:
            failures.append(f"need_{args.require_consecutive}_consecutive_green")
            passed = False
            gates["pass"] = False
            gates["failures"] = failures

    out = EXPORT_DIR / "WHIEDA_release_gate_report.json"
    out.write_text(json.dumps(gates, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pass": passed, "failures": failures, "report_path": str(out)}, ensure_ascii=False, indent=2))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
