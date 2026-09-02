from __future__ import annotations

from typing import Any

from app.admin.masking import mask_contact

GAP_FIELDS = frozenset(
    {
        "partner_tier",
        "subscription_cost",
        "last_activity_at",
        "partners_sync_last_run",
        "structured_sync_last_run",
    }
)

LEAD_METADATA_ALLOWLIST = frozenset(
    {
        "visitor_session_id",
        "market_id",
        "center_id",
        "country_iso",
        "city_selected",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "active_ref",
        "landing_url",
    }
)

FORBIDDEN_RESPONSE_KEYS = frozenset(
    {
        "tenant_id",
        "token",
        "secret",
        "password",
        "webhook",
        "service_account",
    }
)


def gap_meta(*fields: str) -> dict[str, str]:
    return {name: "gap" for name in fields}


def pick_lead_metadata(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {k: raw[k] for k in LEAD_METADATA_ALLOWLIST if k in raw and raw[k] is not None}


__all__ = [
    "FORBIDDEN_RESPONSE_KEYS",
    "GAP_FIELDS",
    "gap_meta",
    "mask_contact",
    "pick_lead_metadata",
]
