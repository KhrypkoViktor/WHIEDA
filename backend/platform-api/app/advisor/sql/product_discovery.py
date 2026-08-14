"""Read-only product-discovery map for short and generic product wording."""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.advisor.sql.text import normalize_text

MAP_PATH = Path(__file__).with_name("data") / "product_discovery_map_v1.tsv"
CHOICE_STATUSES = frozenset({"generic_category", "needs_owner_review"})
# These phrases are already specific enough to open a card.  Other one-word
# discovery phrases intentionally ask a short question first: that is friendlier
# for a newcomer than silently choosing a product from a category.
DIRECT_SAFE_PHRASES = frozenset(
    {
        "бэм",
        "сауна",
        "цинфэн",
        "наколенники",
        "шейная накладка",
        "зубная паста",
        "красный эликсир",
        "зеленый эликсир",
        "синий эликсир",
        "активатор про",
    }
)
# Common one-token slips should enter the same deliberate choice path as their
# clean counterpart, before alias scoring has a chance to pick a random SKU.
DISCOVERY_INPUT_ALIASES = {
    "пасту": "паста",
    "поис": "пояс",
    "актив": "активатор",
    "активatr": "активатор",
}


def normalize_discovery_phrase(value: str) -> str:
    normalized = normalize_text(value).replace("ё", "е")
    return DISCOVERY_INPUT_ALIASES.get(normalized, normalized)


@lru_cache(maxsize=1)
def load_discovery_map() -> dict[str, list[dict[str, str]]]:
    if not MAP_PATH.is_file():
        return {}
    grouped: dict[str, list[dict[str, str]]] = {}
    with MAP_PATH.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            phrase = normalize_discovery_phrase(str(row.get("normalized_phrase") or row.get("phrase") or ""))
            if phrase:
                grouped.setdefault(phrase, []).append({str(key): str(value or "") for key, value in row.items()})
    for rows in grouped.values():
        rows.sort(key=lambda row: int(row.get("candidate_rank") or 999))
    return grouped


def lookup_discovery_candidates(value: str) -> list[dict[str, str]]:
    return list(load_discovery_map().get(normalize_discovery_phrase(value), []))


def should_show_choices(phrase: str, rows: list[dict[str, str]]) -> bool:
    normalized = normalize_discovery_phrase(phrase)
    if not rows or normalized in DIRECT_SAFE_PHRASES:
        return False
    if any(str(row.get("status") or "") in CHOICE_STATUSES for row in rows):
        return True
    # A single short word such as "очки" or "чай" is often a category in a
    # human message, even when today's catalog happens to contain one match.
    return " " not in normalized


async def build_discovery_choice_response(
    conn: Any,
    tenant_id: str,
    phrase: str,
    *,
    repo: Any,
    trace_id: str,
    fmt: Any,
) -> dict[str, Any] | None:
    """Build a compact choice prompt from verified, current runtime SKUs."""
    rows = lookup_discovery_candidates(phrase)
    if not should_show_choices(phrase, rows):
        return None

    products: list[dict[str, Any]] = []
    for row in rows[:3]:
        sku = str(row.get("sku") or "").strip()
        if not sku:
            continue
        product = await repo.resolve_product_by_sku(conn, tenant_id, sku)
        if product and all(str(product.get("sku")) != str(item.get("sku")) for item in products):
            products.append(product)

    if not products:
        # The approved map deliberately keeps some broad consumer categories
        # unbound to a SKU.  Give the person a useful next turn instead of
        # exposing a catalog miss.
        if any(str(row.get("status") or "") == "generic_category" for row in rows):
            text = (
                "Подскажите, что именно интересует: капсулы, чай, кофе, "
                "косметика или прибор? Тогда покажу подходящие варианты."
            )
            return fmt.ok_response(
                text,
                "clarification",
                trace_id,
                clarifications=["product_discovery_category"],
                context={"discovery_phrase": normalize_discovery_phrase(phrase)},
                media=fmt.empty_media(),
            )
        return None

    names = [str(product.get("canonical_name") or product.get("sku") or "") for product in products]
    lines = "\n".join(f"• {name}" for name in names if name)
    text = (
        "Уточню, чтобы не ошибиться с товаром. Выберите подходящий вариант:\n\n"
        f"{lines}\n\n"
        "Напишите название варианта, который хотите посмотреть."
    )
    return fmt.ok_response(
        text,
        "clarification",
        trace_id,
        product={"skus": [str(product["sku"]) for product in products]},
        clarifications=["product_discovery_choices"],
        context={"discovery_phrase": normalize_discovery_phrase(phrase)},
        media=fmt.empty_media(),
    )
