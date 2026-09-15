"""Tests for product card candidate builder."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "qa" / "product_card_candidates"
CATALOG = ROOT / "qa" / "catalog_experience"
sys.path.insert(0, str(CATALOG))
sys.path.insert(0, str(PKG))

from audit_lib import find_latest_valid_snapshot, load_snapshot  # noqa: E402
from build_product_card_candidates import (  # noqa: E402
    MISSING_SKUS,
    REVIEW_COLUMNS,
    RUNTIME_COLUMNS,
    build_rows,
    run_build,
)
from candidate_drafts import CANDIDATE_DRAFTS  # noqa: E402


@pytest.fixture(scope="module")
def snapshot_dir() -> Path:
    return find_latest_valid_snapshot()


@pytest.fixture(scope="module")
def snapshot(snapshot_dir: Path):
    return load_snapshot(snapshot_dir)


def test_all_missing_skus_have_drafts():
    assert set(MISSING_SKUS) == set(CANDIDATE_DRAFTS)
    assert len(MISSING_SKUS) == 16


def test_build_rows_cover_snapshot_products(snapshot):
    rows, summary = build_rows(snapshot)
    assert summary.candidate_count == 16
    assert not summary.missing_drafts
    assert summary.validation_errors == []
    assert set(r["sku"] for r in rows) == set(MISSING_SKUS)


def test_tsv_schema_and_no_existing_cards(snapshot, tmp_path: Path):
    rows, _ = build_rows(snapshot)
    cards = snapshot["cards"]
    for row in rows:
        assert row["sku"] not in cards
        assert row["source_status"] == "candidate_draft"
        assert row["price_answer_mode"] == "structured_price"
    out = tmp_path / "candidates.tsv"
    from build_product_card_candidates import write_tsv

    write_tsv(out, rows)
    loaded = list(csv.DictReader(out.open(encoding="utf-8"), delimiter="\t"))
    assert list(loaded[0].keys()) == list(RUNTIME_COLUMNS) + list(REVIEW_COLUMNS)
    for field in (
        "what_it_is",
        "who_asks_about_it",
        "common_use_cases",
        "how_to_use_short",
        "what_to_expect_soft",
        "contraindications_short",
    ):
        assert all(row[field] for row in loaded), f"missing {field}"


def test_provenance_status_counts(snapshot):
    _, summary = build_rows(snapshot)
    assert summary.by_status.get("ready_for_owner_upload", 0) >= 6
    assert summary.by_status.get("needs_owner_review", 0) >= 1


def test_runner_writes_artifacts(tmp_path: Path, snapshot_dir: Path):
    summary = run_build(
        snapshot_dir=snapshot_dir,
        tsv_path=tmp_path / "candidates.tsv",
        review_path=tmp_path / "review.md",
        report_path=tmp_path / "report.md",
    )
    assert summary.candidate_count == 16
    text = (tmp_path / "review.md").read_text(encoding="utf-8")
    assert "Ready for owner upload" in text
    assert "BEM" in text
