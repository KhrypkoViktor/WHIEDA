"""Tests for product discovery map lint and mandatory coverage."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "qa" / "product_discovery"
FIXTURE = ROOT / "qa" / "master_integrity" / "fixtures" / "valid"
TSV = PKG / "PRODUCT_DISCOVERY_MAP_CANDIDATES_V1.tsv"

sys.path.insert(0, str(PKG))
# qa/company_knowledge ships modules with the same bare names; drop its cached copies
for _name in ("builder", "constants", "lint", "snapshot_loader", "tsv_io"):
    sys.modules.pop(_name, None)

from builder import build_rows, mandatory_phrase_coverage  # noqa: E402
from constants import MANDATORY_GROUPS, TSV_COLUMNS  # noqa: E402
from lint import lint_rows  # noqa: E402
from snapshot_loader import load_bundle  # noqa: E402
from tsv_io import read_rows  # noqa: E402


def test_build_script_exits_zero():
    proc = subprocess.run(
        [sys.executable, str(PKG / "build_product_discovery_map.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert TSV.is_file()


def test_tsv_schema_and_mandatory_groups():
    rows = read_rows(TSV)
    assert rows, "TSV must not be empty"
    for row in rows:
        for col in TSV_COLUMNS:
            assert col in row
    for group, phrases in MANDATORY_GROUPS.items():
        for phrase in phrases:
            assert any(r["phrase"] == phrase for r in rows), f"missing {phrase} in {group}"


def test_lint_passes_on_built_tsv():
    bundle = load_bundle()
    rows = read_rows(TSV)
    issues = lint_rows(rows, bundle)
    assert not issues, [f"{i.code}: {i.message}" for i in issues]


def test_max_three_candidates_per_phrase():
    rows = read_rows(TSV)
    from collections import Counter

    counts = Counter(r["normalized_phrase"] for r in rows)
    for norm, count in counts.items():
        assert count <= 3, f"{norm} has {count} candidates"


def test_color_guard_do_not_resolve():
    rows = read_rows(TSV)
    for color in ("красный", "зелёный", "синий"):
        color_rows = [r for r in rows if r["phrase"] == color]
        assert len(color_rows) == 1
        assert color_rows[0]["status"] == "do_not_resolve"
        assert color_rows[0]["sku"] == ""


def test_pодарок_is_generic_task_selection():
    rows = read_rows(TSV)
    gift_rows = [r for r in rows if r["phrase"] == "подарок"]
    assert gift_rows
    assert all(r["status"] == "generic_category" for r in gift_rows)
    assert all(r["status"] != "ready_for_core_review" for r in gift_rows)


def test_activator_is_generic_not_silent_open():
    rows = read_rows(TSV)
    act = [r for r in rows if r["phrase"] == "активатор"]
    assert len(act) == 2
    assert all(r["status"] == "generic_category" for r in act)


def test_policy_skus_exist_in_map():
    from lint import lint_policy_skus

    rows = read_rows(TSV)
    issues = lint_policy_skus(PKG / "PRODUCT_DISCOVERY_RESOLUTION_POLICY_V1.md", rows)
    assert not issues, [f"{i.code}: {i.message}" for i in issues]


def test_triage_uses_live_hlr_report():
    triage = (PKG / "HLR_DISCOVERY_TRIAGE_V1.md").read_text(encoding="utf-8")
    assert "corpus_proxy" not in triage.casefold()
    assert "HLR_HTTP_REPORT_" in triage
    assert "20260814T154952Z-f5e9841f" in triage


def test_lint_rejects_pодарок_ready():
    bundle = load_bundle(FIXTURE)
    row = {
        "phrase": "подарок",
        "normalized_phrase": "подарок",
        "discovery_group": "lifestyle",
        "candidate_rank": "1",
        "sku": "C065-00",
        "canonical_name": "x",
        "confidence": "high",
        "status": "ready_for_core_review",
        "evidence": "ev",
        "source_snapshot_id": "x",
        "notes": "",
    }
    issues = lint_rows([row], bundle)
    assert any(i.code == "gift_task_selection_conflict" for i in issues)


def test_lint_rejects_unknown_sku():
    bundle = load_bundle(FIXTURE)
    bad = {
        "phrase": "test",
        "normalized_phrase": "test",
        "discovery_group": "test",
        "candidate_rank": "1",
        "sku": "NO-SUCH-SKU",
        "canonical_name": "",
        "confidence": "low",
        "status": "ready_for_core_review",
        "evidence": "test",
        "source_snapshot_id": "x",
        "notes": "",
    }
    issues = lint_rows([bad], bundle)
    assert any(i.code == "unknown_sku" for i in issues)


def test_lint_rejects_duplicate_phrase_sku():
    bundle = load_bundle(FIXTURE)
    sku = next(iter(bundle.products))
    base = {
        "phrase": "dup",
        "normalized_phrase": "dup",
        "discovery_group": "test",
        "candidate_rank": "1",
        "sku": sku,
        "canonical_name": "x",
        "confidence": "high",
        "status": "ready_for_core_review",
        "evidence": "ev",
        "source_snapshot_id": "x",
        "notes": "",
    }
    dup = dict(base)
    dup["candidate_rank"] = "2"
    issues = lint_rows([base, dup], bundle)
    assert any(i.code == "duplicate_phrase_sku" for i in issues)


def test_lint_rejects_missing_evidence():
    bundle = load_bundle(FIXTURE)
    row = {
        "phrase": "x",
        "normalized_phrase": "x",
        "discovery_group": "test",
        "candidate_rank": "1",
        "sku": "",
        "canonical_name": "",
        "confidence": "low",
        "status": "generic_category",
        "evidence": "",
        "source_snapshot_id": "x",
        "notes": "",
    }
    issues = lint_rows([row], bundle)
    assert any(i.code == "missing_evidence" for i in issues)


def test_builder_mandatory_coverage_empty_on_live_snapshot():
    bundle = load_bundle()
    rows = build_rows(bundle)
    missing = mandatory_phrase_coverage(rows)
    assert missing == {}, missing


def test_offline_runner_passes():
    proc = subprocess.run(
        [sys.executable, str(PKG / "run_product_discovery_map.py"), "--offline"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
