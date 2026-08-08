"""Schema contract validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dqc.loader import load_table

Issue = dict[str, Any]

BOOL_TRUE = {"true", "1", "yes", "y", "да", "active", "активен"}
BOOL_FALSE = {"false", "0", "no", "n", "нет", "inactive", "неактивен"}


@dataclass
class Contract:
    layer: str
    raw: dict[str, Any]
    id_fields: list[str] = field(default_factory=list)
    columns: dict[str, Any] = field(default_factory=dict)
    unique: list[list[str]] = field(default_factory=list)
    references: list[dict[str, Any]] = field(default_factory=list)
    medical_scan_fields: list[str] = field(default_factory=list)
    price_conflict_keys: list[str] = field(default_factory=list)

    @classmethod
    def from_file(cls, path) -> "Contract":
        import json
        from pathlib import Path

        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            layer=data["layer"],
            raw=data,
            id_fields=list(data.get("id_fields") or []),
            columns=dict(data.get("columns") or {}),
            unique=list(data.get("unique") or []),
            references=list(data.get("references") or []),
            medical_scan_fields=list(data.get("medical_scan_fields") or []),
            price_conflict_keys=list(data.get("price_conflict_keys") or []),
        )


def _norm_header(name: str) -> str:
    return re.sub(r"\s+", "_", str(name or "").strip().lower())


def normalize_row(row: dict[str, Any], contract: Contract) -> dict[str, Any]:
    header_map: dict[str, str] = {}
    for col, spec in contract.columns.items():
        header_map[_norm_header(col)] = col
        for alias in spec.get("aliases") or []:
            header_map[_norm_header(alias)] = col

    out: dict[str, Any] = {}
    for key, value in row.items():
        target = header_map.get(_norm_header(key))
        if target:
            out[target] = value if value is not None else ""
        else:
            out[key] = value if value is not None else ""
    return out


def parse_number(raw: Any) -> tuple[float | None, bool]:
    text = str(raw or "").strip()
    if not text:
        return None, False
    text = text.replace(" ", "").replace(",", ".")
    try:
        return float(text), True
    except ValueError:
        return None, False


def parse_bool(raw: Any) -> bool | None:
    text = str(raw or "").strip().lower()
    if not text:
        return None
    if text in BOOL_TRUE:
        return True
    if text in BOOL_FALSE:
        return False
    return None


def parse_date(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def validate_schema(
    *,
    layer: str,
    file_path: str,
    rows: list[dict[str, Any]],
    contract: Contract,
) -> list[Issue]:
    issues: list[Issue] = []
    if not rows:
        issues.append(
            {
                "severity": "warning",
                "check": "empty_layer",
                "layer": layer,
                "file": file_path,
                "row": 0,
                "field": "",
                "message": "Layer file is empty",
            }
        )
        return issues

    normalized = [normalize_row(r, contract) for r in rows]

    for idx, row in enumerate(normalized, start=2):
        for col, spec in contract.columns.items():
            raw_val = row.get(col, "")
            if spec.get("required") and str(raw_val).strip() == "":
                issues.append(
                    _issue("error", "required_field_empty", layer, file_path, idx, col, "Required field is empty")
                )
            col_type = spec.get("type", "string")
            if str(raw_val).strip() == "":
                continue
            if col_type == "number":
                num, ok = parse_number(raw_val)
                if not ok:
                    issues.append(
                        _issue("error", "invalid_number", layer, file_path, idx, col, f"Not a number: {raw_val!r}")
                    )
                elif num is not None:
                    if spec.get("min") is not None and num < float(spec["min"]):
                        issues.append(
                            _issue("error", "number_below_min", layer, file_path, idx, col, f"Value {num} below min")
                        )
                    if not spec.get("allow_zero", True) and num == 0:
                        issues.append(
                            _issue("error", "zero_forbidden", layer, file_path, idx, col, "Zero is not allowed")
                        )
            elif col_type == "boolean":
                if parse_bool(raw_val) is None:
                    issues.append(
                        _issue("error", "invalid_boolean", layer, file_path, idx, col, f"Not a boolean: {raw_val!r}")
                    )
            elif col_type == "url":
                if not str(raw_val).startswith("https://"):
                    issues.append(
                        _issue("error", "invalid_url", layer, file_path, idx, col, "URL must start with https://")
                    )
            elif col_type == "date":
                if parse_date(raw_val) is None:
                    issues.append(
                        _issue("error", "invalid_date", layer, file_path, idx, col, f"Invalid date: {raw_val!r}")
                    )
            enum = spec.get("enum")
            if enum and str(raw_val).strip().lower() not in {str(x).lower() for x in enum}:
                issues.append(
                    _issue("warning", "enum_violation", layer, file_path, idx, col, f"Value not in enum {enum}")
                )

    for key_group in contract.unique:
        seen: dict[tuple[str, ...], int] = {}
        for idx, row in enumerate(normalized, start=2):
            key = tuple(str(row.get(k, "")).strip().lower() for k in key_group)
            if all(not part for part in key):
                continue
            if key in seen:
                issues.append(
                    _issue(
                        "error",
                        "duplicate_key",
                        layer,
                        file_path,
                        idx,
                        ",".join(key_group),
                        f"Duplicate key {key} (first row {seen[key]})",
                    )
                )
            else:
                seen[key] = idx

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
