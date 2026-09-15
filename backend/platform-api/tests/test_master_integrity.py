"""Unit tests for WHIEDA master integrity helpers."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "n8n" / "current"))
sys.path.insert(0, str(ROOT / "qa" / "master_integrity"))
# qa/tenant_canary_preflight ships a build_fixtures module too; drop its cached copy
sys.modules.pop("build_fixtures", None)

from build_fixtures import (  # noqa: E402
    build_collapsed_products_snapshot,
    build_header_mutation_snapshot,
    build_valid_snapshot,
)
from whieda_master_integrity_lib import (  # noqa: E402
    compare_master_to_runtime,
    compare_snapshots,
    header_hash,
    redact_sensitive_text,
    retention_plan,
    validate_snapshot_dir,
)


@pytest.fixture()
def valid_snapshot(tmp_path: Path) -> Path:
    return build_valid_snapshot(tmp_path / "valid")


def test_valid_snapshot_has_20_layers(valid_snapshot: Path) -> None:
    result = validate_snapshot_dir(valid_snapshot)
    assert result["valid"] is True
    assert len(result["layers"]) == 20


def test_empty_product_details_allowed(valid_snapshot: Path) -> None:
    result = validate_snapshot_dir(valid_snapshot)
    assert result["layers"]["product_details"]["rows"] == 0
    assert result["valid"] is True


def test_header_hash_stable() -> None:
    assert header_hash(["sku", "name"]) == header_hash(["sku", "name"])


def test_redact_dsn() -> None:
    text = redact_sensitive_text("failed postgresql://admin:hunter2@127.0.0.1:5432/db password=secret")
    assert "hunter2" not in text
    assert "secret" not in text or "REDACTED" in text


def test_compare_detects_header_mutation(valid_snapshot: Path, tmp_path: Path) -> None:
    mutated = build_header_mutation_snapshot(tmp_path / "mutated")
    report = compare_snapshots(valid_snapshot, mutated)
    assert report["layers"]["aliases"]["classification"] == "review_required"


def test_compare_detects_critical_collapse(valid_snapshot: Path, tmp_path: Path) -> None:
    collapsed = build_collapsed_products_snapshot(tmp_path / "collapsed")
    report = compare_snapshots(valid_snapshot, collapsed)
    assert report["layers"]["products"]["classification"] == "blocking"


def test_retention_dry_run_no_removed_key(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    snap = root / "20260801T120000Z"
    build_valid_snapshot(snap)
    plan = retention_plan(root, now=datetime(2026, 8, 10, tzinfo=timezone.utc))
    assert "would_remove" in plan
    assert "removed" not in plan


def test_runtime_parity_stale_on_count_mismatch(valid_snapshot: Path) -> None:
    report = compare_master_to_runtime(
        valid_snapshot,
        {"products": {"row_count": 20, "content_hash": "wrong-hash"}},
    )
    assert report["layers"]["products"]["status"] == "stale_runtime"


def test_manifest_written_with_header_hash(valid_snapshot: Path) -> None:
    manifest = json.loads((valid_snapshot / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["layers"]["products"]["header_hash"]
