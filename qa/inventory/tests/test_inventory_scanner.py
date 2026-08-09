"""Tests for read-only WHIEDA asset inventory scanner."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

QA_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = QA_ROOT.parent
FIXTURES = Path(__file__).parent / "fixtures"

if str(QA_ROOT) not in sys.path:
    sys.path.insert(0, str(QA_ROOT))

from inventory.constants import MANIFEST_COLUMNS  # noqa: E402
from inventory.readers import read_jsonl_info, read_tsv_info  # noqa: E402
from inventory.scanner import run_inventory_scan  # noqa: E402


def test_read_tsv_fixture():
    info = read_tsv_info(FIXTURES / "corpus" / "01_QUESTIONS.tsv")
    assert info.header[0] == "question_id"
    assert info.data_rows == 2
    assert info.non_empty_rows == 2


def test_read_jsonl_fixture():
    info = read_jsonl_info(FIXTURES / "parity.jsonl")
    assert info.valid_objects == 2
    assert info.invalid_lines == 0


def test_inventory_scan_on_real_repo(tmp_path):
    result = run_inventory_scan(tmp_path, repo_root=REPO_ROOT)
    assert result.manifest_path.is_file()
    assert result.feature_matrix_path.is_file()
    assert result.data_map_path.is_file()
    assert len(result.manifest_rows) >= 5
    assert result.errors == []


def test_manifest_csv_schema(tmp_path):
    result = run_inventory_scan(tmp_path, repo_root=REPO_ROOT)
    with result.manifest_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == list(MANIFEST_COLUMNS)
        rows = list(reader)
    assert len(rows) == len(result.manifest_rows)


def test_distillate_row_counts_match_files(tmp_path):
    result = run_inventory_scan(tmp_path, repo_root=REPO_ROOT)
    assert result.checks_failed == 0


def test_blocked_layers_not_user_visible(tmp_path):
    result = run_inventory_scan(tmp_path, repo_root=REPO_ROOT)
    for row in result.manifest_rows:
        if row.asset_id == "DIST-07-TESTIMONIALS":
            assert row.user_visible == "no"
            assert row.review_state == "blocked_raw"
            break
    else:
        if (REPO_ROOT / "RAG").is_dir():
            pytest.fail("expected DIST-07-TESTIMONIALS in manifest when RAG present")
