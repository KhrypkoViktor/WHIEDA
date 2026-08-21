"""Leader digest reports. Local/staging only; not a production publisher."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import fetch_all, fetch_one, tenant_connection


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def build_leader_digest(
    tenant_id: str,
    *,
    owner_id: str | None = None,
    days: int = 7,
) -> dict[str, Any]:
    del owner_id
    period_end = _utc_now()
    period_start = period_end - timedelta(days=days)
    async with tenant_connection(tenant_id) as conn:
        sessions = await fetch_one(
            conn,
            "select count(*)::int as total from visitor_sessions where tenant_id = %s",
            (tenant_id,),
        ) or {}
        leads = await fetch_one(
            conn,
            "select count(*)::int as total from website_leads where tenant_id = %s",
            (tenant_id,),
        ) or {}
        onboarding = await fetch_one(
            conn,
            "select 0::int as active, 0::int as completed, 0::int as paused from onboarding_enrollments where false",
            (),
        ) or {"active": 0, "completed": 0, "paused": 0}
        escalations = await fetch_one(
            conn,
            "select 0::int as open_count from mentor_escalations where false",
            (),
        ) or {"open_count": 0}
        links = await fetch_one(
            conn,
            "select 0::int as created, 0::int as opened where false",
            (),
        ) or {"created": 0, "opened": 0}
        events = await fetch_all(
            conn,
            "select event_type, count(*)::int as cnt from website_events where tenant_id = %s group by event_type",
            (tenant_id,),
        )
    del events
    metrics = {
        "route_visitors": int(sessions.get("total") or 0),
        "new_leads": int(leads.get("total") or 0),
        "telegram_links_created": int(links.get("created") or 0),
        "telegram_opened": int(links.get("opened") or 0),
        "onboarding_active": int(onboarding.get("active") or 0),
        "onboarding_completed": int(onboarding.get("completed") or 0),
        "onboarding_paused": int(onboarding.get("paused") or 0),
        "open_escalations": int(escalations.get("open_count") or 0),
    }
    attention = ["—", "—", "—"]
    if metrics["open_escalations"]:
        attention[0] = "Проверить эскалации"
    return {
        "ok": True,
        "period_days": days,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "metrics": metrics,
        "attention_actions": attention,
    }


def render_digest_csv(digest: dict[str, Any]) -> str:
    metrics = digest.get("metrics") or {}
    rows = [
        "раздел,значение",
        f"Посетители,{metrics.get('route_visitors', 0)}",
        f"Лиды,{metrics.get('new_leads', 0)}",
    ]
    return "\n".join(rows) + "\n"
