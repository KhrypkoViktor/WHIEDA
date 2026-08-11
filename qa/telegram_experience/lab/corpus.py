"""Telegram experience flow corpus helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CATEGORIES = (
    "presentation",
    "ordering",
    "structured_routes",
    "safe_gaps",
    "data_gaps",
)

REQUIRED_TURN_KEYS = (
    "turn",
    "input",
    "expected_mode",
    "must_contain",
    "must_not_contain",
    "expected_media",
    "max_latency_ms",
)


def load_flows(path: Path) -> list[dict[str, Any]]:
    flows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        flows.append(json.loads(line))
    return flows


def validate_flows(
    flows: list[dict[str, Any]],
    *,
    min_flows: int = 12,
    min_turns: int = 22,
) -> list[str]:
    errors: list[str] = []
    if len(flows) < min_flows:
        errors.append(f"expected at least {min_flows} flows, got {len(flows)}")
    turns = sum(len(f.get("turns") or []) for f in flows)
    if turns < min_turns:
        errors.append(f"expected at least {min_turns} turns, got {turns}")
    seen: set[str] = set()
    for flow in flows:
        flow_id = str(flow.get("flow_id") or "")
        if not flow_id:
            errors.append("flow missing flow_id")
            continue
        if flow_id in seen:
            errors.append(f"duplicate flow_id {flow_id}")
        seen.add(flow_id)
        category = str(flow.get("category") or "")
        if category not in CATEGORIES:
            errors.append(f"{flow_id}: invalid category {category!r}")
        if not flow.get("turns"):
            errors.append(f"{flow_id}: no turns")
        for turn in flow.get("turns") or []:
            for key in REQUIRED_TURN_KEYS:
                if key not in turn:
                    errors.append(f"{flow_id} turn {turn.get('turn')}: missing {key}")
    return errors


def flow_stats(flows: list[dict[str, Any]]) -> dict[str, Any]:
    by_category: dict[str, int] = {cat: 0 for cat in CATEGORIES}
    for flow in flows:
        cat = str(flow.get("category") or "")
        if cat in by_category:
            by_category[cat] += 1
    turns = sum(len(f.get("turns") or []) for f in flows)
    return {"flows": len(flows), "turns": turns, "by_category": by_category}
