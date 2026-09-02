"""Tests for product card wording audit."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "qa" / "product_card_wording_audit"
LAB = PKG / "lab"
FIXTURE = PKG / "fixtures" / "mini_snapshot"
CAND_FIXTURE = PKG / "fixtures" / "candidates_repeat.tsv"

sys.path.insert(0, str(LAB))
import parser as parser_mod  # noqa: E402
import rules as rules_mod  # noqa: E402


def test_fixture_snapshot_valid():
    from audit_lib import verify_snapshot  # noqa: WPS433

    verify_snapshot(FIXTURE)


def test_repeat_and_vague_phrase_detected():
    _, cards = parser_mod.load_all_cards(snapshot_dir=FIXTURE, candidates_path=CAND_FIXTURE)
    findings = rules_mod.run_audit(cards)
    rules_found = {f.rule_id for f in findings}
    assert "VAGUE-PHRASE" in rules_found
    assert "REPEAT-BULLET" in rules_found or "OPENING-FORMULA" in rules_found


def test_clean_card_has_no_review_findings():
    _, cards = parser_mod.load_all_cards(snapshot_dir=FIXTURE, candidates_path=FIXTURE / "empty_candidates.tsv")
    # empty candidates file
    findings = [f for f in rules_mod.run_audit(cards) if f.sku == "CLEAN-01" and f.severity == "review"]
    assert not findings


def test_missing_provenance_attention():
    _, cards = parser_mod.load_all_cards(snapshot_dir=FIXTURE, candidates_path=CAND_FIXTURE)
    prov = [f for f in rules_mod.run_audit(cards) if f.rule_id.startswith("PROV-")]
    assert prov


def test_runner_writes_gitignored_reports(tmp_path: Path, monkeypatch):
    sys.path.insert(0, str(PKG))
    import run_product_card_wording_audit as runner  # noqa: WPS433

    monkeypatch.setattr(runner, "OUT_DIR", tmp_path / "reports")
    monkeypatch.setattr(runner, "STATIC_REPORT", tmp_path / "static.md")
    monkeypatch.setattr(sys, "argv", ["run_product_card_wording_audit.py", "--fixture"])
    code = runner.main()
    assert code == 0
    assert list((tmp_path / "reports").glob("PRODUCT_CARD_WORDING_AUDIT_*_FINDINGS.csv"))
