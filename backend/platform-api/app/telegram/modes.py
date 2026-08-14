"""Telegram answer delivery rules for Core processor."""

from __future__ import annotations

from typing import Any

# Structured modes that may include photo-first media delivery.
TELEGRAM_STRUCTURED_MODES: frozenset[str] = frozenset(
    {
        "structured_price",
        "structured_card",
        "structured_photo",
        "structured_video",
        "structured_certificate",
        "structured_product_detail",
        "structured_comparison",
        "structured_comparison_layer",
        "structured_business",
        "structured_business_faq",
        "structured_business_objection",
        "structured_promotion",
        "structured_event",
        "structured_community",
        "structured_starter_basket",
        "structured_cart",
        "clarification",
    }
)

# Internal / routing-only modes — never push to Telegram chat.
TELEGRAM_INTERNAL_MODES: frozenset[str] = frozenset(
    {
        "fallback",
        "error",
    }
)


def should_deliver_telegram_response(core_response: dict[str, Any] | None) -> bool:
    """Deliver when there is user-visible text and mode is not internal."""
    if not core_response:
        return False
    text = str(core_response.get("answer_text") or "").strip()
    if not text:
        return False
    mode = str(core_response.get("answer_mode") or "").strip()
    if mode in TELEGRAM_INTERNAL_MODES:
        return False
    return True


def is_telegram_deliverable(answer_mode: str | None) -> bool:
    """Backward-compatible alias for structured-mode checks (shadow logging)."""
    return bool(answer_mode) and answer_mode in TELEGRAM_STRUCTURED_MODES
