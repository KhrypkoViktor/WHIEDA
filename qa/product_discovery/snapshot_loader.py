"""Snapshot loader for product discovery map."""

from __future__ import annotations

import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "qa" / "catalog_experience"
if str(CATALOG) not in sys.path:
    sys.path.insert(0, str(CATALOG))

from audit_lib import find_latest_valid_snapshot, load_snapshot, verify_snapshot  # noqa: E402


@dataclass
class SnapshotBundle:
    snapshot_id: str
    snapshot_dir: Path
    manifest: dict[str, Any]
    products: dict[str, dict[str, str]]
    aliases: list[dict[str, str]]
    cards: dict[str, dict[str, str]]
    layer_hashes: dict[str, str]


def normalize_phrase(text: str) -> str:
    lowered = unicodedata.normalize("NFKC", str(text or "")).casefold().strip()
    return lowered.replace("ё", "е")


def load_bundle(snapshot_dir: Path | None = None) -> SnapshotBundle:
    snap = snapshot_dir or find_latest_valid_snapshot()
    data = load_snapshot(snap)
    manifest = verify_snapshot(snap)
    layers = manifest.get("layers") or {}
    hashes = {name: str(meta.get("sha256") or "") for name, meta in layers.items()}
    return SnapshotBundle(
        snapshot_id=snap.name,
        snapshot_dir=snap,
        manifest=manifest,
        products=data["products"],
        aliases=data["aliases_rows"],
        cards=data["cards"],
        layer_hashes=hashes,
    )


def active_aliases(bundle: SnapshotBundle) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in bundle.aliases:
        if str(row.get("active") or "").strip().upper() not in {"TRUE", "1", "YES"}:
            continue
        sku = str(row.get("canonical_sku") or "").strip()
        if sku not in bundle.products:
            continue
        out.append(row)
    return out


def aliases_for_phrase(bundle: SnapshotBundle, phrase: str) -> list[dict[str, str]]:
    norm = normalize_phrase(phrase)
    matches = [
        row
        for row in active_aliases(bundle)
        if normalize_phrase(row.get("alias") or "") == norm
    ]
    matches.sort(key=lambda r: int(str(r.get("priority") or 0)), reverse=True)
    return matches


def product_name(bundle: SnapshotBundle, sku: str) -> str:
    return str((bundle.products.get(sku) or {}).get("canonical_name") or "")
