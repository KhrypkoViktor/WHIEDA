"""Cross-layer and business quality checks."""

from __future__ import annotations

import re
from typing import Any

from dqc.schema import Contract, normalize_row, parse_bool, parse_date, parse_number

Issue = dict[str, Any]

ARTIFACTS = ("Nordman", "Traceback", "I need human review")
MEDICAL_WORDS = (
    "лечит",
    "излеч",
    "диагноз",
    "гарантир",
    "назнач",
    "схема лечения",
    "заменяет лекар",
)


def run_quality_checks(
    *,
    layers: dict[str, list[dict[str, Any]]],
    contracts: dict[str, Contract],
    file_paths: dict[str, str],
) -> list[Issue]:
    issues: list[Issue] = []
    products = _index(layers, contracts, "products_prices", "sku")
    product_active = {
        sku: _is_active(row, contracts.get("products_prices"))
        for sku, row in products.items()
    }

    issues.extend(_check_references(layers, contracts, file_paths, products))
    issues.extend(_check_faq_active_text(layers, contracts, file_paths))
    issues.extend(_check_promotion_dates(layers, contracts, file_paths))
    issues.extend(_check_bundle_products(layers, contracts, file_paths, products, product_active))
    issues.extend(_check_price_conflicts(layers, contracts, file_paths))
    issues.extend(_check_artifacts(layers, contracts, file_paths))
    issues.extend(_check_medical_warnings(layers, contracts, file_paths))
    issues.extend(_check_resource_material_type(layers, contracts, file_paths))
    return issues


def _index(
    layers: dict[str, list[dict[str, Any]]],
    contracts: dict[str, Contract],
    layer: str,
    col: str,
) -> dict[str, dict[str, Any]]:
    contract = contracts.get(layer)
    if not contract:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in layers.get(layer, []):
        norm = normalize_row(row, contract)
        key = str(norm.get(col, "")).strip()
        if key:
            out[key] = norm
    return out


def _is_active(row: dict[str, Any], contract: Contract | None) -> bool:
    if not contract:
        return True
    norm = normalize_row(row, contract)
    val = parse_bool(norm.get("active", "true"))
    return True if val is None else val


def _check_references(
    layers: dict[str, list[dict[str, Any]]],
    contracts: dict[str, Contract],
    file_paths: dict[str, str],
    products: dict[str, dict[str, Any]],
) -> list[Issue]:
    issues: list[Issue] = []
    for layer, contract in contracts.items():
        rows = layers.get(layer, [])
        if not rows:
            continue
        for ref in contract.references:
            if ref.get("when_present") and ref["column"] not in contract.columns:
                continue
            target_layer = ref["target_layer"]
            target_col = ref.get("target_column", "sku")
            target_index = products if target_layer == "products_prices" else _index(
                layers, contracts, target_layer, target_col
            )
            for idx, row in enumerate(rows, start=2):
                norm = normalize_row(row, contract)
                val = str(norm.get(ref["column"], "")).strip()
                if not val:
                    if layer in {"certificates", "resource_links"} and ref["column"] == "sku":
                        issues.append(_issue(ref.get("severity", "error"), "missing_product_ref", layer, file_paths.get(layer, ""), idx, ref["column"], "SKU reference is empty"))
                        continue
                    if ref.get("when_present"):
                        continue
                    continue
                if val not in target_index:
                    issues.append(
                        _issue(
                            ref.get("severity", "error"),
                            "unknown_product_ref",
                            layer,
                            file_paths.get(layer, ""),
                            idx,
                            ref["column"],
                            f"Unknown {target_col} {val!r} in {target_layer}",
                        )
                    )
    return issues


def _check_faq_active_text(
    layers: dict[str, list[dict[str, Any]]],
    contracts: dict[str, Contract],
    file_paths: dict[str, str],
) -> list[Issue]:
    issues: list[Issue] = []
    contract = contracts.get("business_faq")
    if not contract:
        return issues
    for idx, row in enumerate(layers.get("business_faq", []), start=2):
        norm = normalize_row(row, contract)
        active = parse_bool(norm.get("active", "true"))
        answer = str(norm.get("answer_text", "")).strip()
        if active is not False and not answer:
            issues.append(
                _issue("error", "faq_missing_answer", "business_faq", file_paths.get("business_faq", ""), idx, "answer_text", "Active FAQ without answer text")
            )
    return issues


def _check_promotion_dates(
    layers: dict[str, list[dict[str, Any]]],
    contracts: dict[str, Contract],
    file_paths: dict[str, str],
) -> list[Issue]:
    issues: list[Issue] = []
    contract = contracts.get("promotions")
    if not contract:
        return issues
    for idx, row in enumerate(layers.get("promotions", []), start=2):
        norm = normalize_row(row, contract)
        start = parse_date(norm.get("start_date"))
        end = parse_date(norm.get("end_date"))
        if not end:
            issues.append(
                _issue("error", "promotion_missing_end", "promotions", file_paths.get("promotions", ""), idx, "end_date", "Promotion missing end date")
            )
        elif start and end and end < start:
            issues.append(
                _issue("error", "promotion_date_order", "promotions", file_paths.get("promotions", ""), idx, "end_date", "End date before start date")
            )
    return issues


