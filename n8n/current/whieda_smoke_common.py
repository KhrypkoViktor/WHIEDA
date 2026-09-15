"""Shared constants for Telegram legacy smoke runners."""

from __future__ import annotations

LEGACY_WEBHOOK = "https://sysarchn8n.duckdns.org/webhook/advisor-whieda-v0"
SMOKE_CHAT_ID = 900001

CASES: list[tuple[str, str]] = [
    ("greeting", "привет"),
    ("capability", "что умеешь"),
    ("price_alias", "цена спирулина"),
    ("product_card", "активатор клеток"),
    ("clarify_price", "сколько стоит"),
    ("faq_business", "что такое pv"),
]
