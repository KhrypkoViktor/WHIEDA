#!/usr/bin/env python3
"""Generate data quality test fixtures (offline, does not touch exports/)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "qa" / "data_quality" / "fixtures"
SCENARIOS = FIX / "scenarios"

BASE_PRODUCTS = (
    "sku\tcanonical_name\tretail_price_rub\tpartner_points\tactive\n"
    "M015-00\tАктиватор клеток\t12000\t45\ttrue\n"
    "EU-N000031-25\tАктиватор клеток PRO\t18000\t60\ttrue\n"
    "EU-N000021-24\tMagic Foherb\t25000\t80\ttrue\n"
)

BASE_ALIASES = (
    "alias\tcanonical_sku\tactive\n"
    "активатор\tM015-00\ttrue\n"
    "бэм\tEU-N000021-24\ttrue\n"
)

BASE_RESOURCES = (
    "resource_id\tsku\tresource_type\ttitle\turl\tactive\n"
    "R-001\tM015-00\tphoto\tФото\thttps://cdn.example/activator.jpg\ttrue\n"
    "R-002\tM015-00\tvideo\tВидео\thttps://cdn.example/activator.mp4\ttrue\n"
)

BASE_CARDS = (
    "sku\tcanonical_name\twhat_it_is\tcontraindications_short\tactive\n"
    "M015-00\tАктиватор клеток\tОписание\tНе заменяет лечение\ttrue\n"
)

BASE_BUNDLES = (
    "bundle_id\ttitle\tprimary_sku\tadditional_skus\trestrictions\tactive\n"
    "B-001\tСтарт\tM015-00\tEU-N000021-24\tНе для детей\ttrue\n"
)

BASE_FAQ = (
    "faq_id\ttitle\tanswer_text\tactive\n"
    "FAQ-001\tПовторка\tПовторные покупки партнёра\ttrue\n"
)

BASE_PROMOS = (
    "promotion_id\ttitle\tstart_date\tend_date\tactive\n"
    "P-001\tАкция\t2026-01-01\t2026-12-31\ttrue\n"
)

BASE_EVENTS = (
    "event_id\ttitle\tstart_date\turl\tactive\n"
    "E-001\tКонференция\t2026-06-01\thttps://events.example/conf\ttrue\n"
)

BASE_USERS = (
    "user_id\temail\trole\tactive\n"
    "U-001\tops@example.test\teditor\ttrue\n"
)

BASE_OWNERS = (
    "owner_id\tstructure_code\towner_name\tactive\n"
    "O-001\tWHIEDA-ROOT\tOwner One\ttrue\n"
)

BASE_CERTS = (
    "certificate_id\tsku\ttitle\tresource_type\turl\tactive\n"
    "C-001\tM015-00\tСертификат\tcertificate\thttps://cdn.example/cert.pdf\ttrue\n"
)

BASE_COMPARISONS = (
    "comparison_id\tsku_a\tsku_b\ttitle\tactive\n"
    "CMP-001\tM015-00\tEU-N000031-25\tPRO vs base\ttrue\n"
)


def write(name: str, files: dict[str, str]) -> None:
    folder = SCENARIOS / name
    folder.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        (folder / fname).write_text(content, encoding="utf-8")


def main() -> int:
    FIX.mkdir(parents=True, exist_ok=True)
    SCENARIOS.mkdir(parents=True, exist_ok=True)

    scenarios: dict[str, dict[str, str]] = {
        "valid_full": {
            "products_prices.tsv": BASE_PRODUCTS,
            "product_aliases.tsv": BASE_ALIASES,
            "resource_links.tsv": BASE_RESOURCES,
            "product_cards.tsv": BASE_CARDS,
            "solution_bundles.tsv": BASE_BUNDLES,
            "business_faq.tsv": BASE_FAQ,
            "promotions.tsv": BASE_PROMOS,
            "events.tsv": BASE_EVENTS,
            "users_access.tsv": BASE_USERS,
            "structure_owners.tsv": BASE_OWNERS,
            "certificates.tsv": BASE_CERTS,
            "product_comparisons.tsv": BASE_COMPARISONS,
        },
        "dup_sku": {"products_prices.tsv": BASE_PRODUCTS + "M015-00\tДубликат\t1000\t10\ttrue\n"},
        "price_string": {"products_prices.tsv": "sku\tcanonical_name\tretail_price_rub\tpartner_points\tactive\nM015-00\tАктиватор\tabc\t45\ttrue\n"},
        "pv_comma": {"products_prices.tsv": "sku\tcanonical_name\tretail_price_rub\tpartner_points\tactive\nM015-00\tАктиватор\t12000\t45,5\ttrue\n"},
        "empty_alias": {"product_aliases.tsv": "alias\tcanonical_sku\tactive\n\tM015-00\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "bad_url": {"resource_links.tsv": "resource_id\tsku\tresource_type\turl\tactive\nR-9\tM015-00\tphoto\thttp://bad\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "alias_missing_sku": {"product_aliases.tsv": "alias\tcanonical_sku\tactive\nghost\tNO-SKU\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "bundle_unknown": {"solution_bundles.tsv": "bundle_id\ttitle\tprimary_sku\tactive\nB-X\tX\tNO-SKU\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "two_active_prices": {
            "products_prices.tsv": (
                "sku\tcanonical_name\tcountry\tprice_type\tretail_price_rub\tactive\n"
                "M015-00\tАктиватор\tBY\tretail\t12000\ttrue\n"
                "M015-00\tАктиватор\tBY\tretail\t13000\ttrue\n"
            )
        },
        "promo_bad_dates": {"promotions.tsv": "promotion_id\ttitle\tstart_date\tend_date\tactive\nP-BAD\tX\t2026-12-01\t2026-01-01\ttrue\n"},
        "promo_missing_end": {"promotions.tsv": "promotion_id\ttitle\tstart_date\tend_date\tactive\nP-NOEND\tX\t2026-01-01\t\ttrue\n"},
        "inactive_in_bundle": {
            "products_prices.tsv": BASE_PRODUCTS.replace("EU-N000021-24\tMagic Foherb\t25000\t80\ttrue", "EU-N000021-24\tMagic Foherb\t25000\t80\tfalse"),
            "solution_bundles.tsv": BASE_BUNDLES,
        },
        "cert_no_product": {"certificates.tsv": "certificate_id\tsku\ttitle\tresource_type\turl\tactive\nC-X\tNO-SKU\tX\tcertificate\thttps://cdn.example/x.pdf\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "faq_empty_answer": {"business_faq.tsv": "faq_id\ttitle\tanswer_text\tactive\nFAQ-X\tX\t\ttrue\n"},
        "duplicate_alias": {"product_aliases.tsv": BASE_ALIASES + "активатор\tM015-00\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "duplicate_faq": {"business_faq.tsv": BASE_FAQ + "FAQ-001\tDup\tText\ttrue\n"},
        "duplicate_resource_id": {"resource_links.tsv": BASE_RESOURCES + "R-001\tM015-00\tphoto\tDup\thttps://cdn.example/dup.jpg\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "duplicate_url": {"resource_links.tsv": BASE_RESOURCES + "R-003\tM015-00\tphoto\tDup\thttps://cdn.example/activator.jpg\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "zero_price": {"products_prices.tsv": "sku\tcanonical_name\tretail_price_rub\tactive\nM015-00\tАктиватор\t0\ttrue\n"},
        "negative_pv": {"products_prices.tsv": "sku\tcanonical_name\tpartner_points\tactive\nM015-00\tАктиватор\t-1\ttrue\n"},
        "missing_material_type": {"resource_links.tsv": "resource_id\tsku\tresource_type\turl\tactive\nR-X\tM015-00\t\thttps://cdn.example/x.jpg\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "nordman_artifact": {"product_cards.tsv": "sku\tcanonical_name\twhat_it_is\tactive\nM015-00\tX\tNordman leak\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "traceback_artifact": {"business_faq.tsv": "faq_id\ttitle\tanswer_text\tactive\nFAQ-T\tX\tTraceback here\ttrue\n"},
        "human_review_artifact": {"business_faq.tsv": "faq_id\ttitle\tanswer_text\tactive\nFAQ-H\tX\tI need human review\ttrue\n"},
        "medical_warning_card": {"product_cards.tsv": "sku\tcanonical_name\twhat_it_is\tcontraindications_short\tactive\nM015-00\tX\tлечит диабет\t\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "medical_warning_bundle": {"solution_bundles.tsv": "bundle_id\ttitle\tprimary_sku\trestrictions\tactive\nB-M\tX\tM015-00\tгарантирует излечение\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "resource_missing_sku": {"resource_links.tsv": "resource_id\tsku\tresource_type\turl\tactive\nR-NO\t\tphoto\thttps://cdn.example/x.jpg\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "comparison_unknown_sku": {"product_comparisons.tsv": "comparison_id\tsku_a\tsku_b\tactive\nC-1\tNO-A\tM015-00\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "invalid_boolean": {"products_prices.tsv": "sku\tcanonical_name\tretail_price_rub\tactive\nM015-00\tАктиватор\t12000\tmaybe\n"},
        "invalid_date_event": {"events.tsv": "event_id\ttitle\tstart_date\tactive\nE-X\tX\tnot-a-date\ttrue\n"},
        "users_dup": {"users_access.tsv": BASE_USERS + "U-001\tother@test\tadmin\ttrue\n"},
        "owners_dup_code": {"structure_owners.tsv": BASE_OWNERS + "O-002\tWHIEDA-ROOT\tTwo\ttrue\n"},
        "card_unknown_sku": {"product_cards.tsv": "sku\tcanonical_name\tactive\nNO-SKU\tGhost\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "promo_unknown_sku_warn": {"promotions.tsv": "promotion_id\ttitle\tstart_date\tend_date\tsku\tactive\nP-U\tX\t2026-01-01\t2026-12-31\tNO-SKU\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "bundle_extra_unknown": {"solution_bundles.tsv": "bundle_id\ttitle\tprimary_sku\tadditional_skus\tactive\nB-E\tX\tM015-00\tNO-1,NO-2\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "alias_valid_json": {"product_aliases.json": json.dumps([{"alias": "активатор", "canonical_sku": "M015-00", "active": "true"}], ensure_ascii=False)},
        "products_csv": {"products_prices.csv": BASE_PRODUCTS.replace("\t", ",")},
        "products_jsonl": {"products_prices.jsonl": json.dumps({"sku": "M015-00", "canonical_name": "Активатор", "retail_price_rub": "12000", "partner_points": "45", "active": "true"}, ensure_ascii=False) + "\n"},
        "price_change_v2": {"products_prices.tsv": BASE_PRODUCTS.replace("12000", "12500")},
        "alias_removed_v2": {"product_aliases.tsv": "alias\tcanonical_sku\tactive\nбэм\tEU-N000021-24\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "safety_change_v2": {"product_cards.tsv": "sku\tcanonical_name\tcontraindications_short\tactive\nM015-00\tАктиватор\tНовое ограничение\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "empty_products": {"products_prices.tsv": "sku\tcanonical_name\tretail_price_rub\tactive\n"},
        "enum_resource_type_warn": {"resource_links.tsv": "resource_id\tsku\tresource_type\turl\tactive\nR-E\tM015-00\tunknown\thttps://cdn.example/x.jpg\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "cert_missing_url": {"certificates.tsv": "certificate_id\tsku\ttitle\tresource_type\turl\tactive\nC-B\tM015-00\tX\tcertificate\t\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "faq_inactive_ok": {"business_faq.tsv": "faq_id\ttitle\tanswer_text\tactive\nFAQ-I\tX\t\tfalse\n"},
        "multi_question_bundle": {"solution_bundles.tsv": "bundle_id\ttitle\tprimary_sku\tadditional_skus\tactive\nB-M\tПосчитай\tM015-00\tEU-N000021-24, M014-00\ttrue\n", "products_prices.tsv": BASE_PRODUCTS + "M014-00\tБа-Гуа\t30000\t90\ttrue\n"},
        "resource_photo_only": {"resource_links.tsv": "resource_id\tsku\tresource_type\turl\tactive\nR-P\tM015-00\tphoto\thttps://cdn.example/p.jpg\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "resource_video_only": {"resource_links.tsv": "resource_id\tsku\tresource_type\turl\tactive\nR-V\tM015-00\tvideo\thttps://cdn.example/v.mp4\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "resource_cert_only": {"resource_links.tsv": "resource_id\tsku\tresource_type\turl\tactive\nR-C\tM015-00\tcertificate\thttps://cdn.example/c.pdf\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "structure_valid": {"structure_owners.tsv": BASE_OWNERS},
        "users_inactive": {"users_access.tsv": "user_id\temail\trole\tactive\nU-2\tx@test\tviewer\tfalse\n"},
        "card_https_image": {"product_cards.tsv": "sku\tcanonical_name\tprimary_image_url\tactive\nM015-00\tA\thttps://cdn.example/a.jpg\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "card_bad_image_url": {"product_cards.tsv": "sku\tcanonical_name\tprimary_image_url\tactive\nM015-00\tA\tftp://bad\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "promo_valid_sku": {"promotions.tsv": "promotion_id\ttitle\tstart_date\tend_date\tsku\tactive\nP-OK\tOK\t2026-01-01\t2026-12-31\tM015-00\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "events_valid_range": {"events.tsv": "event_id\ttitle\tstart_date\tend_date\tactive\nE-2\tMeet\t2026-01-01\t2026-01-02\ttrue\n"},
        "comparison_valid": {"product_comparisons.tsv": BASE_COMPARISONS, "products_prices.tsv": BASE_PRODUCTS},
        "aliases_typo_ativator": {"product_aliases.tsv": "alias\tcanonical_sku\tactive\nативатор\tM015-00\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "price_repeat_type": {"products_prices.tsv": "sku\tcanonical_name\tcountry\tprice_type\tpartner_price_rub\tactive\nM015-00\tA\tBY\trepeat\t9000\ttrue\n"},
        "bundle_inactive_ok": {"solution_bundles.tsv": "bundle_id\ttitle\tprimary_sku\tactive\nB-OFF\tOff\tM015-00\tfalse\n", "products_prices.tsv": BASE_PRODUCTS.replace("EU-N000021-24\tMagic Foherb\t25000\t80\tfalse", "EU-N000021-24\tMagic Foherb\t25000\t80\ttrue")},
        "cert_inactive": {"certificates.tsv": "certificate_id\tsku\ttitle\tresource_type\turl\tactive\nC-O\tM015-00\tOld\tcertificate\thttps://cdn.example/old.pdf\tfalse\n", "products_prices.tsv": BASE_PRODUCTS},
        "resource_inactive": {"resource_links.tsv": "resource_id\tsku\tresource_type\turl\tactive\nR-O\tM015-00\tphoto\thttps://cdn.example/old.jpg\tfalse\n", "products_prices.tsv": BASE_PRODUCTS},
        "faq_priority_number": {"business_faq.tsv": "faq_id\ttitle\tanswer_text\tpriority\tactive\nFAQ-P\tP\tAnswer\t1\ttrue\n"},
        "owners_missing_name": {"structure_owners.tsv": "owner_id\tstructure_code\towner_name\tactive\nO-X\tCODE\t\ttrue\n"},
        "users_missing_role": {"users_access.tsv": "user_id\temail\trole\tactive\nU-X\tx@test\t\ttrue\n"},
        "products_missing_name": {"products_prices.tsv": "sku\tcanonical_name\tretail_price_rub\tactive\nM015-00\t\t12000\ttrue\n"},
        "aliases_missing_sku_col": {"product_aliases.tsv": "alias\tcanonical_sku\tactive\nsolo\t\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "bundle_missing_title": {"solution_bundles.tsv": "bundle_id\ttitle\tprimary_sku\tactive\nB-NT\t\tM015-00\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "card_safety_do_not_claim": {"product_cards.tsv": "sku\tcanonical_name\tdo_not_claim\tactive\nM015-00\tA\tНе обещать излечение\ttrue\n", "products_prices.tsv": BASE_PRODUCTS},
        "promo_far_future": {"promotions.tsv": "promotion_id\ttitle\tstart_date\tend_date\tactive\nP-F\tFuture\t2026-01-01\t2027-01-01\ttrue\n"},
        "event_missing_title": {"events.tsv": "event_id\ttitle\tstart_date\tactive\nE-NT\t\t2026-01-01\ttrue\n"},
        "comparison_inactive": {"product_comparisons.tsv": "comparison_id\tsku_a\tsku_b\tactive\nCMP-O\tM015-00\tEU-N000031-25\tfalse\n", "products_prices.tsv": BASE_PRODUCTS},
        "valid_minimal_products": {"products_prices.tsv": "sku\tcanonical_name\tretail_price_rub\tpartner_points\tactive\nM015-00\tАктиватор\t12000\t45\ttrue\n"},
    }

    file_count = 0
    for name, files in scenarios.items():
        write(name, files)
        file_count += len(files)

    # test manifest mapping scenario dirs
    manifest = {
        "version": 1,
        "exports_root": "qa/data_quality/fixtures/scenarios/valid_full",
        "sources": [],
    }
    template = json.loads((ROOT / "qa" / "data_quality" / "source_manifest.json").read_text(encoding="utf-8"))
    for src in template["sources"]:
        entry = dict(src)
        entry["path"] = f"qa/data_quality/fixtures/scenarios/valid_full/{src['file']}"
        manifest["sources"].append(entry)

    (FIX / "manifest.test.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    index = {"scenarios": sorted(scenarios.keys()), "scenario_count": len(scenarios), "file_count": file_count}
    (FIX / "fixtures_index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Generated {len(scenarios)} scenarios, {file_count} fixture files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
