"""Run modes and sync gate for data quality validation."""

from __future__ import annotations

from typing import Any

RELEASE_REQUIRED: frozenset[str] = frozenset(
    {
        "products_prices",
        "product_cards",
        "product_aliases",
        "resource_links",
    }
)

VALID_MODES = frozenset({"dev", "release", "full"})


def manifest_layers(manifest: dict[str, Any]) -> list[str]:
    return [src["layer"] for src in manifest.get("sources", [])]


def required_layers_for_mode(mode: str, manifest: dict[str, Any]) -> set[str]:
    if mode not in VALID_MODES:
        raise ValueError(f"Unknown mode {mode!r}; expected dev|release|full")
    if mode == "dev":
        return set()
    if mode == "release":
        return set(RELEASE_REQUIRED)
    return set(manifest_layers(manifest))


def evaluate_gate(
    *,
    mode: str,
    manifest: dict[str, Any],
    missing: list[dict[str, Any]],
    found_layers: list[str],
    validation_status: str,
) -> dict[str, Any]:
    """Apply mode-specific source requirements and compute sync gate."""
    required = required_layers_for_mode(mode, manifest)
    missing_layers = [item["layer"] for item in missing]
    missing_required = sorted(layer for layer in missing_layers if layer in required)

    gate_fail = False
    gate_warn = False

    if mode == "dev" and missing_layers:
        gate_warn = True
    elif mode == "release" and missing_required:
        gate_fail = True
    elif mode == "full" and missing_layers:
        gate_fail = True

    if gate_fail:
        gate_status = "FAIL"
    elif gate_warn or validation_status == "WARN":
        gate_status = "WARN"
    elif validation_status == "FAIL":
        gate_status = "FAIL"
    else:
        gate_status = "PASS"

    can_sync = gate_status != "FAIL" and validation_status != "FAIL"

    return {
        "mode": mode,
        "required_layers": sorted(required),
        "found_layers": sorted(found_layers),
        "missing_layers": sorted(missing_layers),
        "missing_required_layers": missing_required,
        "can_sync": can_sync,
        "gate_status": gate_status,
        "status": gate_status,
    }


def baseline_allowed(*, gate: dict[str, Any], layer_stats: dict[str, int]) -> tuple[bool, str]:
    if gate.get("status") == "FAIL" or gate.get("gate_status") == "FAIL":
        return False, "baseline blocked: validation status is FAIL"
    total_rows = sum(layer_stats.values())
    if total_rows <= 0:
        return False, "baseline blocked: no data rows loaded"
    if not layer_stats:
        return False, "baseline blocked: empty dataset"
    return True, ""
