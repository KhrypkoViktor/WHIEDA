"""Enums and default triage routing for advisor gap review queue."""

from __future__ import annotations

GAP_KINDS = frozenset(
    {
        "unknown_product",
        "ambiguous_product",
        "unknown_followup",
        "unsupported_topic",
        "missing_resource",
        "medical_or_safety_boundary",
    }
)

STATUSES = frozenset(
    {"new", "triaged", "in_review", "approved_candidate", "rejected", "resolved"}
)

PRIORITIES = frozenset({"p0", "p1", "p2", "p3"})

OWNER_ROLES = frozenset({"owner", "medical", "business", "admin"})

CANDIDATE_TYPES = frozenset(
    {
        "alias",
        "clarification_rule",
        "resource_link",
        "business_faq",
        "medical_review",
        "safety_review",
        "intent_gap",
        "none",
    }
)

PATCHABLE_FIELDS = frozenset(
    {"status", "priority", "owner_role", "owner_name", "operator_note", "candidate_type"}
)

DEFAULT_TRIAGE: dict[str, dict[str, str]] = {
    "medical_or_safety_boundary": {
        "priority": "p0",
        "owner_role": "medical",
        "candidate_type": "medical_review",
    },
    "missing_resource": {
        "priority": "p1",
        "owner_role": "admin",
        "candidate_type": "resource_link",
    },
    "ambiguous_product": {
        "priority": "p1",
        "owner_role": "business",
        "candidate_type": "clarification_rule",
    },
    "unknown_product": {
        "priority": "p1",
        "owner_role": "business",
        "candidate_type": "alias",
    },
    "unknown_followup": {
        "priority": "p2",
        "owner_role": "owner",
        "candidate_type": "intent_gap",
    },
    "unsupported_topic": {
        "priority": "p3",
        "owner_role": "owner",
        "candidate_type": "intent_gap",
    },
}

OWNER_ROLE_LABELS = {
    "owner": "Владелец",
    "medical": "Медицинский эксперт",
    "business": "Бизнес / формулировки",
    "admin": "Администратор",
}

PRIORITY_LABELS = {
    "p0": "Срочно",
    "p1": "Высокий",
    "p2": "Средний",
    "p3": "Низкий",
}

NOTE_MAX_LEN = 2000
