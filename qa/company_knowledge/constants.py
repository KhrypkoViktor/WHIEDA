"""Company knowledge pack constants."""

from __future__ import annotations

FACT_COLUMNS = (
    "fact_id",
    "topic",
    "audience_layer",
    "question_examples",
    "fact_text",
    "answer_scope",
    "source_kind",
    "source_ref",
    "source_locator",
    "provenance_status",
    "confidence",
    "owner_status",
    "notes",
)

LEADER_COLUMNS = (
    "leader_id",
    "display_name",
    "role",
    "region",
    "public_bio",
    "source_ref",
    "source_locator",
    "provenance_status",
    "owner_status",
    "notes",
)

PUBLIC_REPLY_COLUMNS = (
    "reply_id",
    "intent",
    "question_examples",
    "answer_text",
    "fact_ids",
    "source_ref",
    "owner_status",
    "answer_length",
)

PARTNER_CONTACT_COLUMNS = (
    "contact_id",
    "display_name",
    "contact_type",
    "region",
    "telegram",
    "ref_url",
    "event_public",
    "requires_ref_context",
    "source_ref",
    "source_locator",
    "owner_status",
    "notes",
)

TOPICS = (
    "company_overview",
    "history",
    "leadership",
    "production",
    "technology",
    "partner_business",
    "events",
    "contacts",
)

AUDIENCE_LAYERS = frozenset({"company_public", "partner_network", "platform_internal"})
PROVENANCE = frozenset({"verified", "company_stated", "missing", "internal_doc"})
OWNER_STATUS = frozenset({"accepted", "needs_owner_input", "pending_review"})
CONFIDENCE = frozenset({"high", "medium", "low"})
ANSWER_LENGTH = frozenset({"short", "full"})

SNAPSHOT_REL = "n8n/live-exports/structured-master/{snapshot_id}"

PUBLIC_REPLY_FORBIDDEN = (
    "единую систему роста партнёрской структуры",
    "контент → сайт → советник",
    "Core хранит",
    "SQL-ответы",
    "RAG-фрагменты",
    "RAW → distillate",
    "Владелец платформы и продающего языка",
    "structured master",
    "tenant",
)
