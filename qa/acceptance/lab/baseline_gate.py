"""Explicit baseline acceptance gate."""

from __future__ import annotations

from typing import Any


def evaluate_baseline_acceptance(
    payload: dict[str, Any],
    *,
    dry_run: bool,
    accept_baseline: bool,
) -> tuple[bool, str]:
    if not accept_baseline:
        return False, "baseline not saved: --accept-baseline not set"
    if dry_run:
        return False, "baseline not saved: dry-run rejects --accept-baseline"
    if payload.get("live_status") != "PASS":
        return False, f"baseline not saved: live_status is {payload.get('live_status')}, expected PASS"
    summary = payload.get("summary") or {}
    if summary.get("fail", 0) > 0:
        return False, "baseline not saved: run contains FAIL results"
    if summary.get("unasserted", 0) > 0:
        return False, "baseline not saved: run contains UNASSERTED results"
    return True, "baseline saved: explicit --accept-baseline on clean PASS run"


def baseline_exit_code(*, accept_baseline: bool, can_save: bool, live_status: str) -> int:
    if accept_baseline and not can_save:
        return 1
    if live_status == "FAIL":
        return 1
    return 0
