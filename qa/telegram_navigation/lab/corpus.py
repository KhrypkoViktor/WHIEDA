"""Telegram navigation acceptance corpus helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CATEGORIES = (
    "menu",
    "catalog_browse",
    "callback_routing",
    "free_text",
    "photo_first",
)

REQUIRED_FLOW_KEYS = ("flow_id", "category", "name", "checks")


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
    min_flows: int = 20,
) -> list[str]:
    errors: list[str] = []
    if len(flows) < min_flows:
        errors.append(f"expected at least {min_flows} flows, got {len(flows)}")
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
        for key in REQUIRED_FLOW_KEYS:
            if key not in flow:
                errors.append(f"{flow_id}: missing {key}")
        checks = flow.get("checks") or []
        if not checks:
            errors.append(f"{flow_id}: no checks")
    return errors


def flow_stats(flows: list[dict[str, Any]]) -> dict[str, Any]:
    by_category: dict[str, int] = {cat: 0 for cat in CATEGORIES}
    for flow in flows:
        cat = str(flow.get("category") or "")
        if cat in by_category:
            by_category[cat] += 1
    return {"flows": len(flows), "by_category": by_category}
