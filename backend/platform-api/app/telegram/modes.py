"""Single source of truth: answer_mode values Core may deliver to Telegram."""

from __future__ import annotations

# Modes the SQL engine can emit that must reach the user on CORE_ROUTE_TELEGRAM=core.
TELEGRAM_DELIVERABLE_MODES: frozenset[str] = frozenset(
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


def is_telegram_deliverable(answer_mode: str | None) -> bool:
    return bool(answer_mode) and answer_mode in TELEGRAM_DELIVERABLE_MODES
