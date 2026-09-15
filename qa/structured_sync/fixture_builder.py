#!/usr/bin/env python3
"""Generate minimal TSV fixtures that satisfy the P0 circuit breaker."""

from __future__ import annotations


def tsv(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["\t".join(headers)]
    lines.extend("\t".join(row) for row in rows)
    return "\n".join(lines) + "\n"


def products_fixture(count: int = 20) -> str:
    rows = [
        [f"SKU-{index:03d}", f"Product {index}", "cat", "100", "10", "", "80", "8", "", "5"]
        for index in range(1, count + 1)
    ]
    return tsv(["sku", "canonical_name", "category", "retail_price_rub", "retail_w", "retail_price_byn", "partner_price_rub", "partner_w", "partner_price_byn", "partner_points"], rows)


def aliases_fixture(count: int = 60) -> str:
    rows = [[f"alias-{index}", f"SKU-{(index % 20) + 1:03d}", f"Product {(index % 20) + 1}", "contains", "100", "true"] for index in range(1, count + 1)]
    return tsv(["alias", "canonical_sku", "canonical_name", "match_type", "priority", "active"], rows)


def resources_fixture(count: int = 25) -> str:
    rows = [
        [f"res-{index}", f"SKU-{(index % 20) + 1:03d}", f"Product {(index % 20) + 1}", "", "pdf", f"Title {index}", f"https://example.com/r/{index}", "owner", "ru", "100", "true"]
        for index in range(1, count + 1)
    ]
    return tsv(["resource_id", "sku", "canonical_name", "alias", "resource_type", "title", "url", "source_owner", "language", "priority", "active"], rows)


def product_cards_fixture(count: int = 10) -> str:
    rows = [[f"SKU-{index:03d}", f"Product {index}", f"Short {index}", "cat", "what", "who", "cases", "how", "expect", "contra"] for index in range(1, count + 1)]
    return tsv(["sku", "canonical_name", "short_name", "category", "what_it_is", "who_asks_about_it", "common_use_cases", "how_to_use_short", "what_to_expect_soft", "contraindications_short"], rows)


def empty_headers_only() -> str:
    return "sku\tcanonical_name\ncategory\n"
