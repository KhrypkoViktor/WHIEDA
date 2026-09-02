"""Tests for company knowledge pack."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / "qa" / "company_knowledge"
LAB = PKG / "lab"
DOCS = ROOT / "backend" / "platform-api" / "docs"

sys.path.insert(0, str(PKG))
sys.path.insert(0, str(LAB))

from constants import (  # noqa: E402
    AUDIENCE_LAYERS,
    FACT_COLUMNS,
    PARTNER_CONTACT_COLUMNS,
    PUBLIC_REPLY_COLUMNS,
    TOPICS,
)
from lint import (  # noqa: E402
    lint_facts,
    lint_leaders,
    lint_partner_contacts,
    lint_public_replies,
    lint_questions,
    lint_registry_preservation,
)
from tsv_io import (  # noqa: E402
    read_facts,
    read_leaders,
    read_partner_contacts,
    read_public_replies,
    read_questions,
)


def test_build_script_exits_zero():
    proc = subprocess.run(
        [sys.executable, str(PKG / "build_company_knowledge_pack.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_facts_schema_and_topics():
    rows = read_facts(PKG / "COMPANY_FACTS_CANDIDATES_V1.tsv")
    assert len(rows) >= 30
    topics = {r["topic"] for r in rows}
    assert set(TOPICS).issubset(topics)
    for row in rows:
        for col in FACT_COLUMNS:
            assert col in row
        assert row.get("audience_layer") in AUDIENCE_LAYERS
        assert str(row.get("source_ref") or "").strip()
        assert str(row.get("source_locator") or "").strip()


def test_leaders_have_sources():
    rows = read_leaders(PKG / "LEADERS_CANDIDATES_V1.tsv")
    assert len(rows) >= 5
    for row in rows:
        assert row.get("display_name")
        assert row.get("source_ref")
        assert row.get("source_locator")


def test_questions_cover_topics_and_layers():
    questions = read_questions(PKG / "COMPANY_QUESTION_CANDIDATES_V1.jsonl")
    assert len(questions) >= 45
    topics = {str(q["expected_topic"]) for q in questions}
    assert topics == set(TOPICS)
    for q in questions:
        assert q.get("audience_layer") in AUDIENCE_LAYERS


def test_public_reply_layer_isolation():
    facts = read_facts(PKG / "COMPANY_FACTS_CANDIDATES_V1.tsv")
    replies = read_public_replies(PKG / "COMPANY_PUBLIC_REPLY_CANDIDATES_V1.tsv")
    assert len(replies) >= 6
    for row in replies:
        for col in PUBLIC_REPLY_COLUMNS:
            assert col in row
    issues = lint_public_replies(replies, facts)
    assert not issues, [f"{i.code}: {i.message}" for i in issues]


def test_partner_contacts_schema():
    rows = read_partner_contacts(PKG / "PARTNER_NETWORK_CONTACTS_CANDIDATES_V1.tsv")
    assert len(rows) >= 5
    for row in rows:
        for col in PARTNER_CONTACT_COLUMNS:
            assert col in row
    issues = lint_partner_contacts(rows)
    assert not issues, [f"{i.code}: {i.message}" for i in issues]


def test_v1_registry_preserved():
    facts = read_facts(PKG / "COMPANY_FACTS_CANDIDATES_V1.tsv")
    issues = lint_registry_preservation(facts, PKG / "COMPANY_FACTS_V1_REGISTRY.json")
    assert not issues, [f"{i.code}: {i.message}" for i in issues]


def test_lint_passes_on_built_pack():
    facts = read_facts(PKG / "COMPANY_FACTS_CANDIDATES_V1.tsv")
    leaders = read_leaders(PKG / "LEADERS_CANDIDATES_V1.tsv")
    questions = read_questions(PKG / "COMPANY_QUESTION_CANDIDATES_V1.jsonl")
    public_replies = read_public_replies(PKG / "COMPANY_PUBLIC_REPLY_CANDIDATES_V1.tsv")
    partner_contacts = read_partner_contacts(PKG / "PARTNER_NETWORK_CONTACTS_CANDIDATES_V1.tsv")
    issues = (
        lint_facts(facts)
        + lint_leaders(leaders)
        + lint_questions(questions, facts)
        + lint_public_replies(public_replies, facts)
        + lint_partner_contacts(partner_contacts)
        + lint_registry_preservation(facts, PKG / "COMPANY_FACTS_V1_REGISTRY.json")
    )
    assert not issues, [f"{i.code}: {i.message}" for i in issues]


def test_owner_request_exists_and_plain():
    text = (PKG / "COMPANY_INFORMATION_REQUEST_FOR_OWNER.md").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) <= 12
    assert "двоеточия" in text.lower()
    assert "Год основания" in text
    assert "необяз" in text.lower()
    assert "provenance" not in text.lower()
    assert "runtime" not in text.lower()


def test_offline_runner_passes():
    proc = subprocess.run(
        [sys.executable, str(PKG / "run_company_knowledge_pack.py"), "--offline"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (DOCS / "COMPANY_KNOWLEDGE_PACK_REVIEW_FIX_LOCAL_REPORT.md").is_file()


def test_accepted_question_links_verified_facts():
    facts = read_facts(PKG / "COMPANY_FACTS_CANDIDATES_V1.tsv")
    questions = read_questions(PKG / "COMPANY_QUESTION_CANDIDATES_V1.jsonl")
    accepted_facts = {
        f["fact_id"]
        for f in facts
        if f["owner_status"] == "accepted" and f["provenance_status"] == "verified"
    }
    for q in questions:
        if q.get("acceptance_status") != "accepted":
            continue
        for fid in q.get("expected_fact_ids") or []:
            assert fid in accepted_facts


def test_review_fix_report_has_layer_table():
    report = (DOCS / "COMPANY_KNOWLEDGE_PACK_REVIEW_FIX_LOCAL_REPORT.md").read_text(encoding="utf-8")
    assert "company_public" in report
    assert "partner_network" in report
    assert "platform_internal" in report
