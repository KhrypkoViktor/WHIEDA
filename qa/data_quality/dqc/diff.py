"""Diff current fingerprints against baseline."""

from __future__ import annotations

from typing import Any


def diff_baselines(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    if not previous:
        return {"status": "no_baseline", "layers": {}}

    result: dict[str, Any] = {"status": "ok", "layers": {}}
    prev_layers = previous.get("layers") or {}
    curr_layers = current.get("layers") or {}

    for layer, curr in curr_layers.items():
        prev = prev_layers.get(layer) or {"id_index": {}}
        prev_ids = set((prev.get("id_index") or {}).keys())
        curr_ids = set((curr.get("id_index") or {}).keys())
        added = sorted(curr_ids - prev_ids)
        removed = sorted(prev_ids - curr_ids)
        changed: list[str] = []
        price_changed: list[str] = []
        safety_changed: list[str] = []
        for row_id in sorted(curr_ids & prev_ids):
            if (prev.get("id_index") or {}).get(row_id) != (curr.get("id_index") or {}).get(row_id):
                changed.append(row_id)
                if layer in {"products_prices"}:
                    price_changed.append(row_id)
                if layer in {"product_cards", "solution_bundles"}:
                    safety_changed.append(row_id)
        result["layers"][layer] = {
            "added": added,
            "removed": removed,
            "changed": changed,
            "price_changed": price_changed,
            "safety_changed": safety_changed,
            "row_count_before": prev.get("row_count", 0),
            "row_count_after": curr.get("row_count", 0),
        }
    return result
