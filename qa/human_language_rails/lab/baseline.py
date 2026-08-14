"""HLR HTTP baseline guards."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def normalize_result(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": row.get("case_id"),
        "flow_id": row.get("flow_id"),
        "expected_rail": row.get("expected_rail"),
        "expected_mode": row.get("expected_mode"),
        "status": row.get("status"),
        "reason": row.get("reason"),
        "answer_mode": row.get("answer_mode"),
        "gap_kind": row.get("gap_kind"),
        "latency_ms": row.get("latency_ms"),
    }


def build_baseline(run_payload: dict[str, Any]) -> dict[str, Any]:
    rows = [
        normalize_result(r)
        for r in run_payload.get("results") or []
        if r.get("kind") == "assertion" and r.get("acceptance_status") == "accepted"
    ]
    index = {str(r["case_id"]): r for r in rows if r.get("case_id")}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_payload.get("run_id"),
        "target": run_payload.get("target"),
        "cases": index,
    }


def save_baseline(path: Path, baseline: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")


def can_accept_baseline(run_payload: dict[str, Any]) -> tuple[bool, list[str]]:
    if run_payload.get("dry_run"):
        return False, ["dry_run cannot update baseline"]
    errors: list[str] = []
    accepted_rows = [
        r
        for r in run_payload.get("results") or []
        if r.get("kind") == "assertion" and r.get("acceptance_status") == "accepted"
    ]
    if not accepted_rows:
        errors.append("no accepted assertion rows executed")
    for row in accepted_rows:
        status = str(row.get("status") or "")
        if status in {"FAIL", "TIMEOUT", "NOT_RUN_SETUP", "NOT_RUN_TIMEOUT"}:
            errors.append(f"{row.get('case_id')}: {status}")
    summary = run_payload.get("summary") or {}
    if summary.get("fail"):
        errors.append("summary reports failures")
    return not errors, errors
