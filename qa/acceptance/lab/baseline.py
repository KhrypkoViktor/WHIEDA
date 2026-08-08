"""Normalized acceptance baselines (no raw responses or secrets)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def normalize_result(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": row.get("case_id"),
        "priority": row.get("priority"),
        "status": row.get("status"),
        "reason": row.get("reason"),
        "answer_mode": row.get("answer_mode"),
        "product_name": row.get("product_name"),
        "latency_ms": row.get("latency_ms"),
        "has_photo": row.get("has_photo"),
        "has_video": row.get("has_video"),
        "has_pdf": row.get("has_pdf"),
    }


def build_baseline(run_payload: dict[str, Any]) -> dict[str, Any]:
    rows = [normalize_result(r) for r in run_payload.get("results") or []]
    index = {r["case_id"]: r for r in rows if r.get("case_id")}
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
        return {"status": "no_baseline", "fixed": [], "regressions": [], "changed": [], "slower": []}

    prev_cases = previous.get("cases") or {}
    curr_cases = current.get("cases") or {}
    fixed: list[str] = []
    regressions: list[str] = []
    changed: list[str] = []
    slower: list[str] = []

    all_ids = sorted(set(prev_cases) | set(curr_cases))
    for case_id in all_ids:
        prev = prev_cases.get(case_id)
        curr = curr_cases.get(case_id)
        if not prev or not curr:
            changed.append(case_id)
            continue
        if prev.get("status") == "FAIL" and curr.get("status") == "PASS":
            fixed.append(case_id)
        if prev.get("status") == "PASS" and curr.get("status") == "FAIL":
            regressions.append(case_id)
        if prev != curr:
            if case_id not in fixed and case_id not in regressions:
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
    }
