"""Lint company knowledge pack artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from constants import (
    ANSWER_LENGTH,
    AUDIENCE_LAYERS,
    CONFIDENCE,
    FACT_COLUMNS,
    LEADER_COLUMNS,
    OWNER_STATUS,
    PARTNER_CONTACT_COLUMNS,
    PROVENANCE,
    PUBLIC_REPLY_COLUMNS,
    TOPICS,
)
from layers import public_reply_is_clean


@dataclass
class LintIssue:
    code: str
    message: str


def lint_facts(rows: list[dict[str, str]]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    seen_ids: set[str] = set()

    for row in rows:
        fid = str(row.get("fact_id") or "")
        if fid in seen_ids:
            issues.append(LintIssue("duplicate_fact_id", f"duplicate fact_id {fid!r}"))
        seen_ids.add(fid)

        for col in FACT_COLUMNS:
            if col not in row:
                issues.append(LintIssue("missing_column", f"{fid}: missing {col}"))

        layer = str(row.get("audience_layer") or "")
        if layer not in AUDIENCE_LAYERS:
            issues.append(LintIssue("invalid_audience_layer", f"{fid}: bad audience_layer {layer!r}"))

        topic = str(row.get("topic") or "")
        if topic not in TOPICS:
            issues.append(LintIssue("invalid_topic", f"{fid}: bad topic {topic!r}"))
        prov = str(row.get("provenance_status") or "")
        if prov not in PROVENANCE:
            issues.append(LintIssue("invalid_provenance", f"{fid}: bad provenance {prov!r}"))
        owner = str(row.get("owner_status") or "")
        if owner not in OWNER_STATUS:
            issues.append(LintIssue("invalid_owner_status", f"{fid}: bad owner_status {owner!r}"))
        conf = str(row.get("confidence") or "")
        if conf not in CONFIDENCE:
            issues.append(LintIssue("invalid_confidence", f"{fid}: bad confidence {conf!r}"))

        locator = str(row.get("source_locator") or "").strip()
        ref = str(row.get("source_ref") or "").strip()
        if not ref:
            issues.append(LintIssue("missing_source_ref", f"{fid}: missing source_ref"))
        if not locator:
            issues.append(LintIssue("missing_source_locator", f"{fid}: missing source_locator"))

        fact_text = str(row.get("fact_text") or "").strip()
        if owner == "accepted" and prov == "verified":
            if not fact_text:
                issues.append(LintIssue("accepted_empty_fact", f"{fid}: accepted verified fact has empty text"))
            if not locator:
                issues.append(LintIssue("accepted_missing_locator", f"{fid}: accepted fact missing locator"))

        if owner == "accepted" and prov != "verified":
            issues.append(LintIssue("accepted_not_verified", f"{fid}: accepted requires provenance verified"))

        if layer == "company_public" and owner == "accepted" and prov == "verified":
            if not fact_text:
                issues.append(LintIssue("public_accepted_empty", f"{fid}: company_public accepted fact empty"))
            if not ref:
                issues.append(LintIssue("public_accepted_no_ref", f"{fid}: company_public accepted fact missing ref"))

    return issues


def lint_leaders(rows: list[dict[str, str]]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for row in rows:
        lid = str(row.get("leader_id") or "")
        if not str(row.get("display_name") or "").strip():
            issues.append(LintIssue("leader_missing_name", f"{lid}: missing display_name"))
        if not str(row.get("source_ref") or "").strip():
            issues.append(LintIssue("leader_missing_ref", f"{lid}: missing source_ref"))
        if not str(row.get("source_locator") or "").strip():
            issues.append(LintIssue("leader_missing_locator", f"{lid}: missing source_locator"))
        for col in LEADER_COLUMNS:
            if col not in row:
                issues.append(LintIssue("leader_missing_column", f"{lid}: missing {col}"))
    return issues


def lint_questions(
    questions: list[dict[str, object]],
    facts: list[dict[str, str]],
) -> list[LintIssue]:
    issues: list[LintIssue] = []
    accepted_facts = {
        str(f["fact_id"])
        for f in facts
        if f.get("owner_status") == "accepted" and f.get("provenance_status") == "verified"
    }
    if len(questions) < 45:
        issues.append(LintIssue("too_few_questions", f"need >=45 questions, got {len(questions)}"))

    topics_seen: set[str] = set()
    for row in questions:
        topic = str(row.get("expected_topic") or "")
        topics_seen.add(topic)
        layer = str(row.get("audience_layer") or "")
        if layer not in AUDIENCE_LAYERS:
            issues.append(LintIssue("question_bad_layer", f"{row.get('case_id')}: bad audience_layer"))
        status = str(row.get("acceptance_status") or "")
        fact_ids = [str(x) for x in (row.get("expected_fact_ids") or [])]
        if status == "accepted":
            if not fact_ids:
                issues.append(
                    LintIssue(
                        "accepted_question_no_facts",
                        f"{row.get('case_id')}: accepted question has no expected_fact_ids",
                    )
                )
            for fid in fact_ids:
                if fid not in accepted_facts:
                    issues.append(
                        LintIssue(
                            "accepted_question_bad_fact",
                            f"{row.get('case_id')}: points to non-accepted fact {fid!r}",
                        )
                    )
        elif status != "pending_owner":
            issues.append(LintIssue("invalid_acceptance_status", f"{row.get('case_id')}: bad status"))

    for topic in TOPICS:
        if topic not in topics_seen:
            issues.append(LintIssue("missing_question_topic", f"no questions for topic {topic!r}"))

    return issues


def lint_public_replies(
    rows: list[dict[str, str]],
    facts: list[dict[str, str]],
) -> list[LintIssue]:
    issues: list[LintIssue] = []
    fact_map = {str(f["fact_id"]): f for f in facts}
    required_intents = {
        "what_is_whieda",
        "company_activity",
        "history_founding",
        "production_location",
        "public_representatives",
        "news_and_events",
    }
    seen_intents: set[str] = set()

    for row in rows:
        rid = str(row.get("reply_id") or "")
        for col in PUBLIC_REPLY_COLUMNS:
            if col not in row:
                issues.append(LintIssue("public_reply_missing_column", f"{rid}: missing {col}"))

        intent = str(row.get("intent") or "")
        seen_intents.add(intent)
        owner = str(row.get("owner_status") or "")
        answer = str(row.get("answer_text") or "").strip()
        length = str(row.get("answer_length") or "")
        if length not in ANSWER_LENGTH:
            issues.append(LintIssue("public_reply_bad_length", f"{rid}: bad answer_length"))

        if owner == "pending_owner" and answer:
            issues.append(LintIssue("pending_owner_has_answer", f"{rid}: pending_owner must have empty answer_text"))

        if owner == "accepted" and not answer:
            issues.append(LintIssue("accepted_reply_empty", f"{rid}: accepted reply has empty answer_text"))

        for fid in str(row.get("fact_ids") or "").split("|"):
            if not fid:
                continue
            fact = fact_map.get(fid)
            if not fact:
                issues.append(LintIssue("public_reply_unknown_fact", f"{rid}: unknown fact {fid!r}"))
                continue
            if fact.get("audience_layer") == "platform_internal":
                issues.append(LintIssue("platform_in_public_reply", f"{rid}: platform_internal fact {fid}"))
            if fact.get("audience_layer") == "partner_network":
                issues.append(LintIssue("partner_in_public_reply", f"{rid}: partner_network fact {fid}"))

        if answer and not public_reply_is_clean(row, facts):
            issues.append(LintIssue("public_reply_not_clean", f"{rid}: forbidden wording or bad fact_ids"))

        if intent == "what_is_whieda" and answer:
            for fid in str(row.get("fact_ids") or "").split("|"):
                if fid and fact_map.get(fid, {}).get("audience_layer") != "company_public":
                    issues.append(LintIssue("what_is_whieda_not_public", f"{rid}: non-public fact in what_is_whieda"))

    missing = required_intents - seen_intents
    if missing:
        issues.append(LintIssue("public_reply_missing_intent", f"missing intents: {sorted(missing)}"))
    return issues


def lint_partner_contacts(rows: list[dict[str, str]]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for row in rows:
        cid = str(row.get("contact_id") or "")
        for col in PARTNER_CONTACT_COLUMNS:
            if col not in row:
                issues.append(LintIssue("partner_contact_missing_column", f"{cid}: missing {col}"))
        ref_ctx = str(row.get("requires_ref_context") or "")
        if ref_ctx not in {"yes", "no"}:
            issues.append(LintIssue("partner_bad_ref_context", f"{cid}: bad requires_ref_context"))
        event_pub = str(row.get("event_public") or "")
        if event_pub not in {"yes", "no"}:
            issues.append(LintIssue("partner_bad_event_public", f"{cid}: bad event_public"))
        if "prepared_not_routed" in str(row.get("notes") or "").lower():
            if str(row.get("owner_status") or "") == "accepted":
                issues.append(LintIssue("prepared_routed_as_public", f"{cid}: prepared_not_routed treated as public"))
    return issues


def lint_registry_preservation(
    facts: list[dict[str, str]],
    registry_path: Path,
) -> list[LintIssue]:
    issues: list[LintIssue] = []
    if not registry_path.is_file():
        issues.append(LintIssue("missing_registry", f"missing {registry_path}"))
        return issues
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    expected = set(registry.get("fact_ids") or [])
    actual = {str(f["fact_id"]) for f in facts}
    missing = expected - actual
    extra = actual - expected
    if missing:
        issues.append(LintIssue("registry_missing_ids", f"missing fact_ids: {sorted(missing)}"))
    if extra:
        issues.append(LintIssue("registry_extra_ids", f"unexpected fact_ids: {sorted(extra)}"))
    return issues
