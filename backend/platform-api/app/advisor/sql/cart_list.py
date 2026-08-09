"""Explicit cart sum: «посчитай: активатор, спирулина»."""

from __future__ import annotations

import re
from typing import Any

from app.advisor.sql.text import normalize_text

CART_LIST_RE = re.compile(r"^(?:посчитай|рассчитай|считай|корзина)\s*[:\-]\s*(.+)$", re.I)


def parse_cart_list_request(question: str) -> list[str] | None:
    match = CART_LIST_RE.match(question.strip())
    if not match:
        return None
    names = [part.strip() for part in re.split(r"[,;+\n]", match.group(1)) if part.strip()]
    return names[:12] or None


def build_cart_list_response(
    names: list[str],
    resolved: list[dict[str, Any]],
    missing: list[str],
) -> tuple[str, list[str]]:
    if not resolved:
        label = ", ".join(missing or names)
        return (
            f"Не узнал товары в списке: {label}. "
            "Напишите через запятую, например: посчитай: активатор, БЭМ, Ба-Гуа.",
            [],
        )
    total_retail = sum(float(row.get("retail_price_byn") or 0) for row in resolved)
    total_partner = sum(float(row.get("partner_price_byn") or 0) for row in resolved)
    total_pv = sum(float(row.get("partner_w") or row.get("partner_points") or 0) for row in resolved)
    lines = []
    for index, row in enumerate(resolved, start=1):
        lines.append(
            f"{index}. {row['canonical_name']} — "
            f"{float(row.get('retail_price_byn') or 0):.0f} BYN / "
            f"{float(row.get('partner_price_byn') or 0):.0f} BYN, "
            f"{float(row.get('partner_w') or 0):.0f} PV"
        )
    text_parts = [
        "🛒 Расчёт списка",
        "",
        *lines,
        "",
        f"Первичная/розничная: {total_retail:.0f} BYN.",
        f"Повторная/партнёрская: {total_partner:.0f} BYN.",
        f"Объём: {total_pv:.0f} PV.",
    ]
    if missing:
        text_parts.append(f"Не распознал: {', '.join(missing)}.")
    text_parts.extend(["", "Могу убрать товар, добавить другой или подобрать более выгодный вход."])
    return "\n".join(text_parts), [str(row["sku"]) for row in resolved]
