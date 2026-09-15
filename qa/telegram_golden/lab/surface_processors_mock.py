"""Minimal mock processors for Telegram surface tests (no app imports)."""

from __future__ import annotations

from typing import Any


def process_telegram_text_label(user_text: str) -> dict[str, Any]:
    label = str(user_text or "").strip().casefold()
    routes = {
        "📦 товары": {"surface": "telegram_text", "action": "open_catalog"},
        "📈 бизнес": {"surface": "telegram_text", "action": "open_business_menu"},
        "🏢 о компании": {"surface": "telegram_text", "action": "open_company_menu"},
        "🧮 калькулятор": {"surface": "telegram_text", "action": "open_calculator"},
        "🧭 подбор": {"surface": "telegram_text", "action": "open_picker"},
        "📅 встречи": {"surface": "telegram_text", "action": "open_events"},
    }
    for key, payload in routes.items():
        if label == key.casefold():
            return payload
    return {"surface": "telegram_text", "action": "unknown_label"}


def process_telegram_callback(callback_data: str) -> dict[str, Any]:
    data = str(callback_data or "").strip()
    if data.startswith(("nav:", "cat:", "act:")):
        return {"surface": "telegram_callback", "callback_data": data, "ack": True}
    return {"surface": "telegram_callback", "callback_data": data, "ack": False}


def process_telegram_delivery(*, photo_first: bool, reply_markup: bool) -> dict[str, Any]:
    return {
        "surface": "telegram_delivery",
        "photo_first": photo_first,
        "reply_markup": reply_markup,
    }
