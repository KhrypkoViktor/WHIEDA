#!/usr/bin/env python3
"""HTTP Core advisor parity runner — localhost:8080 only."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PARITY_ROOT = Path(__file__).resolve().parent
ACCEPTANCE_ROOT = PARITY_ROOT.parent / "acceptance"
REPO_ROOT = PARITY_ROOT.parents[1]
LAB = PARITY_ROOT / "lab"

if str(ACCEPTANCE_ROOT) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE_ROOT))

baseline_gate = _load_module("parity_baseline_gate", LAB / "baseline_gate.py")
check_target = _load_module("parity_check_target", LAB / "check_target.py")
report = _load_module("parity_report", LAB / "report.py")
runner = _load_module("parity_runner", LAB / "runner.py")

DEFAULT_TARGET = ACCEPTANCE_ROOT / "acceptance_target.local.json"
EXAMPLE_TARGET = ACCEPTANCE_ROOT / "acceptance_target.example.json"
DEFAULT_CORPUS = PARITY_ROOT / "core_local_parity_cases_v2.jsonl"
REPORTS_DIR = PARITY_ROOT / "reports"
RAW_DIR = PARITY_ROOT / "raw_responses"
BASELINE_PATH = PARITY_ROOT / "baselines" / "latest.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA Core local parity runner")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--priority", choices=("P0", "P1", "P2"))
    parser.add_argument("--case-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--accept-baseline", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--skip-target-check", action="store_true")
    args = parser.parse_args()

    target_path = args.target.resolve()
    if not target_path.is_file():
        if EXAMPLE_TARGET.is_file():
            print(f"FAIL: copy {EXAMPLE_TARGET} to {DEFAULT_TARGET}", file=sys.stderr)
        else:
            print(f"FAIL: target not found: {target_path}", file=sys.stderr)
        return 1

    try:
        from lab.target import load_target

        target = load_target(target_path)
        check_target.validate_local_target(str(target["base_url"]))
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if not args.dry_run and not args.skip_target_check:
        check = check_target.check_target_ready(target_path, timeout_seconds=args.timeout_seconds)
        if check.get("status") != "PASS":
            print("Target check: FAIL")
            for err in check.get("errors") or []:
                print(f"  - {err}")
            return 1
        print("Target check: PASS")

    payload = runner.run_parity_cases(
        target_path=target_path,
        corpus_path=args.corpus.resolve(),
        raw_dir=None if args.dry_run else RAW_DIR,
        priority=args.priority,
        case_id=args.case_id,
        limit=args.limit,
        fail_fast=args.fail_fast,
        timeout_seconds=args.timeout_seconds,
        dry_run=args.dry_run,
    )

    can_save, reason = baseline_gate.evaluate_baseline_acceptance(
        payload,
        dry_run=args.dry_run,
        accept_baseline=args.accept_baseline,
        local_target_ok=True,
    )
    if can_save:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(
            json.dumps({"run_id": payload["run_id"], "summary": payload["summary"]}, indent=2),
            encoding="utf-8",
        )
        print(f"Baseline saved: {BASELINE_PATH.relative_to(REPO_ROOT)}")
    else:
        print(f"Baseline: not saved — {reason}")

    md_path = REPORTS_DIR / f"PARITY_REPORT_{payload['run_id']}.md"
    json_path = REPORTS_DIR / f"PARITY_REPORT_{payload['run_id']}.json"
    report.write_reports(payload, md_path, json_path)

    summary = payload["summary"]
    print(f"Live status: {payload['live_status']}")
    print(
        f"Total {summary['total']} | pass {summary['pass']} fail {summary['fail']} "
        f"unasserted {summary['unasserted']} | P0 fail {summary.get('p0_fail', 0)}"
    )
    lat = summary.get("latency_ms") or {}
    print(f"Latency ms p50={lat.get('p50')} p95={lat.get('p95')} max={lat.get('max')}")
    print(f"Report: {md_path.relative_to(REPO_ROOT)}")

    for row in payload.get("results") or []:
        if row.get("status") == "FAIL":
            print(f"  FAIL {row.get('case_id')}: {row.get('reason')}")

    if summary.get("p0_fail", 0) > 0:
        return 1
    if payload["live_status"] == "FAIL":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
