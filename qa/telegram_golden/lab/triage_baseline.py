"""Triage baseline acceptance gate."""

from __future__ import annotations

from typing import Any


def can_accept_triage_baseline(
    triage_payload: dict[str, Any],
    *,
    http_results: list[dict[str, Any]] | None = None,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    backlog = triage_payload.get("core_bug_backlog") or []
    for item in backlog:
        if str(item.get("priority") or "").upper() == "P0":
            errors.append(f"P0 core_bug remains: {item.get('id')}")

    negative = (triage_payload.get("summary") or {}).get("negative_safety_gate") or {}
    if int(negative.get("fail") or 0) > 0:
        errors.append("negative safety fixtures failed")

    rows = triage_payload.get("rows") or []
    for row in rows:
        if row.get("status") != "SKIP_SURFACE":
            continue
        if row.get("classification") != "surface_mismatch" and row.get("execution_surface") == "advisor_http":
            errors.append(f"skip without surface_mismatch: {row.get('id')}")

    for row in rows:
        if row.get("classification") == "policy_decision_required" and row.get("status") == "PASS":
            errors.append(f"pending policy cannot be PASS: {row.get('id')}")

    if http_results:
        for row in http_results:
            if row.get("status") in {"FAIL", "TIMEOUT", "NEGATIVE_FAIL"}:
                row_id = row.get("case_id") or row.get("fixture_id")
                if row_id and not any(r.get("id") == row_id and r.get("classification") for r in rows):
                    errors.append(f"unclassified failure: {row_id}")

    return (len(errors) == 0, errors)


def build_triage_baseline(triage_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": triage_payload.get("run_id"),
        "by_classification": (triage_payload.get("summary") or {}).get("by_classification"),
        "core_bug_ids": [item.get("id") for item in triage_payload.get("core_bug_backlog") or []],
        "pending_policy_ids": [item.get("policy_id") for item in triage_payload.get("pending_owner_decisions") or []],
    }
