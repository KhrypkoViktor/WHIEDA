"""Execution surface contract for Golden corpus cases and flows."""

from __future__ import annotations

import re
from typing import Any

EXECUTION_SURFACES = frozenset(
    {
        "advisor_http",
        "telegram_text",
        "telegram_callback",
        "telegram_delivery",
    }
)

TELEGRAM_TEXT_LABELS = (
    "📦 товары",
    "📈 бизнес",
    "🏢 о компании",
    "🧮 калькулятор",
    "🧭 подбор",
    "📅 встречи",
)

_TELEGRAM_TEXT_LABELS_CF = {label.casefold() for label in TELEGRAM_TEXT_LABELS}

_CALLBACK_PREFIXES = ("nav:", "cat:", "act:")


def infer_execution_surface(case: dict[str, Any]) -> str:
    inp = case.get("input") or {}
    text = str(inp.get("user_text") or "").strip()
    text_cf = text.casefold()

    callback = str(inp.get("callback_data") or inp.get("callback_query") or "").strip()
    if callback.lower().startswith(_CALLBACK_PREFIXES):
        return "telegram_callback"
    if text_cf.startswith(_CALLBACK_PREFIXES):
        return "telegram_callback"

    source = case.get("source") or {}
    ref = str(source.get("ref") or "").strip()
    ref_cf = ref.casefold()

    if text_cf in _TELEGRAM_TEXT_LABELS_CF or ref_cf in _TELEGRAM_TEXT_LABELS_CF:
        return "telegram_text"

    if source.get("kind") == "telegram_navigation":
        return "telegram_callback"

    expected = case.get("expected") or {}
    if str(expected.get("mode") or "") == "navigation_catalog":
        return "telegram_callback"

    if source.get("kind") == "contract" and ref_cf in _TELEGRAM_TEXT_LABELS_CF:
        return "telegram_text"

    if str(source.get("category") or "") in {"photo_first", "telegram_delivery"}:
        return "telegram_delivery"
    if "photo_first" in str(case.get("notes") or "").casefold():
        return "telegram_delivery"
    if re.search(r"reply_markup|callback ack|photo-first", str(case.get("notes") or ""), re.I):
        return "telegram_delivery"

    ref_upper = ref.upper()
    if ref_upper.startswith("TG-SVC-CATALOG") or ref_upper.startswith("TG-PRES-CATALOG"):
        return "telegram_text"

    return "advisor_http"


def apply_execution_surface(case: dict[str, Any]) -> dict[str, Any]:
    updated = dict(case)
    updated["execution_surface"] = infer_execution_surface(case)
    return updated


def apply_flow_surfaces(flow: dict[str, Any]) -> dict[str, Any]:
    turns = [apply_execution_surface(turn) for turn in flow.get("turns") or []]
    surfaces = {turn.get("execution_surface") for turn in turns}
    flow_surface = turns[0].get("execution_surface") if len(surfaces) == 1 and turns else "advisor_http"
    if len(surfaces) > 1:
        flow_surface = "advisor_http" if "advisor_http" in surfaces else sorted(surfaces)[0]
    return {
        **flow,
        "execution_surface": flow_surface,
        "turns": turns,
    }


def validate_execution_surface(value: str) -> bool:
    return str(value or "") in EXECUTION_SURFACES


def skip_reason_for_http(surface: str) -> str:
    return f"execution_surface={surface} is not runnable via /v1/advisor/query HTTP lab"
