"""Lint and load multi-turn conversation reliability flows."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROHIBITED_FRAGMENTS = (
    "не знаю",
    "нет в базе",
    "передам на проверку",
    "this needs human review",
    "needs human review",
    "не смог обработать",
)

REQUIRED_TURN_KEYS = (
    "turn",
    "input",
    "expected_mode",
    "must_contain",
    "must_not_contain",
    "expected_media",
)


def load_flows(path: Path) -> list[dict[str, Any]]:
    flows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        flows.append(json.loads(line))
    return flows


def validate_flows(flows: list[dict[str, Any]], *, min_flows: int = 24, min_turns: int = 80) -> list[str]:
    errors: list[str] = []
    if len(flows) < min_flows:
        errors.append(f"corpus has {len(flows)} flows, need at least {min_flows}")
    total_turns = sum(len(flow.get("turns") or []) for flow in flows)
    if total_turns < min_turns:
        errors.append(f"corpus has {total_turns} turns, need at least {min_turns}")

    seen_flow_ids: set[str] = set()
    for flow in flows:
        flow_id = str(flow.get("flow_id") or "")
        if not flow_id:
            errors.append("flow missing flow_id")
            continue
        if flow_id in seen_flow_ids:
            errors.append(f"duplicate flow_id {flow_id}")
        seen_flow_ids.add(flow_id)
        if not flow.get("session"):
            errors.append(f"{flow_id}: missing session")
        if not flow.get("priority"):
            errors.append(f"{flow_id}: missing priority")
        turns = flow.get("turns") or []
        if not turns:
            errors.append(f"{flow_id}: no turns")
        terminal = flow.get("terminal_context")
        if terminal is None and turns:
            errors.append(f"{flow_id}: missing terminal_context")
        for turn in turns:
            for key in REQUIRED_TURN_KEYS:
                if key not in turn:
                    errors.append(f"{flow_id} turn {turn.get('turn')}: missing {key}")
            must_not = [str(x).lower() for x in (turn.get("must_not_contain") or [])]
            if "traceback" not in must_not:
                errors.append(f"{flow_id} turn {turn.get('turn')}: must_not_contain missing Traceback guard")
    return errors


def flow_stats(flows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "flows": len(flows),
        "turns": sum(len(f.get("turns") or []) for f in flows),
        "p0_flows": sum(1 for f in flows if f.get("priority") == "P0"),
    }
