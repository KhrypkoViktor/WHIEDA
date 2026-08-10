"""Turn-level assertions for conversation reliability flows."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

PARITY_ASSERT = Path(__file__).resolve().parents[2] / "parity" / "lab" / "assertions.py"
spec = importlib.util.spec_from_file_location("parity_assertions", PARITY_ASSERT)
assert spec and spec.loader
parity_assertions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity_assertions)

PROHIBITED = (
    "не знаю",
    "нет в базе",
    "передам на проверку",
    "this needs human review",
    "needs human review",
    "не смог обработать",
)


def evaluate_turn(
    *,
    flow: dict[str, Any],
    turn: dict[str, Any],
    http_status: int,
    extracted: dict[str, Any],
    latency_ms: float | None,
) -> dict[str, Any]:
    case = {
        **turn,
        "session": flow.get("session"),
        "country": flow.get("country") or "BY",
        "must_not_contain": list(turn.get("must_not_contain") or []) + list(PROHIBITED) + ["Traceback"],
    }
    status, reason = parity_assertions.evaluate_parity_case(
        case=case,
        http_status=http_status,
        latency_ms=latency_ms,
        extracted=extracted,
    )

    expected_ctx = turn.get("expected_context") or {}
    if status == "PASS" and expected_ctx:
        ctx = extracted.get("context") or {}
        for key, expected in expected_ctx.items():
            actual = ctx.get(key)
            if expected is None:
                if actual not in (None, ""):
                    status, reason = "FAIL", f"context.{key} expected null got {actual!r}"
            elif str(expected).lower() not in str(actual or "").lower():
                status, reason = "FAIL", f"context.{key} expected {expected!r} got {actual!r}"

    expected_gap = turn.get("expected_gap_kind")
    if expected_gap and extracted.get("gap_kind") != expected_gap:
        status, reason = "FAIL", f"expected_gap_kind {expected_gap!r} got {extracted.get('gap_kind')!r}"

    return {
        "flow_id": flow.get("flow_id"),
        "turn": turn.get("turn"),
        "priority": flow.get("priority"),
        "status": status,
        "reason": reason,
        "answer_mode": extracted.get("answer_mode"),
        "latency_ms": latency_ms,
    }
