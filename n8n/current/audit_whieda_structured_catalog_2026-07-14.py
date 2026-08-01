"""Read-only audit of the WHIEDA structured master and public media links."""
import csv
import io
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import requests

SHEET_ID = "1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4"
TABS = {
    "products": "1035748906",
    "aliases": "2001001",
    "resources": "2001005",
    "cards": "2001006",
    "details": "2001008",
    "comparisons": "1415928637",
}
OUT_PATH = Path(__file__).resolve().parents[1] / "live-exports" / date.today().isoformat() / "WHIEDA_structured_catalog_audit.json"


def read_tab(gid: str) -> list[dict]:
    url = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=tsv&gid={gid}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return [
        {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}
        for row in csv.DictReader(io.StringIO(response.text), delimiter="\t")
        if any(str(value or "").strip() for value in row.values())
    ]


def active(value: str) -> bool:
    return str(value or "").strip().lower() not in {"false", "0", "no", "нет"}


def url_status(url: str) -> dict:
    try:
        response = requests.head(url, allow_redirects=True, timeout=8)
        if response.status_code in {405, 403}:
            response = requests.get(url, allow_redirects=True, timeout=10, stream=True)
        return {"status": response.status_code, "final_url": response.url, "ok": response.status_code < 400}
    except requests.RequestException as error:
        return {"status": None, "final_url": None, "ok": False, "error": str(error)}


def main() -> None:
    rows = {name: read_tab(gid) for name, gid in TABS.items()}
    products = {row.get("sku"): row for row in rows["products"] if row.get("sku")}
    cards = {row.get("sku"): row for row in rows["cards"] if row.get("sku")}
    resources = [row for row in rows["resources"] if active(row.get("active", "true"))]

    images_by_sku = defaultdict(list)
    for resource in resources:
        if resource.get("resource_type", "").lower() == "image" and resource.get("sku"):
            images_by_sku[resource["sku"]].append(resource)

    required_card_fields = [
        "what_it_is", "who_asks_about_it", "common_use_cases", "how_to_use_short",
        "what_to_expect_soft", "contraindications_short",
    ]
    missing_cards = sorted(sku for sku in products if sku not in cards)
    cards_without_photo = sorted(sku for sku in cards if not images_by_sku.get(sku))
    incomplete_cards = {
        sku: [field for field in required_card_fields if not str(card.get(field, "")).strip()]
        for sku, card in cards.items()
    }
    incomplete_cards = {sku: fields for sku, fields in incomplete_cards.items() if fields}
    aliases_unknown_sku = sorted({row.get("canonical_sku") for row in rows["aliases"] if row.get("canonical_sku") and row.get("canonical_sku") not in products})
    resources_unknown_sku = sorted({row.get("sku") for row in resources if row.get("sku") and row.get("sku") not in products})
    product_price_gaps = sorted(
        sku for sku, row in products.items()
        if not any(str(row.get(field, "")).strip() and str(row.get(field, "")).strip() != "-" for field in ["retail_price_rub", "retail_price_byn", "partner_price_rub", "partner_price_byn"])
    )

    def check_resource(resource: dict) -> dict | None:
        url = resource.get("url", "").strip()
        if not url:
            return None
        return {
            "resource_id": resource.get("resource_id"),
            "sku": resource.get("sku"),
            "type": resource.get("resource_type"),
            "title": resource.get("title"),
            "url": url,
            **url_status(url),
        }

    with ThreadPoolExecutor(max_workers=12) as pool:
        link_checks = [row for row in pool.map(check_resource, resources) if row]
    broken_links = [row for row in link_checks if not row["ok"]]

    report = {
        "date": date.today().isoformat(),
        "mode": "read_only",
        "counts": {
            "products": len(products), "aliases": len(rows["aliases"]), "resources": len(resources),
            "cards": len(cards), "details": len(rows["details"]), "comparisons": len(rows["comparisons"]),
            "image_resources": sum(len(items) for items in images_by_sku.values()),
        },
        "issues": {
            "products_without_cards": missing_cards,
            "cards_without_active_image_resource": cards_without_photo,
            "incomplete_cards": incomplete_cards,
            "aliases_unknown_sku": aliases_unknown_sku,
            "resources_unknown_sku": resources_unknown_sku,
            "products_without_any_price": product_price_gaps,
            "broken_or_inaccessible_links": broken_links,
        },
        "link_check_summary": {
            "checked": len(link_checks), "ok": len(link_checks) - len(broken_links), "failed": len(broken_links),
        },
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
