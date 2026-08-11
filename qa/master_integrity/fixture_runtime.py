"""Fixture runtime rows for master/runtime integrity tests (no network, no DSN)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "n8n" / "current"))

from whieda_master_integrity_lib import (  # noqa: E402
    LAYER_ID_FIELDS,
    RUNTIME_TABLES,
    TITLE_NORMALIZED_LAYERS,
    master_runtime_layer_stats,
    normalized_title_content_hash,
)


def runtime_rows_in_sync_with_snapshot(snapshot_dir: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for layer, (_, fields) in RUNTIME_TABLES.items():
        master = master_runtime_layer_stats(snapshot_dir, layer)
        row: dict[str, Any] = {
            "row_count": master["rows"],
            "content_hash": master["content_hash"],
            "ids": sorted(master["ids"]),
            "tenant_discriminator": "client_id",
        }
        if layer == "aliases":
            row["unique_business_row_count"] = master["master_unique_rows"]
            row["business_keys"] = set(master["business_keys"])
        if layer in TITLE_NORMALIZED_LAYERS:
            row["normalized_title_map"] = dict(master["normalized_title_map"])
            row["normalized_content_hash"] = master["normalized_content_hash"]
        rows[layer] = row
    return rows


def runtime_rows_stale(snapshot_dir: Path, *, layer: str = "products") -> dict[str, dict[str, Any]]:
    rows = runtime_rows_in_sync_with_snapshot(snapshot_dir)
    master = master_runtime_layer_stats(snapshot_dir, layer)
    stale_ids = sorted(master["ids"])
    if stale_ids:
        stale_ids[0] = f"STALE-{stale_ids[0]}"
    row = {
        "row_count": master["rows"],
        "content_hash": "deadbeef",
        "ids": stale_ids,
        "tenant_discriminator": "client_id",
    }
    if layer in TITLE_NORMALIZED_LAYERS:
        title_map = dict(master["normalized_title_map"])
        if title_map:
            first_id = sorted(title_map)[0]
            title_map[first_id] = "STALE-TITLE-MISMATCH"
        row["normalized_title_map"] = title_map
        row["normalized_content_hash"] = normalized_title_content_hash(title_map)
        row["content_hash"] = "deadbeef"
        row["ids"] = sorted(title_map)
        row["row_count"] = len(title_map)
    if layer == "aliases":
        keys = set(master["business_keys"])
        if keys:
            keys.pop()
        row["business_keys"] = keys
        row["unique_business_row_count"] = len(keys)
        row["ids"] = sorted({key[0] for key in keys})
        row["row_count"] = len(keys)
    rows[layer] = row
    return rows


def runtime_rows_extra(snapshot_dir: Path, *, layer: str = "products") -> dict[str, dict[str, Any]]:
    rows = runtime_rows_in_sync_with_snapshot(snapshot_dir)
    master = master_runtime_layer_stats(snapshot_dir, layer)
    extra_ids = sorted(set(master["ids"]) | {"EXTRA-SKU"})
    row = {
        "row_count": len(extra_ids),
        "content_hash": master["content_hash"],
        "ids": extra_ids,
        "tenant_discriminator": "client_id",
    }
    if layer in TITLE_NORMALIZED_LAYERS:
        title_map = dict(master["normalized_title_map"])
        title_map["EXTRA-SKU"] = "Extra Product"
        row["normalized_title_map"] = title_map
        row["normalized_content_hash"] = normalized_title_content_hash(title_map)
    rows[layer] = row
    return rows


def runtime_rows_missing_table(snapshot_dir: Path, *, layer: str = "events") -> dict[str, dict[str, Any]]:
    rows = runtime_rows_in_sync_with_snapshot(snapshot_dir)
    rows[layer] = {
        "status": "runtime_missing",
        "reasons": ["runtime_table_missing"],
        "row_count": 0,
    }
    return rows


def runtime_rows_schema_mismatch(snapshot_dir: Path, *, layer: str = "aliases") -> dict[str, dict[str, Any]]:
    rows = runtime_rows_in_sync_with_snapshot(snapshot_dir)
    rows[layer] = {
        "status": "schema_mismatch",
        "reasons": ["missing_columns:canonical_sku"],
        "tenant_discriminator": "client_id",
        "row_count": 0,
    }
    return rows


def primary_id_field(layer: str) -> str:
    return LAYER_ID_FIELDS[layer][0]
