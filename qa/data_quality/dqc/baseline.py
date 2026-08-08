"""Baseline fingerprints (hashes only, no raw copies)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dqc.schema import Contract, normalize_row


def _row_fingerprint(row: dict[str, Any], id_fields: list[str]) -> str:
    payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_layer_baseline(
    *,
    layer: str,
    rows: list[dict[str, Any]],
    contract: Contract,
) -> dict[str, Any]:
    normalized = [normalize_row(r, contract) for r in rows]
    id_fields = contract.id_fields or ["id"]
    id_index: dict[str, str] = {}
    for row in normalized:
        row_id = "|".join(str(row.get(f, "")).strip() for f in id_fields)
        if row_id:
            id_index[row_id] = _row_fingerprint(row, id_fields)
    all_hash = hashlib.sha256(
        json.dumps(sorted(id_index.items()), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return {
        "row_count": len(normalized),
        "ids_hash": all_hash,
        "id_index": id_index,
    }


def build_baseline(*, layers: dict[str, Any], contracts: dict[str, Contract]) -> dict[str, Any]:
    out = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "layers": {},
    }
    for layer, rows in layers.items():
        contract = contracts.get(layer)
        if not contract:
            continue
        out["layers"][layer] = build_layer_baseline(layer=layer, rows=rows, contract=contract)
    return out


def save_baseline(path: Path, baseline: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")


def load_baseline(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
