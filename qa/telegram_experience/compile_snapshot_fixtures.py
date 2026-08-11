#!/usr/bin/env python3
"""Compile local Telegram experience fixtures from immutable master snapshot."""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "n8n" / "live-exports" / "structured-master" / "20260810T083328Z"
OUT_DIR = Path(__file__).resolve().parent / "fixtures" / "master_snapshot_cards"

# Required named products for card renderer acceptance (Block A1).
NAMED_SKUS: dict[str, str] = {
    "activator": "M015-00",
    "activator_pro": "EU-N000031-25",
    "ba_gua": "M014-00",
    "bem": "EU-N000021-24",
    "wentun": "EU-N000024-24",
    "glasses": "D014",
    "insoles": "D013",
    "spirulina": "F036-00",
}


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _index_by_sku(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {(row.get("sku") or "").strip(): row for row in rows if (row.get("sku") or "").strip()}


def compile_fixtures(*, snapshot_dir: Path = SNAPSHOT, out_dir: Path = OUT_DIR) -> dict[str, Path]:
    cards_path = snapshot_dir / "product_cards.tsv"
    products_path = snapshot_dir / "products.tsv"
    if not cards_path.is_file() or not products_path.is_file():
        raise FileNotFoundError(f"snapshot incomplete under {snapshot_dir}")

    cards = _index_by_sku(_read_tsv(cards_path))
    products = _index_by_sku(_read_tsv(products_path))

    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    for slug, sku in NAMED_SKUS.items():
        card = cards.get(sku)
        product = products.get(sku)
        if not card:
            raise KeyError(f"missing product_cards row for {slug} ({sku})")
        payload = {
            "slug": slug,
            "sku": sku,
            "product": product or {},
            "card": card,
        }
        target = out_dir / f"{slug}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written[slug] = target

    manifest = {
        "snapshot_dir": str(snapshot_dir.relative_to(ROOT)).replace("\\", "/"),
        "products": NAMED_SKUS,
        "files": {slug: path.name for slug, path in written.items()},
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return written


def main() -> None:
    written = compile_fixtures()
    print(f"Wrote {len(written)} card fixtures -> {OUT_DIR}")


if __name__ == "__main__":
    main()
