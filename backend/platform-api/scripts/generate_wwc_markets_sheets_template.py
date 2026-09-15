#!/usr/bin/env python3
"""Generate WWC markets Google Sheets import template from site catalog (products.js)."""

from __future__ import annotations

import csv
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PRODUCTS_JS = ROOT / "03_Website" / "wwc-best" / "src" / "data" / "products.js"
OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "wwc_markets_sheets_template"

MARKETS_HEADER = [
    "market_id",
    "country_iso",
    "country_name",
    "currency_code",
    "price_visibility",
    "is_active",
    "is_default",
]
REF_STRUCTURES_HEADER = ["ref_code", "structure_id", "is_active"]
SERVICE_CENTERS_HEADER = [
    "center_id",
    "structure_id",
    "country_iso",
    "city",
    "region",
    "title",
    "manager_name",
    "photo_url",
    "telegram",
    "phone",
    "address",
    "working_hours",
    "map_url_yandex",
    "map_url_google",
    "notes",
    "is_active",
    "priority",
]
COVERAGE_HEADER = [
    "structure_id",
    "country_iso",
    "city_alias",
    "center_id",
    "is_active",
    "priority",
]
PRICES_HEADER = [
    "sku",
    "market_id",
    "currency_code",
    "amount",
    "formatted",
    "price_state",
    "is_active",
    "updated_at",
]


def _parse_products(text: str) -> list[dict[str, float | str]]:
    """Extract sku + retailRub + retailByn from products.js (no JS runtime)."""
    products: dict[str, dict[str, float | str]] = {}

    def add(sku: str, rub: float, byn: float) -> None:
        sku = sku.strip()
        if not sku or sku == "E028-00":
            return
        products[sku] = {"sku": sku, "retailRub": rub, "retailByn": byn}

    def parse_item_fields(blob: str) -> tuple[str, float, float] | None:
        sku_m = re.search(r"sku:\s*'([^']+)'", blob)
        rub_m = re.search(r"retailRub:\s*([\d.]+)", blob)
        byn_m = re.search(r"retailByn:\s*([\d.]+)", blob)
        if sku_m and rub_m and byn_m:
            return sku_m.group(1), float(rub_m.group(1)), float(byn_m.group(1))
        return None

    item_re = re.compile(r"\{\s*sku:\s*'[^']+'.*?\}", re.DOTALL)
    for match in item_re.finditer(text):
        parsed = parse_item_fields(match.group(0))
        if parsed:
            add(*parsed)

    variant_block_re = re.compile(r"variants:\s*\[(.*?)\]", re.DOTALL)
    variant_item_re = re.compile(r"\{[^{}]*?\}", re.DOTALL)
    for block in variant_block_re.finditer(text):
        for vm in variant_item_re.finditer(block.group(1)):
            parsed = parse_item_fields(vm.group(0))
            if parsed:
                add(*parsed)

    return [products[k] for k in sorted(products.keys())]


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        writer.writerows(rows)


def _try_write_xlsx(csv_dir: Path, xlsx_path: Path) -> bool:
    try:
        from openpyxl import Workbook
    except ImportError:
        return False

    wb = Workbook()
    default = wb.active
    wb.remove(default)

    tab_files = [
        ("markets", "markets.csv"),
        ("ref_structures", "ref_structures.csv"),
        ("service_centers", "service_centers.csv"),
        ("service_center_coverage", "service_center_coverage.csv"),
        ("product_prices", "product_prices.csv"),
    ]
    for tab_name, filename in tab_files:
        ws = wb.create_sheet(title=tab_name)
        with (csv_dir / filename).open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.reader(fh):
                ws.append(row)

    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx_path)
    return True


def main() -> int:
    if not PRODUCTS_JS.is_file():
        raise SystemExit(f"Missing catalog source: {PRODUCTS_JS}")

    text = PRODUCTS_JS.read_text(encoding="utf-8")
    catalog = _parse_products(text)
    if not catalog:
        raise SystemExit("No products parsed from products.js")

    today = date.today().isoformat()

    markets_rows = [
        ["ru", "RU", "Россия", "RUB", "full", "true", "false"],
        ["by", "BY", "Беларусь", "BYN", "full", "true", "false"],
        ["global", "*", "Другая страна", "RUB", "full", "true", "true"],
    ]

    price_rows: list[list] = []
    for item in catalog:
        sku = str(item["sku"])
        rub = item["retailRub"]
        byn = item["retailByn"]
        price_rows.append([sku, "ru", "RUB", rub, "", "active", "true", today])
        price_rows.append([sku, "by", "BYN", byn, "", "active", "true", today])

    _write_csv(OUT_DIR / "markets.csv", MARKETS_HEADER, markets_rows)
    _write_csv(OUT_DIR / "ref_structures.csv", REF_STRUCTURES_HEADER, [])
    _write_csv(OUT_DIR / "service_centers.csv", SERVICE_CENTERS_HEADER, [])
    _write_csv(OUT_DIR / "service_center_coverage.csv", COVERAGE_HEADER, [])
    _write_csv(OUT_DIR / "product_prices.csv", PRICES_HEADER, price_rows)

    xlsx_path = OUT_DIR / "WWC_MARKETS_SHEETS_TEMPLATE.xlsx"
    if _try_write_xlsx(OUT_DIR, xlsx_path):
        print(f"OK: {xlsx_path} ({len(catalog)} SKUs, {len(price_rows)} price rows)")
    else:
        print(
            f"OK: CSV set in {OUT_DIR} ({len(catalog)} SKUs). "
            "Install openpyxl to also emit .xlsx: pip install openpyxl"
        )
    print(f"Source: {PRODUCTS_JS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
