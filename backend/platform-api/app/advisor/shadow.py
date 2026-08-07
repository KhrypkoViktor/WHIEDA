from __future__ import annotations

from typing import Any

from app.observability import log_event

COMPARE_KEYS = ("ok", "answer_mode", "route")


def compare_advisor_responses(core: dict[str, Any], legacy: dict[str, Any]) -> dict[str, Any]:
    core_product = core.get("product") or {}
    legacy_product = legacy.get("product") or {}
    return {
        "ok_match": core.get("ok") == legacy.get("ok"),
        "answer_mode_match": core.get("answer_mode") == legacy.get("answer_mode"),
        "route_match": core.get("route") == legacy.get("route"),
        "sku_match": (core_product.get("sku") or None) == (legacy_product.get("sku") or None),
        "core_answer_mode": core.get("answer_mode"),
        "legacy_answer_mode": legacy.get("answer_mode"),
        "core_route": core.get("route"),
        "legacy_route": legacy.get("route"),
        "legacy_ok": legacy.get("ok"),
        "core_ok": core.get("ok"),
    }


def log_shadow_comparison(
    *,
    trace_id: str,
    tenant_id: str,
    comparison: dict[str, Any],
    question_len: int,
) -> None:
    mismatches = [key for key in COMPARE_KEYS if not comparison.get(f"{key}_match", True)]
    log_event(
        "advisor_shadow_comparison",
        trace_id=trace_id,
        tenant_id=tenant_id,
        question_len=question_len,
        mismatch_fields=mismatches,
        **comparison,
    )
