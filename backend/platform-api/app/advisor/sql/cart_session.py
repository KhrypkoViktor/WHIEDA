"""Session-scoped active cart for Telegram calculate/remove flows."""

from __future__ import annotations

import re
from typing import Any

from app.advisor.sql.cart_list import build_cart_list_response

CART_REMOVE_RE = re.compile(r"^(?:убери|удали|убрать|исключи)\s+(.+)$", re.I)

NO_ACTIVE_CART_TEXT = (
    "Сейчас нет активной корзины. "
    "Посчитайте список одной строкой, например: Посчитай: активатор, БЭМ, Ба-Гуа."
)


def parse_cart_remove_request(question: str) -> str | None:
    match = CART_REMOVE_RE.match(question.strip())
    if not match:
        return None
    name = match.group(1).strip(" .?!")
    return name or None


def cart_items_from_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for row in products:
        items.append(
            {
                "sku": row.get("sku"),
                "canonical_name": row.get("canonical_name"),
                "retail_price_byn": float(row.get("retail_price_byn") or 0),
                "partner_price_byn": float(row.get("partner_price_byn") or 0),
                "partner_w": float(row.get("partner_w") or row.get("partner_points") or 0),
            }
        )
    return items


def build_cart_remove_response(
    remove_name: str,
    active_cart: list[dict[str, Any]],
    *,
    resolved_product: dict[str, Any] | None,
) -> tuple[str, list[dict[str, Any]], str | None]:
    if not active_cart:
        return NO_ACTIVE_CART_TEXT, [], "no_active_cart"
    if not resolved_product:
        return (
            f"Не нашёл «{remove_name}» в активной корзине. "
            "Напишите точное название товара из списка или пересчитайте корзину заново.",
            active_cart,
            "cart_item_not_found",
        )
    target_sku = str(resolved_product["sku"])
    remaining = [item for item in active_cart if str(item.get("sku")) != target_sku]
    if len(remaining) == len(active_cart):
        label = resolved_product.get("canonical_name") or remove_name
        return (
            f"«{label}» нет в текущей корзине. "
            "Могу показать состав корзины или пересчитать список заново.",
            active_cart,
            "cart_item_not_in_cart",
        )
    if not remaining:
        return (
            "Корзина очищена — все позиции убраны. "
            "Напишите новый список, например: Посчитай: активатор, спирулина.",
            [],
            None,
        )
    names = [str(item.get("canonical_name") or item.get("sku") or "") for item in remaining]
    text, _skus = build_cart_list_response(names, remaining, [])
    return text, remaining, None
