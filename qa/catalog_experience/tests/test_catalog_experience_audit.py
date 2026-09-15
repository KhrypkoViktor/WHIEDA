"""Tests for offline catalog experience audit."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
AUDIT_DIR = ROOT / "qa" / "catalog_experience"
sys.path.insert(0, str(AUDIT_DIR))
sys.path.insert(0, str(ROOT / "backend" / "platform-api"))

from audit_lib import (  # noqa: E402
    SnapshotValidationError,
    build_product_rows,
    detect_renderer_defects,
    find_latest_valid_snapshot,
    grade_product,
    load_snapshot,
    verify_snapshot,
)
from run_catalog_experience_audit import run_audit  # noqa: E402


@pytest.fixture(scope="module")
def snapshot_dir() -> Path:
    return find_latest_valid_snapshot()


@pytest.fixture(scope="module")
def snapshot(snapshot_dir: Path):
    return load_snapshot(snapshot_dir)


@pytest.fixture(scope="module")
def rows(snapshot):
    return build_product_rows(snapshot)


def test_latest_snapshot_verifies(snapshot_dir: Path):
    manifest = verify_snapshot(snapshot_dir)
    assert manifest["layers"]["products"]["rows"] == 40


def test_matrix_has_all_products(rows):
    assert len(rows) == 40
    assert {row.sku for row in rows if row.has_card}
    assert sum(1 for row in rows if not row.has_card) == 17


def test_existing_cards_not_blocked(rows):
    for row in rows:
        if row.has_card:
            assert row.presentation_grade in {"showcase_ready", "usable", "thin"}
            assert row.rendered_length > 0
            assert "**" not in row.rendered_preview or row.rendered_preview == ""


def test_activator_showcase_ready(rows):
    activator = next(row for row in rows if row.sku == "M015-00")
    assert activator.presentation_grade == "showcase_ready"
    assert activator.has_primary_photo
    assert activator.alias_count >= 1


def test_blocked_without_card(rows):
    blocked = [row for row in rows if not row.has_card]
    assert blocked
    assert all(row.presentation_grade == "blocked" for row in blocked)


def test_renderer_defect_detection():
    defects = detect_renderer_defects(
        "**bad**",
        product={"sku": "X", "canonical_name": "Name"},
        card={"canonical_name": "Name", "what_it_is": "text"},
        has_primary_photo=True,
        image_count=1,
    )
    assert "literal_markdown_stars" in defects


def test_runner_writes_artifacts(tmp_path: Path, snapshot_dir: Path):
    summary = run_audit(
        snapshot_dir=snapshot_dir,
        matrix_path=tmp_path / "matrix.csv",
        backlog_path=tmp_path / "backlog.md",
        showcase_path=tmp_path / "showcase.md",
        report_path=tmp_path / "report.md",
    )
    assert summary["products"] == 40
    matrix = list(csv.DictReader((tmp_path / "matrix.csv").open(encoding="utf-8")))
    assert len(matrix) == 40
    assert (tmp_path / "backlog.md").read_text(encoding="utf-8").startswith("# Catalog Experience")
    assert (tmp_path / "showcase.md").read_text(encoding="utf-8").count("| 1 |") == 1


def test_missing_snapshot_root_errors(tmp_path: Path):
    with pytest.raises(SnapshotValidationError):
        verify_snapshot(tmp_path / "missing")
