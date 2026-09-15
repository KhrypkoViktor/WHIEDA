"""Build generic two-tenant canary preflight fixtures. No live tenant names in Core logic."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backend" / "platform-api" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from tenant_release.package import seal_package, write_json  # noqa: E402

PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bf0000000049454e44ae426082"
)


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write_package(package_dir: Path, *, tenant_id: str, sku: str, alias: str, name: str) -> None:
    package_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        package_dir / "manifest.json",
        {
            "schema_version": "tenant-release-package.v1",
            "package_id": f"{tenant_id}-canary",
            "package_version": "1.0.0",
            "tenant_id": tenant_id,
            "release_status": "candidate",
            "display": {"display_name": tenant_id, "advisor_signature": f"советник {tenant_id}"},
            "files": {
                "products": {"path": "products.json", "sha256": ""},
                "aliases": {"path": "aliases.json", "sha256": ""},
                "cards": {"path": "cards.json", "sha256": ""},
                "media": {"path": "media.json", "sha256": ""},
                "faq": {"path": "faq.json", "sha256": ""},
            },
            "source": {"kind": "synthetic-fixture", "ref": f"qa/tenant_canary_preflight/{tenant_id}"},
        },
    )
    write_json(
        package_dir / "products.json",
        [
            {
                "tenant_id": tenant_id,
                "sku": sku,
                "canonical_name": name,
                "review_status": "approved",
                "price_missing": False,
                "media_state": "present",
                "source": {"kind": "synthetic-fixture", "ref": f"fixture:{tenant_id}:{sku}"},
                "prices": [
                    {
                        "kind": "retail",
                        "amount": "37.13",
                        "currency": "USD",
                        "source": "catalogue_2026",
                    }
                ],
            }
        ],
    )
    write_json(
        package_dir / "aliases.json",
        [{"tenant_id": tenant_id, "alias": alias, "canonical_sku": sku}],
    )
    write_json(
        package_dir / "cards.json",
        [
            {
                "tenant_id": tenant_id,
                "sku": sku,
                "canonical_name": name,
                "what_it_is": f"Card for {name}",
            }
        ],
    )
    write_json(
        package_dir / "media.json",
        [
            {
                "tenant_id": tenant_id,
                "sku": sku,
                "resource_type": "image",
                "url": f"media/{tenant_id}/{sku}/main.webp",
                "filename": "main.webp",
                "title": name,
            }
        ],
    )
    write_json(
        package_dir / "faq.json",
        [
            {
                "tenant_id": tenant_id,
                "faq_id": f"{sku}-faq",
                "sku": sku,
                "title": name,
                "answer_text": f"FAQ {name}",
            }
        ],
    )
    seal_package(package_dir)


def write_binding(path: Path, *, tenant_id: str, binding_id: str, username: str, bot_id: str) -> None:
    write_json(
        path,
        {
            "tenant_id": tenant_id,
            "tenant_status": "active",
            "bindings": [
                {
                    "binding_id": binding_id,
                    "tenant_id": tenant_id,
                    "status": "active",
                    "bot_username": username,
                    "bot_id": bot_id,
                    "processing_mode": "core",
                }
            ],
        },
    )


def write_runtime(path: Path, *, tenant_id: str, sku: str) -> None:
    write_json(
        path,
        {
            "tenant_id": tenant_id,
            "candidates": [{"tenant_id": tenant_id, "sku": sku, "review_status": "approved"}],
        },
    )


def write_manifest(path: Path, *, tenant_id: str, sku: str, source: Path) -> None:
    digest = sha256_hex(source.read_bytes())
    size = source.stat().st_size
    relative = f"media/{tenant_id}/{sku}/main.webp"
    source_rel = os.path.relpath(source.resolve(), path.parent.resolve()).replace("\\", "/")
    header = "tenant_id\tsku\trelative_path\tfilename\tsource_path\tsha256\tbytes\tmime_type\tstatus\treason"
    row = "\t".join(
        [
            tenant_id,
            sku,
            relative,
            "main.webp",
            source_rel,
            digest,
            str(size),
            "image/png",
            "ready",
            "",
        ]
    )
    path.write_text(header + "\n" + row + "\n", encoding="utf-8")


def build(root: Path) -> None:
    sources = root / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    pixel = sources / "pixel.png"
    pixel.write_bytes(PIXEL_PNG)
    sku = "SHARE-01"
    alias = "shared capsule"
    tenants = (
        ("tenant-north", "north-advisor-bot", "north_canary_bot", "91001", "North Capsule"),
        ("tenant-south", "south-advisor-bot", "south_canary_bot", "91002", "South Capsule"),
    )
    for tenant_id, binding_id, username, bot_id, name in tenants:
        base = root / "tenants" / tenant_id
        package = base / "package"
        write_package(package, tenant_id=tenant_id, sku=sku, alias=alias, name=name)
        write_binding(base / "binding.json", tenant_id=tenant_id, binding_id=binding_id, username=username, bot_id=bot_id)
        write_runtime(base / "runtime.json", tenant_id=tenant_id, sku=sku)
        write_manifest(base / "media-manifest.tsv", tenant_id=tenant_id, sku=sku, source=pixel)


if __name__ == "__main__":
    build(Path(__file__).resolve().parent)
