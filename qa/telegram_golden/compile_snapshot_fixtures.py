#!/usr/bin/env python3
"""Compile golden snapshot card fixtures from immutable master export (read-only)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "n8n" / "live-exports" / "structured-master" / "20260810T083328Z"
OUT_DIR = Path(__file__).resolve().parent / "fixtures" / "snapshot_cards"

EXTRA_SKUS: dict[str, str] = {
    "soy_peptide": "F038-00",
    "foher_elixir": "F001-02",
    "treasures_elixir": "F002-02",
    "sancin_elixir": "F003-02",
    "tsinfeng_paste": "F071-00",
}

GOLDEN_MARKERS_FULL = ["🔥 Коротко:", "👥 Для кого:", "⚠️ Ограничения:"]
GOLDEN_MARKERS_BASIC = ["🔥 Коротко:", "👥 Для кого:"]


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _index_by_sku(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {(row.get("sku") or "").strip(): row for row in rows if (row.get("sku") or "").strip()}


def compile_extra_cards(*, snapshot_dir: Path = SNAPSHOT, out_dir: Path = OUT_DIR) -> dict[str, Path]:
    cards_path = snapshot_dir / "product_cards.tsv"
    products_path = snapshot_dir / "products.tsv"
    if not cards_path.is_file() or not products_path.is_file():
        raise FileNotFoundError(f"snapshot incomplete under {snapshot_dir}")

    cards = _index_by_sku(_read_tsv(cards_path))
    products = _index_by_sku(_read_tsv(products_path))
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    for slug, sku in EXTRA_SKUS.items():
        card = cards.get(sku)
        product = products.get(sku)
        if not card or not str(card.get("what_it_is") or "").strip():
            raise KeyError(f"missing approved card row for {slug} ({sku})")
        payload = {"slug": slug, "sku": sku, "product": product or {}, "card": card}
        target = out_dir / f"{slug}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written[slug] = target
    return written


def update_manifest(*, out_dir: Path = OUT_DIR) -> None:
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cards = list(manifest.get("cards") or [])
    existing_slugs = {str(c.get("slug")) for c in cards}

    canonical_names = {
        "soy_peptide": "Низкомолекулярный соевый пептид",
        "foher_elixir": "Эликсир Фохоу",
        "treasures_elixir": "Эликсир 3 Драгоценности",
        "sancin_elixir": "Эликсир Саньцин",
        "tsinfeng_paste": "Паста Цинфэн",
    }
    for slug, sku in EXTRA_SKUS.items():
        if slug in existing_slugs:
            continue
        markers = GOLDEN_MARKERS_FULL if slug != "tsinfeng_paste" else GOLDEN_MARKERS_BASIC
        cards.append(
            {
                "slug": slug,
                "sku": sku,
                "canonical_name": canonical_names[slug],
                "golden_markers": markers,
                "file": f"{slug}.json",
            }
        )
    manifest["version"] = "2026-08-12-v1.1"
    manifest["snapshot_source"] = "n8n/live-exports/structured-master/20260810T083328Z"
    manifest["cards"] = cards
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    written = compile_extra_cards()
    update_manifest()
    print(f"Wrote {len(written)} extra card fixtures -> {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
