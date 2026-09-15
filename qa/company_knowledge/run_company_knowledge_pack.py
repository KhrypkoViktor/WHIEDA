#!/usr/bin/env python3
"""Offline validation and report for company knowledge pack."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = Path(__file__).resolve().parent
LAB = PKG / "lab"
STATIC = ROOT / "backend" / "platform-api" / "docs" / "COMPANY_KNOWLEDGE_PACK_LOCAL_REPORT.md"
REVIEW_STATIC = ROOT / "backend" / "platform-api" / "docs" / "COMPANY_KNOWLEDGE_PACK_REVIEW_FIX_LOCAL_REPORT.md"

sys.path.insert(0, str(PKG))
sys.path.insert(0, str(LAB))

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

FACTS = PKG / "COMPANY_FACTS_CANDIDATES_V1.tsv"
LEADERS = PKG / "LEADERS_CANDIDATES_V1.tsv"
QUESTIONS = PKG / "COMPANY_QUESTION_CANDIDATES_V1.jsonl"
OWNER = PKG / "COMPANY_INFORMATION_REQUEST_FOR_OWNER.md"
PUBLIC = PKG / "COMPANY_PUBLIC_REPLY_CANDIDATES_V1.tsv"
PARTNER = PKG / "PARTNER_NETWORK_CONTACTS_CANDIDATES_V1.tsv"
REGISTRY = PKG / "COMPANY_FACTS_V1_REGISTRY.json"
META = PKG / "COMPANY_KNOWLEDGE_PACK_META.json"


def write_report(
    *,
    meta: dict[str, object],
    facts: list[dict[str, str]],
    questions: list[dict[str, object]],
    lint_ok: bool,
) -> None:
    topic_counts = Counter(str(f.get("topic") or "") for f in facts)
    owner_counts = Counter(str(f.get("owner_status") or "") for f in facts)
    prov_counts = Counter(str(f.get("provenance_status") or "") for f in facts)
    q_accepted = sum(1 for q in questions if q.get("acceptance_status") == "accepted")

    lines = [
        "# Company Knowledge Pack — Local Report",
        "",
        "Mode: read-only data preparation. **Not wired into Telegram/runtime.**",
        "",
        f"- Snapshot: `{meta.get('snapshot_id', 'unknown')}`",
        f"- Facts: {len(facts)}",
        f"- Leaders: {meta.get('leader_count', 0)}",
        f"- Questions: {len(questions)} ({q_accepted} accepted, {len(questions) - q_accepted} pending_owner)",
        f"- Verified accepted facts: {meta.get('accepted_facts', 0)}",
        f"- Needs owner input: {meta.get('needs_owner_facts', 0)}",
        f"- Lint: {'PASS' if lint_ok else 'FAIL'}",
        "",
        "## Facts by topic",
        "",
    ]
    for topic in sorted(topic_counts):
        lines.append(f"- `{topic}`: {topic_counts[topic]}")
    lines.extend(["", "## Facts by owner_status", ""])
    for status, count in sorted(owner_counts.items()):
        lines.append(f"- `{status}`: {count}")
    lines.extend(["", "## Facts by provenance", ""])
    for prov, count in sorted(prov_counts.items()):
        lines.append(f"- `{prov}`: {count}")

    unreadable = meta.get("unreadable_sources") or []
    if unreadable:
        lines.extend(["", "## Unreadable sources", ""])
        for path in unreadable:
            lines.append(f"- `{path}`")

    lines.extend(
        [
            "",
            "## What is found vs missing",
            "",
            "- **Found (verified):** business FAQ, objections rule OBJ-03, partners/contacts, events, platform scope from product direction.",
            "- **Missing (owner input):** founding year/history, corporate email/phone, owner-approved production layer.",
            "- **Pending review:** Obsidian Pack 9 company/production claims marked `company_stated`.",
            "",
            "## Artifacts",
            "",
            "- `qa/company_knowledge/COMPANY_FACTS_CANDIDATES_V1.tsv`",
            "- `qa/company_knowledge/LEADERS_CANDIDATES_V1.tsv`",
            "- `qa/company_knowledge/COMPANY_QUESTION_CANDIDATES_V1.jsonl`",
            "- `qa/company_knowledge/COMPANY_INFORMATION_REQUEST_FOR_OWNER.md`",
            "- `qa/company_knowledge/COMPANY_PUBLIC_REPLY_CANDIDATES_V1.tsv`",
            "- `qa/company_knowledge/PARTNER_NETWORK_CONTACTS_CANDIDATES_V1.tsv`",
            "",
            "## Reproduce",
            "",
            "```bash",
            "python qa/company_knowledge/build_company_knowledge_pack.py",
            "python qa/company_knowledge/run_company_knowledge_pack.py --offline",
            "python -m pytest backend/platform-api/tests/test_company_knowledge_pack.py -q",
            "```",
            "",
        ]
    )
    STATIC.write_text("\n".join(lines), encoding="utf-8")


def write_review_fix_report(
    *,
    meta: dict[str, object],
    facts: list[dict[str, str]],
    questions: list[dict[str, object]],
    public_replies: list[dict[str, str]],
    partner_contacts: list[dict[str, str]],
    lint_ok: bool,
) -> None:
    layer_fact = Counter(str(f.get("audience_layer") or "") for f in facts)
    layer_q = Counter(str(q.get("audience_layer") or "") for q in questions)
    pending_owner = sum(
        1
        for row in (
            [str(f.get("owner_status") or "") for f in facts]
            + [str(q.get("acceptance_status") or "") for q in questions]
            + [str(r.get("owner_status") or "") for r in public_replies]
        )
        if row in {"needs_owner_input", "pending_owner"}
    )

    lines = [
        "# Company Knowledge Pack — Review Fix Local Report",
        "",
        "V1.1 layer split: public company / partner network / platform internal. **Not wired to runtime.**",
        "",
        f"- Snapshot: `{meta.get('snapshot_id', 'unknown')}`",
        f"- Lint: {'PASS' if lint_ok else 'FAIL'}",
        "",
        "## Audience layers",
        "",
        "| Layer | Facts | Questions |",
        "| --- | ---: | ---: |",
        f"| `company_public` | {layer_fact.get('company_public', 0)} | {layer_q.get('company_public', 0)} |",
        f"| `partner_network` | {layer_fact.get('partner_network', 0)} | {layer_q.get('partner_network', 0)} |",
        f"| `platform_internal` | {layer_fact.get('platform_internal', 0)} | {layer_q.get('platform_internal', 0)} |",
        f"| `pending_owner` (rollup) | — | — |",
        "",
        f"Pending-owner rollup (facts/questions/public replies): **{pending_owner}**",
        "",
        "## New artifacts",
        "",
        f"- Public reply intents: {len(public_replies)}",
        f"- Partner network contacts: {len(partner_contacts)}",
        "",
        "## Block E checks",
        "",
        "- Accepted `company_public` facts require non-empty text and source.",
        "- `platform_internal` and `partner_network` facts excluded from public reply TSV.",
        "- Pending-owner public replies have empty `answer_text`.",
        "- V1 `fact_id` registry preserved.",
        "",
        "## Reproduce",
        "",
        "```bash",
        "python qa/company_knowledge/build_company_knowledge_pack.py",
        "python qa/company_knowledge/run_company_knowledge_pack.py --offline",
        "python -m pytest backend/platform-api/tests/test_company_knowledge_pack.py -q",
        "```",
        "",
    ]
    REVIEW_STATIC.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    required = (FACTS, LEADERS, QUESTIONS, OWNER, PUBLIC, PARTNER, META, REGISTRY)
    for path in required:
        if not path.is_file():
            print(f"Missing: {path}", file=sys.stderr)
            return 1

    facts = read_facts(FACTS)
    leaders = read_leaders(LEADERS)
    questions = read_questions(QUESTIONS)
    public_replies = read_public_replies(PUBLIC)
    partner_contacts = read_partner_contacts(PARTNER)
    meta = json.loads(META.read_text(encoding="utf-8"))

    issues = (
        lint_facts(facts)
        + lint_leaders(leaders)
        + lint_questions(questions, facts)
        + lint_public_replies(public_replies, facts)
        + lint_partner_contacts(partner_contacts)
        + lint_registry_preservation(facts, REGISTRY)
    )
    lint_ok = not issues
    if issues:
        for issue in issues:
            print(f"LINT {issue.code}: {issue.message}", file=sys.stderr)

    write_report(meta=meta, facts=facts, questions=questions, lint_ok=lint_ok)
    write_review_fix_report(
        meta=meta,
        facts=facts,
        questions=questions,
        public_replies=public_replies,
        partner_contacts=partner_contacts,
        lint_ok=lint_ok,
    )
    print(f"Report: {STATIC}")
    print(f"Review fix report: {REVIEW_STATIC}")
    return 0 if lint_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
