"""Starter basket builder for Core SQL advisor."""

from __future__ import annotations

import re
from typing import Any

BUDGET_RE = re.compile(
    r"(?:на|до|бюджет(?:ом)?\s*)?\s*(\d{2,5}(?:[ .,]\d+)?)\s*(?:byn|бел(?:орусских)?\s*руб|руб|₽)?",
    re.I,
)
PV_RE = re.compile(r"(?:на|до|нужно|хочу)?\s*(\d{1,4})\s*(?:pv|пв)\b", re.I)
GOAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("demo", re.compile(r"демонстрац|показать|пробовать", re.I)),
    ("gift", re.compile(r"подар(?:ок|к)|дарить", re.I)),
    ("resale", re.compile(r"продаж|перепродаж|заработ", re.I)),
    ("personal", re.compile(r"себ[яе]|личн(?:ого|ое|ый)|домой", re.I)),
    ("pv", re.compile(r"\b(?:pv|пв)\b", re.I)),
]


def parse_basket_goal(question: str, stored: dict[str, Any]) -> str:
    if stored.get("basket_goal"):
        return str(stored["basket_goal"])
    for goal, pattern in GOAL_PATTERNS:
        if pattern.search(question):
            return goal
    return "balanced"


def parse_budget_request(question: str, stored: dict[str, Any]) -> dict[str, Any] | None:
    text = question.strip()
    lowered = text.lower()
    goal = parse_basket_goal(text, stored)
    budget_match = BUDGET_RE.search(text)
    pv_match = PV_RE.search(text)
    if budget_match:
        amount = float(str(budget_match.group(1)).replace(" ", "").replace(",", "."))
        return {"budget_byn": amount, "target_pv": None, "goal": goal}
    if pv_match:
        return {"budget_byn": None, "target_pv": float(pv_match.group(1)), "goal": goal}
    if stored.get("starter_basket") and re.match(r"^(?:а\s*)?на\s+(\d{2,5})\s*$", lowered):
        return {
            "budget_byn": float(re.search(r"(\d{2,5})", lowered).group(1)),
            "target_pv": None,
            "goal": goal,
        }
    return None


def _goal_score(rule: dict[str, Any], goal: str) -> float:
    if goal == "demo":
        return float(rule.get("demo_score") or 0)
    if goal == "gift":
        return float(rule.get("gift_score") or 0)
    if goal == "resale":
        return float(rule.get("resale_score") or 0)
    if goal == "personal":
        return float(rule.get("personal_use_score") or 0)
    if goal == "pv":
        return float(rule.get("business_priority") or 0)
    return (
        float(rule.get("universality_score") or 0) + float(rule.get("personal_use_score") or 0)
    ) / 2


def score_item(product: dict[str, Any], rule: dict[str, Any], goal: str) -> float:
    primary = float(product.get("retail_price_byn") or 0)
    repeat = float(product.get("partner_price_byn") or 0)
    future_discount_ratio = max(0, (primary - repeat) / primary) if primary > 0 and repeat > 0 else 0.5
    registration_price_score = (1 - future_discount_ratio) * 10
    goal_score = _goal_score(rule, goal)
    return (
        goal_score * 0.35
        + float(rule.get("universality_score") or 0) * 0.15
        + float(rule.get("business_priority") or 0) * 0.15
        + float(rule.get("demo_score") or 0) * 0.10
        + float(rule.get("gift_score") or 0) * 0.05
        + float(rule.get("resale_score") or 0) * 0.05
        + registration_price_score * 0.10
        + float(rule.get("popularity_score") or 0) * 0.05
    )


def _pick_template(templates: list[dict[str, Any]], goal: str) -> dict[str, Any] | None:
    for row in templates:
        if str(row.get("goal") or "") == goal:
            return row
    for row in templates:
        if str(row.get("goal") or "") == "balanced":
            return row
    return templates[0] if templates else None


def build_starter_basket(
    catalog: list[dict[str, Any]],
    *,
    budget_byn: float | None,
    target_pv: float | None,
    goal: str = "balanced",
    templates: list[dict[str, Any]] | None = None,
) -> tuple[str, list[str]]:
    if not catalog:
        return (
            "Пока нет активных правил для стартовой корзины. Могу подсказать цену конкретного товара.",
            [],
        )

    template = _pick_template(templates or [], goal)
    preferred = {
        part.strip()
        for part in str((template or {}).get("preferred_product_ids") or "").split("|")
        if part.strip()
    }
    excluded = {
        part.strip()
        for part in str((template or {}).get("excluded_product_ids") or "").split("|")
        if part.strip()
    }

    ranked = sorted(
        (
            {
                **row,
                "price": float(row.get("retail_price_byn") or 0),
                "pv": float(row.get("partner_points") or row.get("partner_w") or 0),
                "score": score_item(row, row, goal)
                + (2.0 if str(row.get("sku")) in preferred else 0.0),
            }
            for row in catalog
            if str(row.get("sku")) not in excluded and float(row.get("retail_price_byn") or 0) > 0
        ),
        key=lambda row: (row["score"], -row["price"]),
        reverse=True,
    )
    if not ranked:
        return ("Не нашёл подходящих товаров для корзины.", [])

    if not budget_byn and not target_pv:
        return (
            "Соберу стартовую корзину. На какой бюджет в BYN или какой PV ориентируемся?",
            [],
        )

    selected: list[dict[str, Any]] = []
    total = 0.0
    total_pv = 0.0
    for row in ranked:
        price = row["price"]
        if budget_byn and total + price > budget_byn:
            continue
        selected.append(row)
        total += price
        total_pv += row["pv"]
        if len(selected) >= 3:
            break
        if target_pv and total_pv >= target_pv:
            break

    if not selected:
        cheapest = min(ranked, key=lambda row: row["price"])
        return (
            f"В заданный бюджет готовый вариант пока не помещается. "
            f"Самый доступный: {cheapest['canonical_name']} — {cheapest['price']:.0f} BYN, {cheapest['pv']:.0f} PV.",
            [str(cheapest["sku"])],
        )

    heading = template.get("title") if template and goal != "balanced" else "Стартовая корзина WHIEDA"
    lines = []
    for index, row in enumerate(selected, start=1):
        reason = str(row.get("reason_short") or "Подходит под выбранную цель.").strip()
        lines.append(
            f"{index}. {row['canonical_name']} — {row['price']:.0f} BYN, {row['pv']:.0f} PV\n{reason}"
        )
    text = (
        f"🛒 {heading}\n\n"
        + "\n\n".join(lines)
        + f"\n\nИтого: {total:.0f} BYN, {total_pv:.0f} PV.\n"
        "Могу пересобрать под другой бюджет, PV или цель."
    )
    return text, [str(row["sku"]) for row in selected]
