"""Baseline acceptance rules for parity runner."""

from __future__ import annotations

from typing import Any


def evaluate_baseline_acceptance(
    payload: dict[str, Any],
    *,
    dry_run: bool,
    accept_baseline: bool,
    local_target_ok: bool,
) -> tuple[bool, str]:
    if dry_run:
        return False, "baseline not saved: dry-run"
    if not accept_baseline:
        return False, "baseline not saved: pass --accept-baseline to save"
    if not local_target_ok:
        return False, "baseline not saved: target is not localhost:8080"
    summary = payload.get("summary") or {}
    if summary.get("fail", 0) != 0 or summary.get("p0_fail", 0) != 0:
        return False, "baseline not saved: failures present"
    if summary.get("unasserted", 0) != 0:
        return False, "baseline not saved: unasserted cases remain"
    return True, "all cases PASS with zero unasserted on local target"
