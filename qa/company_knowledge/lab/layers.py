"""Audience layers, public replies, and partner contacts."""

from __future__ import annotations

from constants import PUBLIC_REPLY_FORBIDDEN, SNAPSHOT_REL

PLATFORM_SCOPES = frozenset(
    {
        "platform_architecture",
        "content_pipeline",
        "advisor_contract",
        "product_vision",
        "platform_leadership",
        "platform_owner",
        "advisor_scope",
    }
)

PARTNER_SCOPES = frozenset(
    {
        "business_faq",
        "objection_handling",
        "structure_leadership",
        "structure_routing",
        "partner_contact",
    }
)


def classify_audience_layer(fact: dict[str, str]) -> str:
    topic = str(fact.get("topic") or "")
    scope = str(fact.get("answer_scope") or "")
    fid = str(fact.get("fact_id") or "")

    if topic == "partner_business" or scope in PARTNER_SCOPES:
        return "partner_network"
    if topic == "technology" or scope in PLATFORM_SCOPES:
        return "platform_internal"
    if fid in {"CK-LD-007", "CK-CON-028"}:
        return "platform_internal"
    if topic in {"company_overview", "history", "production", "leadership"}:
        if str(fact.get("provenance_status") or "") == "company_stated":
            return "company_public"
        if scope in {"company_pack9"}:
            return "company_public"
        if topic == "company_overview" and fid in {"CK-OV-001", "CK-OV-002", "CK-OV-003", "CK-OV-004"}:
            return "platform_internal"
        return "company_public"
    if topic == "events":
        return "company_public"
    if topic == "contacts":
        return "partner_network"
    return "company_public"


def apply_audience_layers(facts: list[dict[str, str]]) -> None:
    for fact in facts:
        fact["audience_layer"] = classify_audience_layer(fact)


def question_audience_layer(case_id: str, topic: str, user_text: str) -> str:
    text = user_text.casefold()
    platform_markers = (
        "платформ",
        "core",
        "sql",
        "бот бер",
        "company layer",
        "владелец платформы",
        "виктор хрипко",
        "отличаетесь от обычного бота",
        "готовятся ответы",
    )
    if any(m in text for m in platform_markers) or case_id in {
        "CKQ-002",
        "CKQ-007",
        "CKQ-008",
        "CKQ-014",
        "CKQ-015",
        "CKQ-033",
        "CKQ-038",
        "CKQ-039",
    }:
        return "platform_internal"
    if topic == "partner_business":
        return "partner_network"
    if topic in {"contacts", "leadership"} or "наставник" in text or "ref" in text or "консультант" in text:
        return "partner_network"
    return "company_public"


def build_public_replies(facts: list[dict[str, str]]) -> list[dict[str, str]]:
    by_id = {str(f["fact_id"]): f for f in facts}
    evt_public = [
        f
        for f in facts
        if f.get("audience_layer") == "company_public"
        and f.get("owner_status") == "accepted"
        and f.get("provenance_status") == "verified"
        and f.get("topic") == "events"
        and str(f.get("fact_text") or "").strip()
    ]

    def _join(ids: list[str]) -> str:
        parts = [str(by_id[i]["fact_text"]).strip() for i in ids if i in by_id and by_id[i].get("fact_text")]
        return " ".join(parts).strip()

    evt_ids = [str(f["fact_id"]) for f in evt_public]
    evt_ref = str(evt_public[0].get("source_ref") or "events.tsv") if evt_public else "events.tsv"

    intents: list[tuple[str, str, str, list[str], str, str]] = [
        (
            "CPR-001",
            "what_is_whieda",
            "что такое WHIEDA | расскажи о компании | кто такие whieda",
            [],
            "qa/company_knowledge/COMPANY_INFORMATION_REQUEST_FOR_OWNER.md",
            "pending_owner",
        ),
        (
            "CPR-002",
            "company_activity",
            "чем занимается компания | чем занимается whieda",
            [],
            "qa/company_knowledge/COMPANY_INFORMATION_REQUEST_FOR_OWNER.md",
            "pending_owner",
        ),
        (
            "CPR-003",
            "history_founding",
            "история whieda | когда основана | год основания",
            ["CK-HIS-008"],
            "qa/company_knowledge/COMPANY_INFORMATION_REQUEST_FOR_OWNER.md",
            "pending_owner",
        ),
        (
            "CPR-004",
            "production_location",
            "где производят | где завод | страна производства",
            ["CK-PRD-037"],
            "qa/company_knowledge/COMPANY_INFORMATION_REQUEST_FOR_OWNER.md",
            "pending_owner",
        ),
        (
            "CPR-005",
            "public_representatives",
            "кто представляет компанию | публичные лидеры whieda",
            [],
            "qa/company_knowledge/COMPANY_INFORMATION_REQUEST_FOR_OWNER.md",
            "pending_owner",
        ),
        (
            "CPR-006",
            "news_and_events",
            "где новости | мероприятия whieda | официальный канал",
            evt_ids,
            evt_ref,
            "accepted" if evt_ids else "pending_owner",
        ),
    ]

    rows: list[dict[str, str]] = []
    for reply_id, intent, examples, fact_ids, source_ref, owner_status in intents:
        answer = _join(fact_ids) if owner_status == "accepted" else ""
        if intent == "news_and_events" and answer:
            answer = answer.replace("контакт Елена Дацкевич", "уточните у наставника")
        rows.append(
            {
                "reply_id": reply_id,
                "intent": intent,
                "question_examples": examples,
                "answer_text": answer,
                "fact_ids": "|".join(fact_ids),
                "source_ref": source_ref,
                "owner_status": owner_status,
                "answer_length": "full" if len(answer) > 180 else "short",
            }
        )
    return rows


