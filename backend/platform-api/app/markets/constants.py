from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

ALLOWED_MARKET_IDS = frozenset({"ru", "by", "global"})
DEFAULT_STRUCTURE_ID = "wwc-default"
FALLBACK_NO_REGISTRY = (
    "В вашем городе сервисный центр пока не указан. "
    "Подскажем ближайший районный центр и формат консультации."
)


def normalize_ref(value: str | None) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower())


def normalize_city(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().casefold())


def normalize_market_id(value: str | None) -> str:
    market = (value or "").strip().lower()
    if market in ALLOWED_MARKET_IDS:
        return market
    return "global"


def structure_display_name(structure_id: str) -> str:
    if structure_id == DEFAULT_STRUCTURE_ID:
        return "WWC Default"
    return structure_id.replace("-", " ").replace("_", " ").title()


def format_price_amount(amount: Decimal | float | int, currency_code: str) -> str:
    value = Decimal(str(amount))
    if currency_code == "BYN":
        formatted = f"{value:,.2f}".replace(",", " ").replace(".", ",")
        return f"{formatted} BYN"
    formatted = f"{value:,.0f}".replace(",", " ")
    return f"{formatted} ₽"


def public_center_row(row: dict[str, Any]) -> dict[str, Any]:
    """Strip internal fields; expose only public center contract."""
    keys = (
        "center_id", "structure_id", "country_iso", "city", "region", "title",
        "manager_name", "photo_url", "telegram", "phone", "address",
        "working_hours", "map_url_yandex", "map_url_google", "notes",
        "is_active", "priority",
    )
    out = {key: row.get(key) for key in keys}
    if out.get("telegram"):
        out["telegram"] = str(out["telegram"]).lstrip("@")
    return out
