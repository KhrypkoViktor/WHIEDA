#!/usr/bin/env python3
"""Build Telegram navigation offline acceptance corpus."""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "whieda_telegram_navigation_flows_v1.jsonl"


def _flow(flow_id: str, category: str, name: str, checks: list[str]) -> dict:
    return {
        "flow_id": flow_id,
        "category": category,
        "name": name,
        "checks": checks,
    }


def build_flows() -> list[dict]:
    flows: list[dict] = []
    flows.append(_flow("TG-NAV-MENU-6", "menu", "Six menu labels", ["menu_labels_count"]))
    flows.append(_flow("TG-NAV-MENU-STABLE", "menu", "Stable menu labels", ["menu_labels_stable"]))
    flows.append(_flow("TG-NAV-MENU-PRODUCTS", "menu", "Products label intent", ["menu_products_intent"]))
    flows.append(_flow("TG-NAV-MENU-CALC", "menu", "Calculator label intent", ["menu_calculator_intent"]))
    flows.append(_flow("TG-NAV-MENU-BIZ", "menu", "Business label intent", ["menu_business_intent"]))
    flows.append(_flow("TG-NAV-MENU-CO", "menu", "Company label intent", ["menu_company_intent"]))
    flows.append(_flow("TG-NAV-MENU-BASKET", "menu", "Basket label intent", ["menu_basket_intent"]))
    flows.append(_flow("TG-NAV-MENU-EVENTS", "menu", "Events label intent", ["menu_events_intent"]))
    flows.append(_flow("TG-NAV-CAT-PAGE1", "catalog_browse", "First catalog page", ["catalog_page_one"]))
    flows.append(_flow("TG-NAV-CAT-MAX8", "catalog_browse", "Max eight products", ["catalog_max_eight"]))
    flows.append(_flow("TG-NAV-CAT-NOBACK-P1", "catalog_browse", "Page one no back", ["catalog_first_no_back"]))
    flows.append(_flow("TG-NAV-CAT-NONEXT-LAST", "catalog_browse", "Last page no next", ["catalog_last_no_next"]))
    flows.append(_flow("TG-NAV-CAT-SKU-BUTTONS", "catalog_browse", "SKU action buttons", ["catalog_sku_actions"]))
    flows.append(_flow("TG-NAV-CB-VALID-PAGE", "callback_routing", "Valid page callback", ["callback_page_valid"]))
    flows.append(_flow("TG-NAV-CB-VALID-SKU", "callback_routing", "Valid SKU callback", ["callback_sku_valid"]))
    flows.append(_flow("TG-NAV-CB-VALID-ACT", "callback_routing", "Valid action callback", ["callback_action_valid"]))
    flows.append(_flow("TG-NAV-CB-INVALID", "callback_routing", "Invalid callback safe", ["callback_invalid_safe"]))
    flows.append(_flow("TG-NAV-CB-OVER64", "callback_routing", "Oversized callback safe", ["callback_oversized_safe"]))
    flows.append(_flow("TG-NAV-CB-SQLINJ", "callback_routing", "No SQL injection path", ["callback_no_sql_injection"]))
    flows.append(_flow("TG-NAV-FT-PRODUCTS", "free_text", "Products label text", ["free_text_products"]))
    flows.append(_flow("TG-NAV-FT-CATALOG-ASK", "free_text", "Catalog list phrase", ["free_text_catalog_phrase"]))
    flows.append(_flow("TG-NAV-FT-GREET", "free_text", "Greeting unchanged", ["free_text_greeting"]))
    flows.append(_flow("TG-NAV-FT-CAP", "free_text", "Capabilities unchanged", ["free_text_capabilities"]))
    flows.append(_flow("TG-NAV-PF-CARD", "photo_first", "Card action photo-first", ["photo_first_card_action"]))
    return flows


def main() -> int:
    flows = build_flows()
    lines = [json.dumps(flow, ensure_ascii=False) for flow in flows]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {len(flows)} flows to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
