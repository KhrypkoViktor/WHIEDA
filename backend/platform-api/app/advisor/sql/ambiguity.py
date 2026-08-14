"""Product ambiguity clarifications — ported from legacy structured lookup."""

from __future__ import annotations

import re
from typing import Any

from app.advisor.sql.text import normalize_text

GENERIC_SHORT_ALIASES = frozenset({"активатор", "пептид", "пептиды", "соевые пептиды"})

DESCRIPTION_INTENT_RE = re.compile(
    r"(подробн|расскаж|что\s+это|что\s+такое|что\s+за|что\s+делает|дай\s+инфо)",
    re.I,
)
EXPLICIT_PRODUCT_ASK_RE = re.compile(
    r"^(расскажи|подскажи|что\s+это|что\s+такое|что\s+за|подробнее|дай\s+инфо|дай\s+информац|что\s+делает)\b",
    re.I,
)
ACTIVATOR_LIKE_RE = re.compile(r"\b(?:активатор|ативатор)\b", re.I)
SOY_PEPTIDE_RE = re.compile(r"(^|\s)пептид(?:ы)?($|\s)|^соевые?\s+пептиды?$", re.I)


def is_explicit_product_ask(question: str) -> bool:
    return bool(EXPLICIT_PRODUCT_ASK_RE.search(str(question or "").strip()))


def has_description_intent(question: str) -> bool:
    return bool(DESCRIPTION_INTENT_RE.search(question))


def is_activator_like(question: str) -> bool:
    return bool(ACTIVATOR_LIKE_RE.search(question))


def is_soy_peptide_like(question: str) -> bool:
    normalized = normalize_text(question)
    return bool(SOY_PEPTIDE_RE.search(normalized))


def is_ambiguous_short_alias(question: str, best_alias: str) -> bool:
    normalized = normalize_text(question)
    alias = normalize_text(best_alias)
    if not normalized or not alias:
        return False
    return normalized == alias and alias in GENERIC_SHORT_ALIASES


def weak_color_or_belt_clarification(question: str) -> dict[str, Any] | None:
    value = normalize_text(question)
    if not value:
        return None
    tokens = value.split()
    has_stem = lambda stem: any(token.startswith(stem) for token in tokens)
    has_following_descriptor = lambda stem: any(
        token.startswith(stem)
        and re.match(r"^(эликсир|банка|коробка|этикетка)", tokens[index + 1] if index + 1 < len(tokens) else "")
        for index, token in enumerate(tokens)
    )

    if has_stem("пояс"):
        if "пояса" in tokens:
            return {
                "direct": True,
                "sku": "T003",
                "canonical_name": "Магнитный пояс",
                "clarification_key": None,
            }
        direct = any(
            (token.startswith("магнитн") or token.startswith("турмалин"))
            and index + 1 < len(tokens)
            and tokens[index + 1].startswith("пояс")
            for index, token in enumerate(tokens)
        )
        return {
            "direct": direct,
            "sku": "T003",
            "canonical_name": "Магнитный пояс",
            "clarification_key": "product_ambiguity_belt",
            "fallback": "Вы про Магнитный пояс? Нужна цена, описание или применение?",
        }

    color_choices = [
        ("красн", "F001-02", "Эликсир Фохоу", "красный эликсир Фохоу"),
        ("зелен", "F003-02", "Эликсир Саньцин", "зелёный эликсир Саньцин"),
        ("син", "F002-02", "Эликсир 3 Драгоценности", "синий эликсир «3 Драгоценности»"),
    ]
    for stem, sku, canonical_name, label in color_choices:
        if not has_stem(stem):
            continue
        return {
            "direct": has_following_descriptor(stem),
            "sku": sku,
            "canonical_name": canonical_name,
            "clarification_key": f"product_ambiguity_color_{stem}",
            "fallback": f"Уточню: вы про {label}? Нужна цена, описание или применение?",
        }
    return None


async def try_ambiguity_clarification(
    conn,
    tenant_id: str,
    question: str,
    *,
    best_product: dict[str, Any] | None,
    repo,
) -> tuple[str, str, list[str]] | None:
    """Return (text, mode, clarification_keys) or None to continue normal routing."""
    normalized = normalize_text(question)

    if normalized == "паста":
        text = await repo.load_clarification_prompt(conn, tenant_id, "product_ambiguity_paste")
        return (
            text or "Вы про зубную пасту с экстрактом полыни или Пасту Цинфэн?",
            "clarification",
            ["product_ambiguity_paste"],
        )

    weak = weak_color_or_belt_clarification(question)
    if weak and not weak.get("direct"):
        key = str(weak.get("clarification_key") or "product_ambiguity_general")
        text = await repo.load_clarification_prompt(conn, tenant_id, key)
        return (text or str(weak.get("fallback") or ""), "clarification", [key])

    activator_exact = normalized == "активатор"
    if activator_exact:
        if tenant_id != "whieda" and best_product:
            return None
        text = await repo.load_clarification_prompt(conn, tenant_id, "product_ambiguity_activator")
        return (
            text or "Вы про Активатор клеток или Активатор клеток PRO?",
            "clarification",
            ["product_ambiguity_activator"],
        )

    if is_activator_like(question) and not best_product and (
        has_description_intent(question) or is_explicit_product_ask(question)
    ):
        text = await repo.load_clarification_prompt(conn, tenant_id, "product_ambiguity_activator")
        return (
            text or "Вы про Активатор клеток или Активатор клеток PRO?",
            "clarification",
            ["product_ambiguity_activator"],
        )

    if is_soy_peptide_like(question) and (
        not best_product
        or str(best_product.get("sku") or "") == "F038-00"
        and normalized in {"пептид", "пептиды", "соевые пептиды"}
    ):
        return (
            "Соевый пептид WHIEDA знаю. Это наш продукт для ежедневной нутрицевтической поддержки. "
            "Что вам сейчас важнее: коротко что это, цена, фото, видео или рассказать подробнее?",
            "clarification",
            ["product_ambiguity_peptide"],
        )

    if best_product and is_ambiguous_short_alias(
        question, str(best_product.get("alias") or best_product.get("canonical_name") or "")
    ):
        text = await repo.load_clarification_prompt(conn, tenant_id, "product_ambiguity_activator")
        return (
            text or "Вы про Активатор клеток или Активатор клеток PRO?",
            "clarification",
            ["product_ambiguity_activator"],
        )

    if weak and weak.get("direct"):
        return None

    return None
