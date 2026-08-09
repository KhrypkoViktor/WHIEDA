"""Orchestrate read-only WHIEDA asset inventory scan."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from inventory.constants import (
    DATA_TO_FEATURE_NAME,
    FEATURE_MATRIX_NAME,
    MANIFEST_NAME,
    REPO_ROOT,
    SKIP_PATH_PARTS,
    SQL_CORPUS,
)
from inventory.data_to_feature import write_data_to_feature_map
from inventory.distillate_layers import DISTILLATE_BY_FILENAME
from inventory.feature_matrix import write_feature_matrix
from inventory.manifest import ManifestRow, build_manifest_rows, write_manifest_csv
from inventory.readers import read_jsonl_info, read_tsv_info


@dataclass
class ScanResult:
    manifest_path: Path
    feature_matrix_path: Path
    data_map_path: Path
    manifest_rows: list[ManifestRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks_passed: int = 0
    checks_failed: int = 0


def _validate_distillate_counts(rows: list[ManifestRow], errors: list[str]) -> int:
    passed = 0
    for row in rows:
        if row.source_kind != "distillate_corpus":
            continue
        filename = Path(row.path).name
        if filename not in DISTILLATE_BY_FILENAME:
            continue
        tsv_path = REPO_ROOT / row.path.replace("/", "\\")
        if not tsv_path.is_file():
            errors.append(f"missing distillate file: {row.path}")
            continue
        info = read_tsv_info(tsv_path)
        if str(info.data_rows) != row.records_or_lines:
            errors.append(
                f"row count mismatch {filename}: manifest={row.records_or_lines} file={info.data_rows}"
            )
        else:
            passed += 1
        if not info.header:
            errors.append(f"TSV missing header: {filename}")
        elif info.data_rows and info.non_empty_rows == 0:
            errors.append(f"TSV has data rows but all empty: {filename}")
        else:
            passed += 1
    return passed


def _validate_jsonl_assets(rows: list[ManifestRow], errors: list[str]) -> int:
    passed = 0
    for row in rows:
        if row.format != "jsonl":
            continue
        path = REPO_ROOT / row.path.replace("/", "\\")
        if not path.is_file():
            errors.append(f"missing jsonl: {row.path}")
            continue
        info = read_jsonl_info(path)
        if info.invalid_lines:
            errors.append(f"invalid jsonl lines in {row.path}: {info.invalid_lines}")
        if str(info.valid_objects) != row.records_or_lines:
            errors.append(
                f"jsonl count mismatch {row.path}: manifest={row.records_or_lines} file={info.valid_objects}"
            )
        else:
            passed += 1
    return passed


def _check_no_backup_duplicates(rows: list[ManifestRow], warnings: list[str]) -> int:
    passed = 0
    paths = [r.path for r in rows]
    for path in paths:
        if any(part in SKIP_PATH_PARTS for part in Path(path).parts):
            warnings.append(f"backup-like path in manifest (should not happen): {path}")
        else:
            passed += 1
    ids = [r.asset_id for r in rows]
    if len(ids) != len(set(ids)):
        warnings.append("duplicate asset_id values in manifest")
    else:
        passed += 1
    return passed


def run_inventory_scan(
    output_dir: Path | None = None,
    *,
    repo_root: Path | None = None,
) -> ScanResult:
    root = repo_root or REPO_ROOT
    out = output_dir or root
    errors: list[str] = []
    warnings: list[str] = []

    manifest_rows = build_manifest_rows()
    if not SQL_CORPUS.is_dir():
        warnings.append(f"SQL corpus folder missing (gitignored RAG may be absent): {SQL_CORPUS}")

    checks_passed = 0
    checks_passed += _validate_distillate_counts(manifest_rows, errors)
    checks_passed += _validate_jsonl_assets(manifest_rows, errors)
    checks_passed += _validate_review_flags(manifest_rows, errors)
    checks_passed += _check_no_backup_duplicates(manifest_rows, warnings)

    manifest_path = out / MANIFEST_NAME
    feature_path = out / FEATURE_MATRIX_NAME
    data_path = out / DATA_TO_FEATURE_NAME

    write_manifest_csv(manifest_path, manifest_rows)
    write_feature_matrix(feature_path)
    write_data_to_feature_map(data_path, len(manifest_rows))

    return ScanResult(
        manifest_path=manifest_path,
        feature_matrix_path=feature_path,
        data_map_path=data_path,
        manifest_rows=manifest_rows,
        errors=errors,
        warnings=warnings,
        checks_passed=checks_passed,
        checks_failed=len(errors),
    )


def _validate_review_flags(rows: list[ManifestRow], errors: list[str]) -> int:
    passed = 0
    blocked_layers = {
        "07_TESTIMONIALS.tsv",
        "10_MEDICAL_REVIEW_QUEUE.tsv",
        "15_SAFETY_SIGNALS.tsv",
    }
    for row in rows:
        if row.source_kind != "distillate_corpus":
            continue
        name = Path(row.path).name
        if name in blocked_layers and row.review_state not in {"blocked_raw", "review_required"}:
            errors.append(f"{name} must have review_state blocked_raw or review_required")
        elif name in blocked_layers:
            passed += 1
        if name in {"07_TESTIMONIALS.tsv", "15_SAFETY_SIGNALS.tsv"} and row.user_visible != "no":
            errors.append(f"{name} must not be user_visible")
        elif name in {"07_TESTIMONIALS.tsv", "15_SAFETY_SIGNALS.tsv"}:
            passed += 1
    return passed
