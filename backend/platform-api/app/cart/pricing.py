"""Server-side cart pricing from advisor structured products. Never coerce missing to 0."""

from __future__ import annotations

from typing import Any

from app.markets.constants import normalize_market_id


def currency_for_market(market_id: str) -> str:
    return "BYN" if normalize_market_id(market_id) == "by" else "RUB"


def money_value(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None
    return amount


def pv_value(product: dict[str, Any]) -> float:
    for key in ("partner_w", "partner_points"):
        raw = product.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return 0.0


def unit_amount_for(product: dict[str, Any], *, market_id: str, price_mode: str) -> float | None:
    market = normalize_market_id(market_id)
    mode = "repeat" if price_mode == "repeat" else "primary"
    if market == "by":
        field = "retail_price_byn" if mode == "primary" else "partner_price_byn"
    else:
        field = "retail_price_rub" if mode == "primary" else "partner_price_rub"
    return money_value(product.get(field))


def build_public_cart(
    record: dict[str, Any],
    products: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    market_id = normalize_market_id(record.get("market_id"))
    price_mode = "repeat" if record.get("price_mode") == "repeat" else "primary"
    currency = currency_for_market(market_id)
    items: list[dict[str, Any]] = []
    total_amount = 0.0
    total_pv = 0.0
    unavailable = 0
    for row in record.get("items") or []:
        sku = str(row.get("sku") or "")
        qty = int(row.get("qty") or 0)
        product = products.get(sku) or {}
        unit = unit_amount_for(product, market_id=market_id, price_mode=price_mode)
        unit_pv = pv_value(product)
        available = unit is not None
        if not available:
            unavailable += 1
        line_amount = None if unit is None else unit * qty
        line_pv = unit_pv * qty
        if line_amount is not None:
            total_amount += line_amount
            total_pv += line_pv
        items.append(
            {
                "sku": sku,
                "canonical_name": product.get("canonical_name") or sku,
                "qty": qty,
                "unit_amount": unit,
                "line_amount": line_amount,
                "unit_pv": unit_pv,
                "line_pv": line_pv,
                "price_state": "active" if available else "unavailable",
                "currency_code": currency,
            }
        )
    return {
        "ok": True,
        "cart_session_id": record["cart_session_id"],
        "market_id": market_id,
        "currency_code": currency,
        "price_mode": price_mode,
        "first_ref": record.get("first_ref") or "",
        "ref": record.get("ref") or "",
        "items": items,
        "totals": {
            "amount": total_amount,
            "pv": total_pv,
            "currency_code": currency,
            "unavailable_count": unavailable,
        },
    }


def format_checkout_comment(public_cart: dict[str, Any]) -> str:
    mode_label = "повторная" if public_cart["price_mode"] == "repeat" else "первичная"
    currency = public_cart["currency_code"]
    lines = [f"Корзина ({mode_label}, {currency})", ""]
    for index, item in enumerate(public_cart["items"], start=1):
        amount = item["line_amount"]
        amount_text = f"{amount:.0f} {currency}" if amount is not None else "цена не указана"
        lines.append(
            f"{index}. {item['canonical_name']} × {item['qty']} — {amount_text}, "
            f"{item['line_pv']:.0f} PV"
        )
    totals = public_cart["totals"]
    lines.extend(
        [
            "",
            f"Итого: {totals['amount']:.0f} {currency}, {totals['pv']:.0f} PV",
        ]
    )
    return "\n".join(lines)