def _split_skus(raw: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,;|]", str(raw or "")) if p.strip()]


def _check_bundle_products(
    layers: dict[str, list[dict[str, Any]]],
    contracts: dict[str, Contract],
    file_paths: dict[str, str],
    products: dict[str, dict[str, Any]],
    product_active: dict[str, bool],
) -> list[Issue]:
    issues: list[Issue] = []
    contract = contracts.get("solution_bundles")
    if not contract:
        return issues
    for idx, row in enumerate(layers.get("solution_bundles", []), start=2):
        norm = normalize_row(row, contract)
        bundle_active = parse_bool(norm.get("active", "true"))
        skus = [norm.get("primary_sku", "")] + _split_skus(norm.get("additional_skus", ""))
        for sku in skus:
            sku = str(sku).strip()
            if not sku:
                continue
            if sku not in products:
                issues.append(
                    _issue("error", "bundle_unknown_product", "solution_bundles", file_paths.get("solution_bundles", ""), idx, "primary_sku", f"Unknown SKU {sku!r}")
                )
            elif bundle_active is not False and product_active.get(sku) is False:
                issues.append(
                    _issue("error", "inactive_product_in_active_bundle", "solution_bundles", file_paths.get("solution_bundles", ""), idx, "primary_sku", f"Inactive product {sku!r} in active bundle")
                )
    return issues


def _check_price_conflicts(
    layers: dict[str, list[dict[str, Any]]],
    contracts: dict[str, Contract],
    file_paths: dict[str, str],
) -> list[Issue]:
    issues: list[Issue] = []
    contract = contracts.get("products_prices")
    if not contract or not contract.price_conflict_keys:
        return issues
    keys = contract.price_conflict_keys
    bucket: dict[tuple[str, ...], list[tuple[int, float | None, bool]]] = {}
    for idx, row in enumerate(layers.get("products_prices", []), start=2):
        norm = normalize_row(row, contract)
        active = parse_bool(norm.get("active", "true"))
        if active is False:
            continue
        key = tuple(str(norm.get(k, "")).strip().lower() for k in keys)
        price_val, _ = parse_number(norm.get("retail_price_rub") or norm.get("partner_price_rub") or norm.get("partner_points"))
        bucket.setdefault(key, []).append((idx, price_val, active is not False))
    for key, entries in bucket.items():
        if len(entries) < 2:
            continue
        values = {e[1] for e in entries if e[1] is not None}
        if len(values) > 1:
            issues.append(
                _issue(
                    "error",
                    "price_conflict",
                    "products_prices",
                    file_paths.get("products_prices", ""),
                    entries[0][0],
                    ",".join(keys),
                    f"Conflicting active prices for {key}: {values}",
                )
            )
    return issues


def _check_artifacts(
    layers: dict[str, list[dict[str, Any]]],
    contracts: dict[str, Contract],
    file_paths: dict[str, str],
) -> list[Issue]:
    issues: list[Issue] = []
    for layer, rows in layers.items():
        contract = contracts.get(layer)
        if not contract:
            continue
        for idx, row in enumerate(rows, start=2):
            blob = " ".join(str(v) for v in normalize_row(row, contract).values())
            for artifact in ARTIFACTS:
                if artifact.lower() in blob.lower():
                    issues.append(
                        _issue("error", "suspicious_artifact", layer, file_paths.get(layer, ""), idx, "", f"Found artifact {artifact!r}")
                    )
    return issues


def _check_medical_warnings(
    layers: dict[str, list[dict[str, Any]]],
    contracts: dict[str, Contract],
    file_paths: dict[str, str],
) -> list[Issue]:
    issues: list[Issue] = []
    scan_layers = {"product_cards", "solution_bundles"}
    for layer in scan_layers:
        contract = contracts.get(layer)
        if not contract:
            continue
        fields = contract.medical_scan_fields or []
        for idx, row in enumerate(layers.get(layer, []), start=2):
            norm = normalize_row(row, contract)
            blob = " ".join(str(norm.get(f, "")) for f in fields)
            for word in MEDICAL_WORDS:
                if word in blob.lower():
                    issues.append(
                        _issue("warning", "medical_language", layer, file_paths.get(layer, ""), idx, "", f"Medical wording detected: {word!r}")
                    )
    return issues


def _check_resource_material_type(
    layers: dict[str, list[dict[str, Any]]],
    contracts: dict[str, Contract],
    file_paths: dict[str, str],
) -> list[Issue]:
    issues: list[Issue] = []
    contract = contracts.get("resource_links")
    if not contract:
        return issues
    for idx, row in enumerate(layers.get("resource_links", []), start=2):
        norm = normalize_row(row, contract)
        rtype = str(norm.get("resource_type", "")).strip().lower()
        if not rtype:
            issues.append(
                _issue("error", "missing_material_type", "resource_links", file_paths.get("resource_links", ""), idx, "resource_type", "Photo/video/certificate link without material type")
            )
    return issues


def _issue(severity: str, check: str, layer: str, file_path: str, row: int, field: str, message: str) -> Issue:
    return {
        "severity": severity,
        "check": check,
        "layer": layer,
        "file": file_path,
        "row": row,
        "field": field,
        "message": message,
    }
