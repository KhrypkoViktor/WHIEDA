"""Tests for WHIEDA master/runtime integrity V2 (no network, no real DSN)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "n8n" / "current"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_fixtures import build_invalid_tsv_snapshot, build_valid_snapshot  # noqa: E402
from fixture_runtime import (  # noqa: E402
    runtime_rows_extra,
    runtime_rows_in_sync_with_snapshot,
    runtime_rows_missing_table,
    runtime_rows_schema_mismatch,
    runtime_rows_stale,
)
from whieda_master_integrity_lib import (  # noqa: E402
    ID_LIST_CAP,
    alias_business_key_token,
    cap_id_list,
    compare_layer_runtime_v2,
    compare_master_runtime_v2,
    detect_tenant_discriminator,
    find_newest_valid_snapshot,
    format_runtime_integrity_markdown,
    master_runtime_layer_stats,
    normalize_display_title,
    normalized_title_content_hash,
    redact_sensitive_text,
    retention_plan_v2,
)


@pytest.fixture()
def valid_snapshot(tmp_path: Path) -> Path:
    return build_valid_snapshot(tmp_path / "valid")


def test_newest_valid_snapshot_selection(valid_snapshot: Path, tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    older = root / "20260801T100000Z"
    newer = root / "20260811T120000Z"
    build_valid_snapshot(older)
    build_valid_snapshot(newer)
    selected, validation = find_newest_valid_snapshot(root)
    assert selected.name == "20260811T120000Z"
    assert validation["valid"] is True
    assert validation["selected_reason"] == "newest_valid"


def test_invalid_latest_falls_back_to_prior_valid(valid_snapshot: Path, tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    good = root / "20260810T100000Z"
    bad = root / "20260811T120000Z"
    build_valid_snapshot(good)
    build_invalid_tsv_snapshot(bad)
    selected, validation = find_newest_valid_snapshot(root)
    assert selected.name == "20260810T100000Z"
    assert validation["selected_reason"] == "fallback_after_invalid_newer"
    assert validation["skipped_newer_invalid"]


def test_readonly_guard_rejects_writable_transaction() -> None:
    from whieda_master_integrity_lib import assert_connection_readonly

    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = ("off",)
    conn.cursor.return_value.__enter__.return_value = cursor

    with pytest.raises(RuntimeError, match="read-only"):
        assert_connection_readonly(conn)


def test_tenant_discriminator_variants() -> None:
    assert detect_tenant_discriminator(["client_id", "sku"]) == "client_id"
    assert detect_tenant_discriminator(["project_id", "sku"]) == "project_id"
    assert detect_tenant_discriminator(["sku", "title"]) is None


def test_in_sync_stale_extra_missing_schema(valid_snapshot: Path) -> None:
    in_sync = compare_master_runtime_v2(valid_snapshot, runtime_rows_in_sync_with_snapshot(valid_snapshot))
    assert in_sync["overall_status"] == "safe"
    assert in_sync["layers"]["products"]["status"] == "in_sync"

    stale = compare_master_runtime_v2(valid_snapshot, runtime_rows_stale(valid_snapshot))
    assert stale["layers"]["products"]["status"] == "runtime_stale"
    assert stale["overall_status"] == "review_required"

    extra = compare_master_runtime_v2(valid_snapshot, runtime_rows_extra(valid_snapshot))
    assert extra["layers"]["products"]["status"] == "runtime_extra"

    missing = compare_master_runtime_v2(
        valid_snapshot,
        runtime_rows_missing_table(valid_snapshot, layer="products"),
    )
    assert missing["layers"]["products"]["status"] == "runtime_missing"
    assert missing["overall_status"] == "blocking"

    schema = compare_master_runtime_v2(valid_snapshot, runtime_rows_schema_mismatch(valid_snapshot))
    assert schema["layers"]["aliases"]["status"] == "schema_mismatch"
    assert schema["overall_status"] == "blocking"


def test_empty_product_details_and_stale_runtime(valid_snapshot: Path) -> None:
    master = master_runtime_layer_stats(valid_snapshot, "product_details")
    assert master["rows"] == 0
    report = compare_layer_runtime_v2(
        "product_details",
        master,
        {"row_count": 0, "content_hash": master["content_hash"], "ids": []},
    )
    assert report["status"] == "in_sync"

    stale = compare_layer_runtime_v2(
        "product_details",
        {"rows": 2, "content_hash": "abc", "ids": {"d1", "d2"}},
        {"row_count": 0, "content_hash": "", "ids": []},
    )
    assert stale["status"] == "runtime_missing"


def test_capped_id_list_preserves_totals() -> None:
    ids = [f"id-{index:03d}" for index in range(40)]
    capped = cap_id_list(ids, cap=ID_LIST_CAP)
    assert capped["total"] == 40
    assert len(capped["values"]) == ID_LIST_CAP
    assert capped["truncated"] is True


def test_partners_ref_not_guessed(valid_snapshot: Path) -> None:
    report = compare_master_runtime_v2(valid_snapshot, runtime_rows_in_sync_with_snapshot(valid_snapshot))
    partners = report["layers"]["partners_ref"]
    assert partners["status"] == "not_runtime_backed"
    assert "legacy_partner" in partners["reason"]


def test_report_has_no_dsn_or_secret(valid_snapshot: Path) -> None:
    report = compare_master_runtime_v2(valid_snapshot, runtime_rows_in_sync_with_snapshot(valid_snapshot))
    report["read_only_proof"] = {"transaction_read_only": "on"}
    report["run_id"] = "test-run"
    text = format_runtime_integrity_markdown(report)
    blob = json.dumps(report, default=str) + text
    assert "postgresql://" not in blob.lower()
    assert "password" not in blob.lower()
    redacted = redact_sensitive_text("failed postgresql://u:secret-pass@host/db token=abc")
    assert "secret-pass" not in redacted


def test_retention_plan_preserves_invalid_and_blocking(tmp_path: Path) -> None:
    root = tmp_path / "snapshots"
    integrity = tmp_path / "integrity"
    good = root / "20260801T120000Z"
    bad = root / "20260802T120000Z"
    build_valid_snapshot(good)
    build_invalid_tsv_snapshot(bad)

    blocking_dir = integrity / "run-blocking"
    blocking_dir.mkdir(parents=True)
    (blocking_dir / "report.json").write_text(
        json.dumps({"overall_status": "blocking", "snapshot_id": good.name}),
        encoding="utf-8",
    )

    now = datetime(2026, 8, 11, tzinfo=timezone.utc)
    plan = retention_plan_v2(root, now=now, integrity_root=integrity)
    assert "removed" not in plan
    assert good.name not in plan["would_remove"]
    assert bad.name not in plan["would_remove"]
    manual = [entry for entry in plan["entries"] if entry.get("manual_review")]
    assert any(entry["snapshot_id"] == bad.name for entry in manual)
    assert any(entry["snapshot_id"] == good.name for entry in manual)


def _alias_keys(count: int) -> set[tuple[str, str]]:
    return {(f"alias-{index}", f"SKU-{index:03d}") for index in range(1, count + 1)}


def test_alias_raw_rows_120_unique_118_runtime_118_not_stale() -> None:
    dup_key = ("dup-alias", "SKU-001")
    business_keys = _alias_keys(117) | {dup_key}
    master = {
        "rows": 120,
        "master_raw_rows": 120,
        "master_unique_rows": 118,
        "business_keys": business_keys,
        "duplicate_rows_in_master": 2,
        "duplicate_examples": [alias_business_key_token(dup_key)],
        "content_hash": "master-hash",
    }
    runtime = {
        "row_count": 118,
        "unique_business_row_count": 118,
        "business_keys": business_keys,
        "content_hash": "runtime-hash",
        "tenant_discriminator": "client_id",
    }
    report = compare_layer_runtime_v2("aliases", master, runtime)
    assert report["status"] == "in_sync"
    assert report["master_raw_rows"] == 120
    assert report["master_unique_rows"] == 118
    assert report["runtime_unique_rows"] == 118
    assert report["duplicate_rows_in_master"] == 2
    assert "master_duplicate_rows" in report["reasons"]


def test_alias_duplicate_examples_are_reported() -> None:
    dup_key = ("dup-alias", "SKU-002")
    master = {
        "rows": 3,
        "master_raw_rows": 3,
        "master_unique_rows": 2,
        "business_keys": {("alias-a", "SKU-001"), dup_key},
        "duplicate_rows_in_master": 1,
        "duplicate_examples": [alias_business_key_token(dup_key)],
        "content_hash": "master-hash",
    }
    runtime = {
        "row_count": 2,
        "unique_business_row_count": 2,
        "business_keys": master["business_keys"],
        "content_hash": "runtime-hash",
    }
    report = compare_layer_runtime_v2("aliases", master, runtime)
    assert report["duplicate_examples"] == [alias_business_key_token(dup_key)]


def test_quoted_ba_gua_product_title_is_equivalent() -> None:
    normalized = normalize_display_title('МИНИСАУНА "БА-ГУА"')
    assert normalized == normalize_display_title("МИНИСАУНА БА-ГУА")
    title_map = {"M014-00": normalized}
    master = {
        "rows": 1,
        "content_hash": "raw-master",
        "normalized_title_map": title_map,
        "normalized_content_hash": normalized_title_content_hash(title_map),
    }
    runtime = {
        "row_count": 1,
        "content_hash": "raw-runtime",
        "normalized_title_map": title_map,
        "normalized_content_hash": normalized_title_content_hash(title_map),
        "tenant_discriminator": "client_id",
    }
    report = compare_layer_runtime_v2("products", master, runtime)
    assert report["status"] == "in_sync"
    assert "equivalent_after_normalization" in report["reasons"]


def test_quoted_resource_title_is_equivalent() -> None:
    title_map = {"res-ba-gua": normalize_display_title('Ба-Гуа "image"')}
    runtime_map = {"res-ba-gua": normalize_display_title("Ба-Гуа image")}
    master = {
        "rows": 1,
        "content_hash": "raw-master",
        "normalized_title_map": title_map,
        "normalized_content_hash": normalized_title_content_hash(title_map),
    }
    runtime = {
        "row_count": 1,
        "content_hash": "raw-runtime",
        "normalized_title_map": runtime_map,
        "normalized_content_hash": normalized_title_content_hash(runtime_map),
    }
    report = compare_layer_runtime_v2("resources", master, runtime)
    assert report["status"] == "in_sync"
    assert "equivalent_after_normalization" in report["reasons"]


def test_true_title_mismatch_after_normalization() -> None:
    master_map = {"M014-00": "МИНИСАУНА БА-ГУА"}
    runtime_map = {"M014-00": "МИНИСАУНА ДРУГАЯ"}
    master = {
        "rows": 1,
        "content_hash": "raw-master",
        "normalized_title_map": master_map,
        "normalized_content_hash": normalized_title_content_hash(master_map),
    }
    runtime = {
        "row_count": 1,
        "content_hash": "raw-runtime",
        "normalized_title_map": runtime_map,
        "normalized_content_hash": normalized_title_content_hash(runtime_map),
    }
    report = compare_layer_runtime_v2("products", master, runtime)
    assert report["status"] == "runtime_stale"
    assert "true_content_mismatch_after_normalization" in report["reasons"]
