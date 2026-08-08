"""Orchestration for acceptance --run (testable)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from lab.baseline import build_baseline, compare_baselines, load_baseline, save_baseline
from lab.baseline_gate import baseline_exit_code, evaluate_baseline_acceptance
from lab.report import write_reports
from lab.run_gate import build_target_blocked_payload, check_target_ready
from lab.runner import run_cases


def execute_run(
    *,
    target_path: Path,
    corpus_path: Path,
    reports_dir: Path,
    baselines_dir: Path,
    raw_dir: Path,
    dry_run: bool,
    accept_baseline: bool,
    priority: str | None = None,
    case_id: str | None = None,
    limit: int | None = None,
    fail_fast: bool = False,
    timeout_seconds: float = 30.0,
    transport: Any | None = None,
    check_target_fn: Callable[..., dict[str, Any]] | None = None,
    run_cases_fn: Callable[..., dict[str, Any]] | None = None,
) -> tuple[int, dict[str, Any]]:
    checker = check_target_fn or check_target_ready
    runner = run_cases_fn or run_cases

    if not dry_run:
        check = checker(target_path, timeout_seconds=timeout_seconds)
        if check.get("status") != "PASS":
            payload = build_target_blocked_payload(
                target_path=target_path,
                corpus_path=corpus_path,
                check_result=check,
            )
            payload["baseline"] = {
                "saved": False,
                "reason": "baseline not saved: target check failed before live run",
                "accepted": accept_baseline,
            }
            md_path = reports_dir / f"ACCEPTANCE_REPORT_{payload['run_id']}.md"
            json_path = reports_dir / f"ACCEPTANCE_REPORT_{payload['run_id']}.json"
            write_reports(payload, md_path, json_path)
            payload["report_paths"] = {"md": str(md_path), "json": str(json_path)}
            return 1, payload

    payload = runner(
        target_path=target_path,
        corpus_path=corpus_path,
        transport=transport,
        priority=priority,
        case_id=case_id,
        limit=limit,
        fail_fast=fail_fast,
        timeout_seconds=timeout_seconds,
        dry_run=dry_run,
        raw_dir=None if dry_run else raw_dir,
    )

    can_save, reason = evaluate_baseline_acceptance(payload, dry_run=dry_run, accept_baseline=accept_baseline)
    baseline_path = baselines_dir / "latest.json"
    prev = load_baseline(baseline_path)
    current_baseline = build_baseline(payload)
    diff = compare_baselines(current_baseline, prev)

    if can_save:
        save_baseline(baseline_path, current_baseline)

    payload["baseline"] = {
        "saved": can_save,
        "reason": reason,
        "accepted": accept_baseline,
        "diff_status": diff.get("status"),
        "regressions": diff.get("regressions") or [],
    }

    md_path = reports_dir / f"ACCEPTANCE_REPORT_{payload['run_id']}.md"
    json_path = reports_dir / f"ACCEPTANCE_REPORT_{payload['run_id']}.json"
    write_reports(payload, md_path, json_path)
    payload["report_paths"] = {"md": str(md_path), "json": str(json_path)}

    exit_code = baseline_exit_code(
        accept_baseline=accept_baseline,
        can_save=can_save,
        live_status=str(payload.get("live_status")),
    )
    return exit_code, payload
