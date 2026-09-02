"""Machine-readable event type statuses for cabinet overview and P0.1A planning."""

from __future__ import annotations

from typing import Any

# Status vocabulary: implemented | staging | production | gap | deprecated
EVENT_TYPE_REGISTRY: dict[str, dict[str, Any]] = {
    "visitor_first_seen": {"status": "implemented", "channel": "core_journey", "production": False},
    "route_opened": {"status": "implemented", "channel": "core_journey", "production": False},
    "ref_seen": {"status": "implemented", "channel": "core_journey", "production": False},
    "market_selected": {"status": "implemented", "channel": "core_journey", "production": False},
    "campaign_attributed": {"status": "implemented", "channel": "core_journey", "production": False},
    "telegram_link_created": {"status": "implemented", "channel": "core_identity", "production": False},
    "telegram_link_exchanged": {"status": "implemented", "channel": "core_identity", "production": False},
    "lead_created": {"status": "implemented", "channel": "core_leads", "production": True},
    "lead_form_start": {"status": "production", "channel": "yandex_metrika", "production": True},
    "lead_submit_success": {"status": "production", "channel": "yandex_metrika", "production": True},
    "ref_visit": {"status": "production", "channel": "yandex_metrika", "production": True},
    "admin_login_approved": {"status": "implemented", "channel": "admin_audit", "production": False},
    "admin_sensitive_view": {"status": "implemented", "channel": "admin_audit", "production": False},
    "website_events_writer": {"status": "gap", "channel": "legacy_ddl", "production": False},
}


def registry_summary() -> dict[str, Any]:
    counts: dict[str, int] = {}
    for meta in EVENT_TYPE_REGISTRY.values():
        status = str(meta.get("status", "gap"))
        counts[status] = counts.get(status, 0) + 1
    return {"total": len(EVENT_TYPE_REGISTRY), "by_status": counts}
