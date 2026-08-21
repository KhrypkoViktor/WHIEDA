#!/usr/bin/env python3
"""Build synthetic tenant-alpha/beta packages. No NSP/WHIEDA catalog copy."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backend" / "platform-api" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from tenant_release.package import seal_package, write_json  # noqa: E402

PKG = ROOT / "qa" / "tenant_release_package"


def _product(tenant: str, sku: str, name: str, *, status: str, price=None, price_missing=False, media_state="present"):
    row = {
        "tenant_id": tenant,
        "sku": sku,
        "canonical_name": name,
        "review_status": status,
        "price_missing": price_missing,
        "media_state": media_state,
        "source": {"kind": "synthetic-fixture", "ref": f"fixture:{tenant}:{sku}"},
    }
    if price is not None:
        row["retail_price_byn"] = price
        row["partner_price_byn"] = round(price * 0.7, 2)
        row["partner_w"] = 8
    return row


def _card(tenant: str, sku: str, name: str) -> dict:
    return {
        "tenant_id": tenant,
        "sku": sku,
        "canonical_name": name,
        "what_it_is": f"Card for {name}",
        "primary_image_url": f"https://cdn.example.test/{tenant}/{sku}.jpg",
    }


def _alias(tenant: str, alias: str, sku: str) -> dict:
    return {"tenant_id": tenant, "alias": alias, "canonical_sku": sku}


def _media(tenant: str, sku: str) -> dict:
    return {
        "tenant_id": tenant,
        "sku": sku,
        "resource_type": "image",
        "url": f"https://cdn.example.test/{tenant}/{sku}.jpg",
        "title": f"photo {sku}",
    }


def _faq(tenant: str, sku: str, name: str) -> dict:
    return {
        "tenant_id": tenant,
        "faq_id": f"{sku}-faq",
        "sku": sku,
        "title": name,
        "answer_text": f"FAQ {name}",
    }


def _usd_product(tenant: str, sku: str, name: str, *, amount: str, source: str, status: str = "approved"):
    return {
        "tenant_id": tenant,
        "sku": sku,
        "canonical_name": name,
        "review_status": status,
        "price_missing": False,
        "media_state": "present",
        "source": {"kind": "synthetic-fixture", "ref": f"fixture:{tenant}:{sku}"},
        "prices": [
            {
                "kind": "retail",
                "amount": amount,
                "currency": "USD",
                "source": source,
            }
        ],
    }


def write_package(rel: str, tenant: str, package_id: str, products, aliases, cards, media, faqs, *, extra_manifest=None):
    directory = PKG / rel
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "products.json", products)
    write_json(directory / "aliases.json", aliases)
    write_json(directory / "cards.json", cards)
    write_json(directory / "media.json", media)
    write_json(directory / "faq.json", faqs)
    manifest = {
        "schema_version": "tenant-release-package.v1",
        "package_id": package_id,
        "package_version": "1.0.0",
        "tenant_id": tenant,
        "release_status": "candidate",
        "display": {"display_name": tenant.replace("-", " ").title(), "advisor_signature": f"советник {tenant}"},
        "files": {
            "products": {"path": "products.json", "sha256": "0" * 64},
            "aliases": {"path": "aliases.json", "sha256": "0" * 64},
            "cards": {"path": "cards.json", "sha256": "0" * 64},
            "media": {"path": "media.json", "sha256": "0" * 64},
            "faq": {"path": "faq.json", "sha256": "0" * 64},
        },
        "source": {"kind": "synthetic-fixture", "ref": str(directory.relative_to(ROOT)).replace("\\", "/")},
    }
    if extra_manifest:
        manifest.update(extra_manifest)
    write_json(directory / "manifest.json", manifest)
    seal_package(directory)
    return directory


def main() -> None:
    alpha = "tenant-alpha"
    write_package(
        "examples/tenant-alpha",
        alpha,
        "tenant-alpha-catalog",
        [
            _product(alpha, "A-001", "Alpha Spirulina", status="approved", price=41),
            _product(alpha, "A-002", "Alpha Tea", status="approved", price_missing=True, media_state="missing"),
            _product(alpha, "A-003", "Alpha Serum", status="review_required", price=90),
            _product(alpha, "A-004", "Alpha Blocked", status="blocked", price=12),
        ],
        [
            _alias(alpha, "спиралина", "A-001"),
            _alias(alpha, "чай альфа", "A-002"),
        ],
        [
            _card(alpha, "A-001", "Alpha Spirulina"),
            _card(alpha, "A-002", "Alpha Tea"),
            _card(alpha, "A-003", "Alpha Serum"),
        ],
        [_media(alpha, "A-001")],
        [_faq(alpha, "A-001", "Alpha Spirulina")],
    )

    beta = "tenant-beta"
    write_package(
        "examples/tenant-beta",
        beta,
        "tenant-beta-catalog",
        [_product(beta, "B-001", "Beta Spirulina", status="approved", price=77)],
        [_alias(beta, "спиралина", "B-001")],
        [_card(beta, "B-001", "Beta Spirulina")],
        [_media(beta, "B-001")],
        [_faq(beta, "B-001", "Beta Spirulina")],
    )

    write_package(
        "fixtures/mixed-tenant",
        alpha,
        "tenant-alpha-mixed",
        [
            _product(alpha, "A-001", "Alpha Spirulina", status="approved", price=41),
            _product("tenant-beta", "B-HACK", "Leaked Beta", status="approved", price=1),
        ],
        [_alias(alpha, "спиралина", "A-001")],
        [_card(alpha, "A-001", "Alpha Spirulina")],
        [_media(alpha, "A-001")],
        [_faq(alpha, "A-001", "Alpha Spirulina")],
    )

    write_package(
        "fixtures/alias-collision",
        alpha,
        "tenant-alpha-alias-collision",
        [
            _product(alpha, "A-010", "Alpha One", status="approved", price=10),
            _product(alpha, "A-011", "Alpha Two", status="approved", price=11),
        ],
        [
            _alias(alpha, "дубль", "A-010"),
            _alias(alpha, "дубль", "A-011"),
        ],
        [_card(alpha, "A-010", "Alpha One"), _card(alpha, "A-011", "Alpha Two")],
        [_media(alpha, "A-010"), _media(alpha, "A-011")],
        [],
    )

    gamma = "tenant-gamma"
    write_package(
        "examples/tenant-gamma",
        gamma,
        "tenant-gamma-usd-catalog",
        [
            _usd_product(
                gamma,
                "G-001",
                "Gamma Capsule",
                amount="37.13",
                source="catalogue_2026",
            )
        ],
        [_alias(gamma, "капсула гамма", "G-001")],
        [_card(gamma, "G-001", "Gamma Capsule")],
        [_media(gamma, "G-001")],
        [_faq(gamma, "G-001", "Gamma Capsule")],
    )

    hashed = write_package(
        "fixtures/hash-mismatch",
        alpha,
        "tenant-alpha-hash-mismatch",
        [_product(alpha, "A-001", "Alpha Spirulina", status="approved", price=41)],
        [_alias(alpha, "спиралина", "A-001")],
        [_card(alpha, "A-001", "Alpha Spirulina")],
        [_media(alpha, "A-001")],
        [_faq(alpha, "A-001", "Alpha Spirulina")],
    )
    manifest = hashed / "manifest.json"
    text = manifest.read_text(encoding="utf-8").replace(
        '"products"',
        '"products"',
        1,
    )
    # Force a bad products hash after seal.
    import json

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["files"]["products"]["sha256"] = "a" * 64
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _ = text
    print("wrote synthetic packages under qa/tenant_release_package")


if __name__ == "__main__":
    main()
