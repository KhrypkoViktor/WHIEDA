"""Build local master snapshot fixtures without network."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "n8n" / "current"))

from whieda_master_integrity_lib import EXPECTED_LAYERS, LAYER_ID_FIELDS, RUNTIME_TABLES, analyze_layer_file

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"


def _write_layer(base: Path, layer: str, header: str, rows: list[str]) -> None:
    content = header + "\n" + "\n".join(rows) + ("\n" if rows else "")
    path = base / f"{layer}.tsv"
    path.write_text(content, encoding="utf-8")


def _manifest_for(base: Path) -> dict:
    layers = {}
    for layer in EXPECTED_LAYERS:
        path = base / f"{layer}.tsv"
        analyzed = analyze_layer_file(layer, path)
        layers[layer] = {
            "gid": EXPECTED_LAYERS[layer],
            "file": path.name,
            "bytes": analyzed["bytes"],
            "rows": analyzed["rows"],
            "sha256": analyzed["sha256"],
            "header_hash": analyzed["header_hash"],
        }
    return {
        "sheet_id": "fixture-sheet",
        "captured_at": "2026-08-10T10:00:00+00:00",
        "layers": layers,
    }


def _critical_products_rows(n: int) -> list[str]:
    return [f"SKU-{i:03d}\tProduct {i}" for i in range(1, n + 1)]


def _critical_aliases_rows(n: int) -> list[str]:
    return [f"alias-{i}\tSKU-{(i % 20) + 1:03d}\tProduct {(i % 20) + 1}" for i in range(1, n + 1)]


def _critical_resources_rows(n: int) -> list[str]:
    return [
        f"res-{i}\tSKU-{(i % 20) + 1:03d}\tProduct\t\ttitle\thttps://example.com/{i}"
        for i in range(1, n + 1)
    ]


def _critical_cards_rows(n: int) -> list[str]:
    return [f"SKU-{i:03d}\tCard {i}" for i in range(1, n + 1)]


def _generic_layer_header_and_row(layer: str) -> tuple[str, list[str]]:
    if layer == "partners_ref":
        return "partner_id\tname", ["partner-1\tPartner"]
    if layer in RUNTIME_TABLES:
        fields = list(RUNTIME_TABLES[layer][1])
    else:
        fields = list(LAYER_ID_FIELDS.get(layer, ["id"]))
    header = "\t".join(fields)
    values = []
    for field in fields:
        if field.endswith("_id") or field in {"sku", "alias", "structure_code", "clarification_key"}:
            values.append(f"{field}-1")
        elif field.endswith("_sku"):
            values.append("SKU-001")
        else:
            values.append("value")
    return header, ["\t".join(values)]


def build_valid_snapshot(target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    _write_layer(target, "products", "sku\tcanonical_name", _critical_products_rows(20))
    _write_layer(target, "aliases", "alias\tcanonical_sku\tcanonical_name", _critical_aliases_rows(60))
    _write_layer(target, "resources", "resource_id\tsku\tcanonical_name\talias\ttitle\turl", _critical_resources_rows(25))
    _write_layer(target, "product_cards", "sku\tcanonical_name", _critical_cards_rows(10))
    _write_layer(target, "product_details", "detail_id\tsku\ttopic\tanswer_text", [])
    for layer in EXPECTED_LAYERS:
        if layer in {"products", "aliases", "resources", "product_cards", "product_details"}:
            continue
        header, rows = _generic_layer_header_and_row(layer)
        _write_layer(target, layer, header, rows)
    (target / "manifest.json").write_text(json.dumps(_manifest_for(target), indent=2) + "\n", encoding="utf-8")
    return target


def build_missing_layer_snapshot(target: Path) -> Path:
    build_valid_snapshot(target)
    (target / "promotions.tsv").unlink()
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    del manifest["layers"]["promotions"]
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return target


def build_invalid_tsv_snapshot(target: Path) -> Path:
    build_valid_snapshot(target)
    (target / "events.tsv").write_text("", encoding="utf-8")
    return target


def build_collapsed_products_snapshot(target: Path) -> Path:
    build_valid_snapshot(target)
    _write_layer(target, "products", "sku\tcanonical_name", _critical_products_rows(5))
    (target / "manifest.json").write_text(json.dumps(_manifest_for(target), indent=2) + "\n", encoding="utf-8")
    return target


def build_header_mutation_snapshot(target: Path) -> Path:
    build_valid_snapshot(target)
    _write_layer(target, "aliases", "alias\tcanonical_sku\tcanonical_title", _critical_aliases_rows(60))
    (target / "manifest.json").write_text(json.dumps(_manifest_for(target), indent=2) + "\n", encoding="utf-8")
    return target


def ensure_all_fixtures() -> Path:
    root = FIXTURE_ROOT
    build_valid_snapshot(root / "valid")
    build_missing_layer_snapshot(root / "missing_layer")
    build_invalid_tsv_snapshot(root / "invalid_tsv")
    build_collapsed_products_snapshot(root / "collapsed_products")
    build_header_mutation_snapshot(root / "header_mutation")
    build_valid_snapshot(root / "valid_copy")
    return root


if __name__ == "__main__":
    path = ensure_all_fixtures()
    print(path)
