"""Offline contract checks mapped from navigation acceptance corpus."""

from __future__ import annotations

from typing import Any

from app.advisor.sql.text import detect_service_intent
from app.telegram.navigation import (
    ACTION_LABELS,
    LABEL_PRODUCTS,
    MENU_LABELS,
    build_catalog_page_callback,
    build_product_action_callback,
    catalog_list_inline_keyboard,
    clamp_page_size,
    parse_callback_data,
    product_action_question,
    resolve_menu_text_intent,
)


def _check(name: str) -> bool:
    if name == "menu_labels_count":
        return len(MENU_LABELS) == 6
    if name == "menu_labels_stable":
        return MENU_LABELS == (
            "📦 Товары",
            "🧮 Калькулятор",
            "📈 Бизнес",
            "🏢 О компании",
            "🧭 Подбор",
            "📅 Встречи",
        )
    if name == "menu_products_intent":
        return resolve_menu_text_intent(LABEL_PRODUCTS) == "nav_products"
    if name == "menu_calculator_intent":
        return resolve_menu_text_intent("🧮 Калькулятор") == "nav_calculator"
    if name == "menu_business_intent":
        return resolve_menu_text_intent("📈 Бизнес") == "nav_business"
    if name == "menu_company_intent":
        return resolve_menu_text_intent("🏢 О компании") == "nav_company"
    if name == "menu_basket_intent":
        return resolve_menu_text_intent("🧭 Подбор") == "nav_basket"
    if name == "menu_events_intent":
        return resolve_menu_text_intent("📅 Встречи") == "nav_events"
    if name == "catalog_page_one":
        parsed = parse_callback_data(build_catalog_page_callback(1))
        return parsed is not None and parsed.kind == "catalog_page" and parsed.page == 1
    if name == "catalog_max_eight":
        return clamp_page_size(99) == 8 and clamp_page_size(0) == 1
    if name == "catalog_first_no_back":
        kb = catalog_list_inline_keyboard(page=1, total_pages=3, products=[{"sku": "A", "canonical_name": "A"}])
        row_texts = [btn["text"] for row in kb["inline_keyboard"] for btn in row]
        return "◀ Назад" not in row_texts and "Далее ▶" in row_texts
    if name == "catalog_last_no_next":
        kb = catalog_list_inline_keyboard(page=3, total_pages=3, products=[{"sku": "A", "canonical_name": "A"}])
        row_texts = [btn["text"] for row in kb["inline_keyboard"] for btn in row]
        return "Далее ▶" not in row_texts and "◀ Назад" in row_texts
    if name == "catalog_sku_actions":
        kb = __import__("app.telegram.navigation", fromlist=["product_actions_inline_keyboard"]).product_actions_inline_keyboard("LOCAL-ACT")
        labels = [btn["text"] for row in kb["inline_keyboard"] for btn in row]
        return all(label in labels for label in ACTION_LABELS.values())
    if name == "callback_page_valid":
        return parse_callback_data("cat:p:2") is not None
    if name == "callback_sku_valid":
        return parse_callback_data("cat:s:LOCAL-ACT") is not None
    if name == "callback_action_valid":
        data = build_product_action_callback("card", "LOCAL-ACT")
        parsed = parse_callback_data(data)
        return parsed is not None and parsed.kind == "product_action"
    if name == "callback_invalid_safe":
        return parse_callback_data("evil;drop table") is None
    if name == "callback_oversized_safe":
        return parse_callback_data("cat:s:" + ("X" * 80)) is None
    if name == "callback_no_sql_injection":
        parsed = parse_callback_data("cat:s:'; delete from advisor_structured_products; --")
        return parsed is None
    if name == "free_text_products":
        return resolve_menu_text_intent("📦 Товары") == "nav_products"
    if name == "free_text_catalog_phrase":
        return (
            resolve_menu_text_intent("какие есть товары") == "nav_products"
            and detect_service_intent("какие есть товары") is None
        )
    if name == "free_text_greeting":
        return detect_service_intent("хай") == "greeting"
    if name == "free_text_capabilities":
        return detect_service_intent("что можешь") == "capabilities"
    if name == "photo_first_card_action":
        question = product_action_question("card", "Активатор клеток")
        return question == "карточка Активатор клеток"
    return False


def run_offline_checks(flows: list[dict[str, Any]]) -> dict[str, Any]:
    category_stats: dict[str, dict[str, int]] = {}
    failures: list[str] = []
    for flow in flows:
        cat = str(flow.get("category") or "unknown")
        category_stats.setdefault(cat, {"flows": 0, "passed": 0})
        category_stats[cat]["flows"] += 1
        flow_id = str(flow.get("flow_id") or "")
        ok = all(_check(str(name)) for name in (flow.get("checks") or []))
        if ok:
            category_stats[cat]["passed"] += 1
        else:
            failures.append(flow_id)
    passed = len(flows) - len(failures)
    return {
        "status": "PASS" if not failures else "FAIL",
        "flows_total": len(flows),
        "flows_passed": passed,
        "flows_failed": len(failures),
        "failed_flow_ids": failures,
        "category_stats": category_stats,
        "summary_line": f"Telegram navigation offline: {passed}/{len(flows)} flows passed",
    }
