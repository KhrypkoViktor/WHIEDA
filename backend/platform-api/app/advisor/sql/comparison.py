"""Product comparison answers — DB layer + card-based fallback like legacy."""

from __future__ import annotations

import re
from typing import Any

from app.advisor.sql import formatters as fmt


def _sentence(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def build_compare_answer(
    left_product: dict[str, Any],
    left_card: dict[str, Any] | None,
    right_product: dict[str, Any],
    right_card: dict[str, Any] | None,
    *,
    country: str = "BY",
) -> str:
    left_name = str(
        (left_card or {}).get("short_name")
        or (left_card or {}).get("canonical_name")
        or left_product.get("canonical_name")
        or "Первый товар"
    ).strip()
    right_name = str(
        (right_card or {}).get("short_name")
        or (right_card or {}).get("canonical_name")
        or right_product.get("canonical_name")
        or "Второй товар"
    ).strip()

    left_what = _sentence((left_card or {}).get("what_it_is"))
    right_what = _sentence((right_card or {}).get("what_it_is"))
    left_for_whom = _sentence((left_card or {}).get("who_asks_about_it"))
    right_for_whom = _sentence((right_card or {}).get("who_asks_about_it"))
    left_why = _sentence((left_card or {}).get("what_to_expect_soft"))
    right_why = _sentence((right_card or {}).get("what_to_expect_soft"))

    parts = [f"{left_name} и {right_name}"]

    if left_what or right_what:
        parts.append(
            "\n".join(
                [
                    f"{left_name}: {left_what or 'данные уточняются.'}",
                    f"{right_name}: {right_what or 'данные уточняются.'}",
                ]
            )
        )

    if left_for_whom or right_for_whom:
        parts.append(
            "\n".join(
                [
                    f"Кому обычно ближе {left_name}: {left_for_whom or 'по ситуации.'}",
                    f"Кому обычно ближе {right_name}: {right_for_whom or 'по ситуации.'}",
                ]
            )
        )

    if left_why or right_why:
        parts.append(
            "\n".join(
                [
                    f"По ощущению {left_name}: {left_why or 'смотреть по задаче.'}",
                    f"По ощущению {right_name}: {right_why or 'смотреть по задаче.'}",
                ]
            )
        )

    left_price = fmt.format_price(left_product, country)
    right_price = fmt.format_price(right_product, country)
    parts.append("\n".join(["По цене:", f"{left_name}: {left_price}", f"{right_name}: {right_price}"]))

    parts.append(
        "Если скажете, для какой задачи сравниваете, подскажу, с чего логичнее начать."
    )
    return "\n\n".join(parts)
