"""Template generator produces markets + full catalog prices."""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_DIR = ROOT / "backend" / "platform-api" / "data" / "wwc_markets_sheets_template"


def test_template_markets_three_rows():
    with (TEMPLATE_DIR / "markets.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 3
    assert {r["market_id"] for r in rows} == {"ru", "by", "global"}


def test_template_product_prices_cover_catalog():
    with (TEMPLATE_DIR / "product_prices.csv").open(encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    skus = {r["sku"] for r in rows}
    assert "M015-00" in skus
    assert "D013-06" in skus
    assert "D014" in skus
    assert len(skus) >= 35
    ru = [r for r in rows if r["market_id"] == "ru"]
    by = [r for r in rows if r["market_id"] == "by"]
    assert len(ru) == len(by)
    assert all(r["currency_code"] == "RUB" for r in ru)
    assert all(r["currency_code"] == "BYN" for r in by)


def test_template_registry_tabs_empty_except_headers():
    for name in ("ref_structures", "service_centers", "service_center_coverage"):
        with (TEMPLATE_DIR / f"{name}.csv").open(encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        assert len(rows) == 1  # header only


def test_xlsx_template_exists():
    assert (TEMPLATE_DIR / "WWC_MARKETS_SHEETS_TEMPLATE.xlsx").is_file()
