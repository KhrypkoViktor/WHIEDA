"""Daily SQL quality metrics for Structure Basic pilot."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
EXPORT_DIR = BASE_DIR.parent / "live-exports" / datetime.now(timezone.utc).date().isoformat()
OUT = EXPORT_DIR / "WHIEDA_sql_quality_daily_report.json"


def load_json(name: str) -> dict | None:
    path = EXPORT_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    scripts = [
        ("owner_queue", "export_owner_review_queue_2026-08-01.py"),
        ("alias_gap", "run_whieda_alias_gap_report_2026-08-01.py"),
    ]
    ran = {}
    for name, script in scripts:
        completed = subprocess.run(
            [sys.executable, str(BASE_DIR / script)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
        )
        ran[name] = {"exit_code": completed.returncode, "stdout_tail": (completed.stdout or "")[-800:]}

    p0 = load_json("WHIEDA_live_p0_smoke_report.json")
    regression = load_json("WHIEDA_live_regression_suite_v1.json")
    unified = load_json("WHIEDA_unified_smoke_pack_2026-08-01.json")
    alias_gap = load_json("WHIEDA_alias_gap_report.json")
    owner_queue = load_json("WHIEDA_owner_review_queue.json")

    p0_meta = (p0 or {}).get("meta", {})
    reg_meta = (regression or {}).get("meta", {})
    unified_meta = (unified or {}).get("meta", {})

    p0_total = int(p0_meta.get("cases_total") or p0_meta.get("total") or 0)
    p0_failed = int(p0_meta.get("failed") or 0)
    p0_pass_rate = round(((p0_total - p0_failed) / p0_total) * 100, 2) if p0_total else 0.0

    reg_total = int(reg_meta.get("total") or reg_meta.get("cases_total") or 0)
    reg_failed = int(reg_meta.get("failed") or 0)
    reg_pass_rate = round(((reg_total - reg_failed) / reg_total) * 100, 2) if reg_total else 0.0

    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "metrics": {
            "p0_total": p0_total,
            "p0_failed": p0_failed,
            "p0_pass_rate": p0_pass_rate,
            "regression_total": reg_total,
            "regression_failed": reg_failed,
            "regression_pass_rate": reg_pass_rate,
            "unified_total": int(unified_meta.get("total") or 0),
            "unified_failed": int(unified_meta.get("failed") or 0),
            "alias_missing_in_live": (alias_gap or {}).get("missing_in_live"),
            "owner_review_pending": len((owner_queue or {}).get("rows") or []),
        },
        "targets": {
            "p0_min_cases": 30,
            "p0_pass_rate": 100.0,
            "regression_pass_rate": 98.0,
            "sql_coverage_supported_corpus": 95.0,
            "fallback_rate_max": 2.0,
        },
        "scripts": ran,
        "status": "green"
        if p0_total >= 30
        and p0_failed == 0
        and reg_pass_rate >= 98.0
        and int(unified_meta.get("failed") or 1) == 0
        else "attention",
    }
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "report_path": str(OUT), "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    if report["status"] != "green":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
