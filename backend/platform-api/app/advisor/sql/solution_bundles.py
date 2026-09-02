"""Runtime matching and presentation for structured solution bundles."""

from __future__ import annotations

import re
from typing import Any

from app.advisor.sql import formatters as fmt
from app.advisor.sql.text import normalize_text


def may_match_solution_bundle(question: str) -> bool:
    """The active set is small; aliases in the master sheet decide the match."""

    return bool(_words(question))


def _words(value: str) -> list[str]:
    return [word for word in normalize_text(value).split() if len(word) >= 3]


def _word_matches(query_word: str, alias_word: str) -> bool:
    if query_word == alias_word:
        return True
    stem = min(5, len(query_word), len(alias_word))
    return stem >= 4 and query_word[:stem] == alias_word[:stem]


def _alias_score(question: str, alias: str) -> int:
    normalized_alias = normalize_text(alias)
    if not normalized_alias:
        return 0
    normalized_question = normalize_text(question)
    if normalized_alias in normalized_question:
        return 1000 + len(normalized_alias)

    query_words = _words(normalized_question)
    alias_words = _words(normalized_alias)
    if not alias_words:
        return 0
    if all(any(_word_matches(query, expected) for query in query_words) for expected in alias_words):
        return 100 + sum(len(word) for word in alias_words)
    return 0


def match_solution_bundle(question: str, bundles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the strongest matching active bundle, never a weak generic match."""

    best: tuple[int, dict[str, Any]] | None = None
    for bundle in bundles:
        if bundle.get("active") is False:
            continue
        aliases = str(bundle.get("aliases") or "").replace("\n", ";").split(";")
        score = max((_alias_score(question, alias) for alias in aliases), default=0)
        if score and (not best or score > best[0]):
            best = (score, bundle)
    return best[1] if best else None


# Live Core prefers a product card when the query is only a product name.
# Scenario words let an already-matched bundle win. Do not add bare «стельки».
_BUNDLE_CONTEXT_RE = re.compile(
    r"(?:совместим|сочет|в\s+один\s+день|не\s+потеет|после\s+|"
    r"протокол|систем[аы]|комплекс|набор|укреплен|поддержк|"
    r"плоскостоп|вальгус|\bшпор|носить\s+стельк|стельки\s+при|"
    r"стельки\s+можно|стельки\s+размер|стельки\s+и\s+давлен)",
    re.I,
)


def has_specific_bundle_context(question: str) -> bool:
    return bool(_BUNDLE_CONTEXT_RE.search(normalize_text(question)))


def bundle_sku_groups(bundle: dict[str, Any]) -> list[list[str]]:
    raw = str(bundle.get("sku_groups") or bundle.get("product_sku_groups") or "").strip()
    if not raw:
        return []
    return [
        [sku.strip() for sku in group.split("|") if sku.strip()]
        for group in raw.split(";")
        if group.strip()
    ]


def ordered_bundle_skus(bundle: dict[str, Any]) -> list[str]:
    return [sku for group in bundle_sku_groups(bundle) for sku in group]


def choose_bundle_products(bundle: dict[str, Any], products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_sku = {str(product.get("sku") or ""): product for product in products}
    selected: list[dict[str, Any]] = []
    for alternatives in bundle_sku_groups(bundle):
        product = next((by_sku[sku] for sku in alternatives if sku in by_sku), None)
        if product:
            selected.append(product)
    return selected


def format_solution_bundle(
    bundle: dict[str, Any], products: list[dict[str, Any]], country: str
) -> str:
    title = str(bundle.get("bundle_name") or "Подходящий набор").strip()
    goal = str(bundle.get("desired_outcome") or bundle.get("positioning") or "").strip()
    lines = [f"Под вашу задачу подойдёт набор «{title}»."]
    if goal:
        lines.extend(["", goal])
    if products:
        lines.extend(["", "Что входит:"])
        for product in products:
            lines.append(f"• {product.get('canonical_name') or product.get('sku')} — {fmt.format_price(product, country)}")
    else:
        lines.extend(["", "Состав набора сейчас обновляется. Могу показать каталог или подобрать товар отдельно."])
    lines.extend(["", "Могу открыть карточку, фото или цену любого товара из набора."])
    return "\n".join(lines)
