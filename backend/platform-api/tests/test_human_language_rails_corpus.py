"""Tests for human language rails offline corpus."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "qa" / "human_language_rails"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(PKG / "lab"))

from corpus import (  # noqa: E402
    ACCEPTANCE_STATUSES,
    FORBIDDEN_FRAGMENTS,
    RAIL_MINIMUMS,
    RAILS,
    UNIVERSAL_MENU_MUST_CONTAIN_ALL,
    accepted_assertions,
    dedupe_key,
    is_malformed,
    load_cases,
    load_flows,
    validate_cases,
    validate_flow_metadata,
)
from run_human_language_rails import run_offline  # noqa: E402


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    return load_cases(PKG / "whieda_human_language_rails_v1.jsonl")


@pytest.fixture(scope="module")
def flows() -> list[dict]:
    return load_flows(PKG / "flows_v1.jsonl")


def test_corpus_meets_accepted_minimums(cases):
    accepted = accepted_assertions(cases)
    assert len([c for c in cases if c.get("turn_role") == "assertion"]) >= 150
    by_rail = Counter(c["expected_rail"] for c in accepted)
    for rail, minimum in RAIL_MINIMUMS.items():
        assert by_rail[rail] >= minimum, f"{rail}={by_rail[rail]}"


def test_acceptance_status_on_every_assertion(cases):
    for row in cases:
        if row.get("turn_role") != "assertion":
            continue
        assert row.get("acceptance_status") in ACCEPTANCE_STATUSES


def test_accepted_universal_menu_must_contain_all(cases):
    for row in accepted_assertions(cases):
        if row.get("expected_rail") != "universal_menu":
            continue
        must_all = row.get("must_contain_all") or []
        for marker in UNIVERSAL_MENU_MUST_CONTAIN_ALL:
            assert marker in must_all, row["case_id"]


def test_no_duplicate_assertion_tuples(cases):
    seen: set[tuple[str, str, str]] = set()
    for row in cases:
        if row.get("turn_role") != "assertion":
            continue
        key = dedupe_key(row)
        assert key not in seen, row["case_id"]
        seen.add(key)


def test_assertion_schema_and_sources(cases):
    for row in cases:
        if row.get("turn_role") != "assertion":
            continue
        assert row["expected_rail"] in RAILS
        src = row.get("source") or {}
        assert src.get("kind")
        assert src.get("ref")
        assert row.get("rationale")
        must_not = [x.lower() for x in row.get("must_not_contain") or []]
        for frag in FORBIDDEN_FRAGMENTS:
            if frag == "traceback":
                assert any("traceback" in x for x in must_not)
            else:
                assert frag in must_not


def test_malformed_and_flow_coverage(cases):
    accepted = accepted_assertions(cases)
    malformed = sum(1 for row in accepted if is_malformed(row["user_text"]))
    assert malformed >= 45
    flow_ids = {row["flow_id"] for row in cases}
    multi = [fid for fid in flow_ids if sum(1 for c in cases if c["flow_id"] == fid) >= 2]
    assert len(multi) >= 35


def test_flow_metadata_reconciled(cases, flows):
    assert not validate_flow_metadata(cases, flows)
    corpus_ids = {c["flow_id"] for c in cases}
    meta_ids = {f["flow_id"] for f in flows}
    assert corpus_ids == meta_ids
    assert len(flows) == len(corpus_ids)


def test_pending_fixture_matches_corpus(cases):
    pending_path = PKG / "fixtures" / "pending_assertions.jsonl"
    assert pending_path.is_file()
    pending = load_cases(pending_path)
    corpus_pending = [
        c
        for c in cases
        if c.get("turn_role") == "assertion"
        and c.get("acceptance_status") in {"pending_surface", "pending_policy"}
    ]
    assert len(pending) == len(corpus_pending)


def test_offline_runner_passes(tmp_path: Path):
    code = run_offline(
        corpus_path=PKG / "whieda_human_language_rails_v1.jsonl",
        flows_path=PKG / "flows_v1.jsonl",
        report_path=tmp_path / "report.md",
    )
    assert code == 0
    body = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "PASS" in body
    assert "must_contain_all" in body


def test_flows_metadata_present(flows):
    assert len(flows) >= 35
    sample = flows[0]
    assert "flow_id" in sample and "name" in sample
    assert "turn_count" in sample and "assertion_count" in sample
