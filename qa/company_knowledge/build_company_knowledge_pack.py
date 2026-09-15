#!/usr/bin/env python3
"""Build company knowledge pack artifacts."""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

PKG = Path(__file__).resolve().parent
LAB = PKG / "lab"
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(LAB))

from builder import build_facts, build_leaders, build_owner_request, build_questions  # noqa: E402
from layers import build_partner_contacts, build_public_replies  # noqa: E402
from lint import (  # noqa: E402
    lint_facts,
    lint_leaders,
    lint_partner_contacts,
    lint_public_replies,
    lint_questions,
    lint_registry_preservation,
)
from sources import load_sources  # noqa: E402
from tsv_io import (  # noqa: E402
    write_facts,
    write_leaders,
    write_partner_contacts,
    write_public_replies,
    write_questions,
)

OUT_FACTS = PKG / "COMPANY_FACTS_CANDIDATES_V1.tsv"
OUT_LEADERS = PKG / "LEADERS_CANDIDATES_V1.tsv"
OUT_QUESTIONS = PKG / "COMPANY_QUESTION_CANDIDATES_V1.jsonl"
OUT_OWNER = PKG / "COMPANY_INFORMATION_REQUEST_FOR_OWNER.md"
OUT_PUBLIC = PKG / "COMPANY_PUBLIC_REPLY_CANDIDATES_V1.tsv"
OUT_PARTNER = PKG / "PARTNER_NETWORK_CONTACTS_CANDIDATES_V1.tsv"
REGISTRY = PKG / "COMPANY_FACTS_V1_REGISTRY.json"
META = PKG / "COMPANY_KNOWLEDGE_PACK_META.json"


def main() -> int:
    bundle = load_sources()
    facts = build_facts(bundle)
    leaders = build_leaders(bundle)
    questions = build_questions(facts)
    owner_md = build_owner_request(facts, bundle)
    public_replies = build_public_replies(facts)
    partner_contacts = build_partner_contacts(bundle, facts)

    issues = (
        lint_facts(facts)
        + lint_leaders(leaders)
        + lint_questions(questions, facts)
        + lint_public_replies(public_replies, facts)
        + lint_partner_contacts(partner_contacts)
        + lint_registry_preservation(facts, REGISTRY)
    )
    if issues:
        for issue in issues:
            print(f"LINT {issue.code}: {issue.message}", file=sys.stderr)
        return 1

    write_facts(OUT_FACTS, facts)
    write_leaders(OUT_LEADERS, leaders)
    write_questions(OUT_QUESTIONS, questions)
    write_public_replies(OUT_PUBLIC, public_replies)
    write_partner_contacts(OUT_PARTNER, partner_contacts)
    OUT_OWNER.write_text(owner_md, encoding="utf-8")

    layer_counts = Counter(str(f.get("audience_layer") or "") for f in facts)
    status_counts = Counter((f["topic"], f["owner_status"]) for f in facts)
    META.write_text(
        json.dumps(
            {
                "snapshot_id": bundle.snapshot_id,
                "fact_count": len(facts),
                "leader_count": len(leaders),
                "question_count": len(questions),
                "public_reply_count": len(public_replies),
                "partner_contact_count": len(partner_contacts),
                "accepted_facts": sum(
                    1 for f in facts if f["owner_status"] == "accepted" and f["provenance_status"] == "verified"
                ),
                "needs_owner_facts": sum(1 for f in facts if f["owner_status"] == "needs_owner_input"),
                "by_audience_layer": dict(layer_counts),
                "by_topic_status": {f"{a}/{b}": c for (a, b), c in status_counts.items()},
                "unreadable_sources": bundle.unreadable,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        f"Wrote {len(facts)} facts, {len(leaders)} leaders, {len(questions)} questions, "
        f"{len(public_replies)} public replies, {len(partner_contacts)} partner contacts "
        f"(snapshot {bundle.snapshot_id})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
