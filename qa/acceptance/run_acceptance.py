#!/usr/bin/env python3
"""WHIEDA Advisor Acceptance Lab — local Core API acceptance runner."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_ROOT = ROOT / "qa" / "acceptance"
DEFAULT_TARGET = ACCEPTANCE_ROOT / "acceptance_target.local.json"
EXAMPLE_TARGET = ACCEPTANCE_ROOT / "acceptance_target.example.json"
DEFAULT_CORPUS = ROOT / "qa" / "cases" / "whieda_regression_cases_v1.jsonl"

sys.path.insert(0, str(ACCEPTANCE_ROOT))

from lab.check_target import check_target_contract  # noqa: E402
from lab.offline import run_offline_checks  # noqa: E402
from lab.baseline import build_baseline, compare_baselines, load_baseline, save_baseline  # noqa: E402
from lab.report import write_reports  # noqa: E402
from lab.runner import run_cases  # noqa: E402
from lab.target import TargetConfigError, load_target  # noqa: E402
from lab.transport import urllib_request_fn  # noqa: E402


def cmd_check_target(target_path: Path, timeout: float) -> int:
    try:
        target = load_target(target_path)
    except TargetConfigError as exc:
        print(f"FAIL: {exc}")
        return 1

    request_fn = urllib_request_fn(timeout_seconds=timeout)
    result = check_target_contract(target, request_fn)
    if result["status"] == "PASS":
        print(f"Target check: PASS ({target.get('name')})")
        print(f"  base_url: {target.get('base_url')}")
        print(f"  health: HTTP {result.get('health_status')}")
        print(f"  openapi paths: {len(result.get('openapi_paths') or [])}")
        return 0

    print("Target check: FAIL")
    for err in result.get("errors") or []:
        print(f"  - {err}")
    return 1


def cmd_offline(target_path: Path, corpus_path: Path) -> int:
    result = run_offline_checks(target_path=target_path, corpus_path=corpus_path)
    print(f"Offline check: {result['status']}")
    print(f"  cases: {result.get('case_count', 0)}")
    if result.get("target_name"):
        print(f"  target: {result['target_name']} ({result.get('target_base_url')})")
    for err in result.get("errors") or []:
        print(f"  ERROR: {err}")
    for warn in result.get("warnings") or []:
        print(f"  WARN: {warn}")
    if result["status"] == "FAIL":
        return 1
    print("Note: offline mode validates corpus/target only — not a live E2E run.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    raw_dir = ACCEPTANCE_ROOT / "raw_responses"
    reports_dir = ACCEPTANCE_ROOT / "reports"
    baselines_dir = ACCEPTANCE_ROOT / "baselines"
    payload = run_cases(
        target_path=args.target,
        corpus_path=args.corpus,
        priority=args.priority,
        case_id=args.case_id,
        limit=args.limit,
        fail_fast=args.fail_fast,
        timeout_seconds=args.timeout_seconds,
        dry_run=args.dry_run,
        raw_dir=None if args.dry_run else raw_dir,
    )
    summary = payload["summary"]
    md_path = reports_dir / f"ACCEPTANCE_REPORT_{payload['run_id']}.md"
    json_path = reports_dir / f"ACCEPTANCE_REPORT_{payload['run_id']}.json"
    write_reports(payload, md_path, json_path)

    baseline_path = baselines_dir / "latest.json"
    prev = load_baseline(baseline_path)
    current_baseline = build_baseline(payload)
    diff = compare_baselines(current_baseline, prev)
    if not args.dry_run:
        save_baseline(baseline_path, current_baseline)

    print(f"Run id: {payload['run_id']}")
    print(f"Live status: {payload['live_status']}")
    print(
        f"Total {summary['total']} | pass {summary['pass']} fail {summary['fail']} "
        f"skip {summary['skip']} unasserted {summary['unasserted']}"
    )
    print(f"Report: {md_path.relative_to(ROOT)}")
    print(f"Baseline diff: {diff.get('status')} regressions={len(diff.get('regressions') or [])}")
    if args.dry_run:
        print("Dry-run: no HTTP requests were sent.")
    return 0 if payload["live_status"] in {"PASS", "NOT_RUN"} else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA Advisor Acceptance Lab")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check-target", action="store_true")
    group.add_argument("--offline", action="store_true")
    group.add_argument("--run", action="store_true")
    parser.add_argument("--priority", choices=("P0", "P1", "P2"))
    parser.add_argument("--case-id")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    target_path = args.target.resolve()
    if not target_path.is_file():
        print(f"FAIL: target config not found: {target_path}")
        print(f"Copy {EXAMPLE_TARGET} to {DEFAULT_TARGET} and adjust if needed.")
        return 1

    if args.check_target:
        return cmd_check_target(target_path, args.timeout_seconds)
    if args.offline:
        return cmd_offline(target_path, args.corpus)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
