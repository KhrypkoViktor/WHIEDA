"""Fixture runtime rows for master/runtime integrity tests (no network, no DSN)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "n8n" / "current"))

from whieda_master_integrity_lib import (  # noqa: E402
    LAYER_ID_FIELDS,
    RUNTIME_TABLES,
    master_runtime_layer_stats,
)


def runtime_rows_in_sync_with_snapshot(snapshot_dir: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for layer, (_, fields) in RUNTIME_TABLES.items():
        master = master_runtime_layer_stats(snapshot_dir, layer)
        rows[layer] = {
            "row_count": master["rows"],
            "content_hash": master["content_hash"],
            "ids": sorted(master["ids"]),
            "tenant_discriminator": "client_id",
        }
    return rows


def runtime_rows_stale(snapshot_dir: Path, *, layer: str = "products") -> dict[str, dict[str, Any]]:
    rows = runtime_rows_in_sync_with_snapshot(snapshot_dir)
    master = master_runtime_layer_stats(snapshot_dir, layer)
    stale_ids = sorted(master["ids"])
    if stale_ids:
        stale_ids[0] = f"STALE-{stale_ids[0]}"
    rows[layer] = {
        "row_count": master["rows"],
        "content_hash": "deadbeef",
        "ids": stale_ids,
        "tenant_discriminator": "client_id",
    }
    return rows


def runtime_rows_extra(snapshot_dir: Path, *, layer: str = "products") -> dict[str, dict[str, Any]]:
    rows = runtime_rows_in_sync_with_snapshot(snapshot_dir)
    master = master_runtime_layer_stats(snapshot_dir, layer)
    extra_ids = sorted(set(master["ids"]) | {"EXTRA-SKU"})
    rows[layer] = {
        "row_count": len(extra_ids),
        "content_hash": master["content_hash"],
        "ids": extra_ids,
        "tenant_discriminator": "client_id",
    }
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