def build_partner_contacts(bundle, facts: list[dict[str, str]]) -> list[dict[str, str]]:  # noqa: ANN001
    rows: list[dict[str, str]] = []
    snap = bundle.snapshot_id

    for idx, row in enumerate(bundle.layers["partners_ref"], start=2):
        pid = str(row.get("partner_id") or "")
        if pid == "nnm":
            rows.append(
                {
                    "contact_id": "PNC-P-NNM",
                    "display_name": str(row.get("display_name") or ""),
                    "contact_type": "platform_owner_internal",
                    "region": str(row.get("country") or "GLOBAL"),
                    "telegram": str(row.get("telegram_username") or ""),
                    "ref_url": str(row.get("ref_url") or ""),
                    "event_public": "no",
                    "requires_ref_context": "no",
                    "source_ref": f"{SNAPSHOT_REL.format(snapshot_id=snap)}/partners_ref.tsv",
                    "source_locator": f"row partner_id={pid} (tsv line {idx})",
                    "owner_status": "pending_review",
                    "notes": "platform_internal only; not for public company reply",
                }
            )
            continue
        rows.append(
            {
                "contact_id": f"PNC-P-{pid.upper()}",
                "display_name": str(row.get("display_name") or ""),
                "contact_type": "partner_consultant",
                "region": str(row.get("service_location") or row.get("country") or "GLOBAL"),
                "telegram": str(row.get("telegram_username") or ""),
                "ref_url": str(row.get("ref_url") or ""),
                "event_public": "no",
                "requires_ref_context": "yes",
                "source_ref": f"{SNAPSHOT_REL.format(snapshot_id=snap)}/partners_ref.tsv",
                "source_locator": f"row partner_id={pid} (tsv line {idx})",
                "owner_status": "accepted",
                "notes": str(row.get("notes") or "")[:160],
            }
        )

    for idx, row in enumerate(bundle.layers["structure_owners"], start=2):
        code = str(row.get("structure_code") or "")
        struct_status = str(row.get("status") or "")
        if struct_status == "prepared_not_routed":
            owner_status = "pending_review"
            note = "prepared_not_routed: not auto-routed to user"
        else:
            owner_status = "accepted" if struct_status == "active" else "pending_review"
            note = f"status={struct_status}"
        rows.append(
            {
                "contact_id": f"PNC-S-{code.upper()}",
                "display_name": str(row.get("leader_name") or ""),
                "contact_type": "structure_mentor",
                "region": "GLOBAL",
                "telegram": f"@{row.get('leader_username')}",
                "ref_url": str(row.get("invite_link") or ""),
                "event_public": "no",
                "requires_ref_context": "yes" if code != "general" else "no",
                "source_ref": f"{SNAPSHOT_REL.format(snapshot_id=snap)}/structure_owners.tsv",
                "source_locator": f"row structure_code={code} (tsv line {idx})",
                "owner_status": owner_status,
                "notes": note,
            }
        )

    for idx, row in enumerate(bundle.layers["events"], start=2):
        rows.append(
            {
                "contact_id": "PNC-EVT-MINSK",
                "display_name": str(row.get("title") or ""),
                "contact_type": "event_public",
                "region": f"{row.get('city')}, {row.get('country')}",
                "telegram": "",
                "ref_url": str(row.get("address") or ""),
                "event_public": "yes",
                "requires_ref_context": "no",
                "source_ref": f"{SNAPSHOT_REL.format(snapshot_id=snap)}/events.tsv",
                "source_locator": f"row {row.get('event_id')} (tsv line {idx})",
                "owner_status": "accepted",
                "notes": f"Schedule: {row.get('recurrence_rule')}; organizer via mentor routing",
            }
        )

    for idx, row in enumerate(bundle.layers["community_resources"], start=2):
        rows.append(
            {
                "contact_id": "PNC-COMM-WORLD-CLUB",
                "display_name": str(row.get("title") or ""),
                "contact_type": "community_channel",
                "region": str(row.get("country") or ""),
                "telegram": str(row.get("url") or ""),
                "ref_url": str(row.get("url") or ""),
                "event_public": "yes",
                "requires_ref_context": "no",
                "source_ref": f"{SNAPSHOT_REL.format(snapshot_id=snap)}/community_resources.tsv",
                "source_locator": f"row {row.get('resource_id')} (tsv line {idx})",
                "owner_status": "accepted",
                "notes": "Official public channel",
            }
        )

    return rows


def public_reply_is_clean(row: dict[str, str], facts: list[dict[str, str]]) -> bool:
    text = str(row.get("answer_text") or "").casefold()
    if any(bad.casefold() in text for bad in PUBLIC_REPLY_FORBIDDEN):
        return False
    fact_map = {str(f["fact_id"]): f for f in facts}
    for fid in str(row.get("fact_ids") or "").split("|"):
        if not fid:
            continue
        fact = fact_map.get(fid)
        if not fact or fact.get("audience_layer") != "company_public":
            return False
    return True
