"""Normalized golden HTTP baselines (no raw answer bodies or secrets)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def normalize_result(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("case_id") or row.get("fixture_id") or row.get("flow_id"),
        "kind": row.get("kind") or ("negative" if str(row.get("status", "")).startswith("NEGATIVE") else "positive"),
        "class": row.get("class"),
        "priority": row.get("priority"),
        "status": row.get("status"),
        "reason": row.get("reason"),
        "expected_mode": row.get("expected_mode"),
        "answer_mode": row.get("answer_mode"),
        "gap_kind": row.get("gap_kind"),
        "latency_ms": row.get("latency_ms"),
    }


def build_baseline(run_payload: dict[str, Any]) -> dict[str, Any]:
    rows = [normalize_result(r) for r in run_payload.get("results") or []]
    index = {str(r["id"]): r for r in rows if r.get("id")}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_payload.get("run_id"),
        "target": run_payload.get("target"),
        "cases": index,
    }


def save_baseline(path: Path, baseline: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")


def load_baseline(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def compare_baselines(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if not previous:
        return {"status": "no_baseline", "fixed": [], "regressions": [], "changed": [], "slower": [], "new_unasserted": []}

    prev_cases = previous.get("cases") or {}
    curr_cases = current.get("cases") or {}
    fixed: list[str] = []
    regressions: list[str] = []
    changed: list[str] = []
    slower: list[str] = []
    new_unasserted: list[str] = []

    all_ids = sorted(set(prev_cases) | set(curr_cases))
    for case_id in all_ids:
        prev = prev_cases.get(case_id)
        curr = curr_cases.get(case_id)
        if not prev:
            if curr and curr.get("status") in {"UNASSERTED", "NOT_RUN_DEPENDENCY"}:
                new_unasserted.append(case_id)
            else:
                changed.append(case_id)
            continue
        if not curr:
            changed.append(case_id)
            continue
        if prev.get("status") == "FAIL" and curr.get("status") == "PASS":
            fixed.append(case_id)
        if prev.get("status") == "PASS" and curr.get("status") == "FAIL":
            regressions.append(case_id)
        if prev != curr and case_id not in fixed and case_id not in regressions:
            changed.append(case_id)
        prev_lat = prev.get("latency_ms")
        curr_lat = curr.get("latency_ms")
        if prev_lat is not None and curr_lat is not None and curr_lat > prev_lat * 1.5 and curr_lat - prev_lat > 200:
            slower.append(case_id)

    status = "PASS" if not regressions else "FAIL"
    return {
        "status": status,
        "fixed": fixed,
        "regressions": regressions,
        "changed": changed,
        "slower": slower,
        "new_unasserted": new_unasserted,
    }


def can_accept_baseline(run_payload: dict[str, Any]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    for row in run_payload.get("results") or []:
        status = str(row.get("status") or "")
        priority = str(row.get("priority") or "P1").upper()
        if status.startswith("NEGATIVE") and status != "NEGATIVE_PASS":
            errors.append(f"{row.get('fixture_id')}: {status}")
        elif status in {"FAIL", "TIMEOUT"} and priority in {"P0", "P1"}:
            errors.append(f"{row.get('case_id') or row.get('fixture_id')}: {status}")
    return not errors, errors
