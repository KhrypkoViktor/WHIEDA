"""Pilot telemetry aggregation (Stage 7 — SQL only)."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from typing import Any

from app.db import fetch_all, fetch_one, tenant_connection

OUTCOME_TYPES = frozenset({"contacted", "meeting", "registered", "refused", "unknown"})


async def record_outcome(
    tenant_id: str,
    *,
    outcome_type: str,
    idempotency_key: str,
    session_id: str | None = None,
    source_route: str | None = None,
    payload: dict | None = None,
) -> dict[str, Any]:
    if outcome_type not in OUTCOME_TYPES:
        return {"ok": False, "error": "invalid_outcome_type"}

    async with tenant_connection(tenant_id) as conn:
        existing = await fetch_one(
            conn,
            "select outcome_id from pilot_outcome_events where tenant_id = %s and idempotency_key = %s",
            (tenant_id, idempotency_key),
        )
        if existing:
            return {"ok": True, "created": False, "outcome_id": str(existing["outcome_id"])}

        outcome_id = str(uuid.uuid4())
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into pilot_outcome_events (
                  outcome_id, tenant_id, session_id, outcome_type, source_route,
                  idempotency_key, payload
                ) values (%s::uuid, %s, %s::uuid, %s, %s, %s, %s::jsonb)
                """,
                (
                    outcome_id,
                    tenant_id,
                    session_id,
                    outcome_type,
                    source_route,
                    idempotency_key,
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
    return {"ok": True, "created": True, "outcome_id": outcome_id}


async def refresh_daily_metrics(tenant_id: str, *, metric_date: date | None = None) -> dict[str, Any]:
    day = metric_date or datetime.now(timezone.utc).date()
    start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
    end = datetime.combine(day, datetime.max.time(), tzinfo=timezone.utc)

    async with tenant_connection(tenant_id) as conn:
        events = await fetch_one(
            conn,
            """
            select
              count(*) filter (where event_type = 'route_opened') as route_opens,
              count(*) filter (where event_type = 'product_viewed') as product_views,
              count(*) filter (where event_type = 'useful_action_completed') as useful_actions,
              count(*) filter (where event_type = 'advisor_question') as advisor_questions,
              count(*) filter (where event_type = 'telegram_link_created') as telegram_links,
              count(*) filter (where event_type = 'telegram_opened') as telegram_opened,
              count(*) filter (where event_type = 'lead_created') as leads_created
            from interaction_events
            where tenant_id = %s and created_at >= %s and created_at <= %s
            """,
            (tenant_id, start, end),
        )
        onboarding = await fetch_one(
            conn,
            """
            select
              count(*) filter (where status = 'active') as active,
              count(*) filter (where status = 'completed') as completed
            from onboarding_enrollments
            where tenant_id = %s and started_at >= %s and started_at <= %s
            """,
            (tenant_id, start, end),
        )

        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into pilot_daily_metrics (
                  tenant_id, metric_date, route_type,
                  route_opens, product_views, useful_actions, advisor_questions,
                  telegram_links_created, telegram_opened, leads_created,
                  onboarding_enrollments, onboarding_completions
                ) values (%s, %s, 'all', %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (tenant_id, metric_date, route_type) do update
                  set route_opens = excluded.route_opens,
                      product_views = excluded.product_views,
                      useful_actions = excluded.useful_actions,
                      advisor_questions = excluded.advisor_questions,
                      telegram_links_created = excluded.telegram_links_created,
                      telegram_opened = excluded.telegram_opened,
                      leads_created = excluded.leads_created,
                      onboarding_enrollments = excluded.onboarding_enrollments,
                      onboarding_completions = excluded.onboarding_completions,
                      updated_at = now()
                """,
                (
                    tenant_id,
                    day,
                    int(events.get("route_opens") or 0),
                    int(events.get("product_views") or 0),
                    int(events.get("useful_actions") or 0),
                    int(events.get("advisor_questions") or 0),
                    int(events.get("telegram_links") or 0),
                    int(events.get("telegram_opened") or 0),
                    int(events.get("leads_created") or 0),
                    int(onboarding.get("active") or 0) + int(onboarding.get("completed") or 0),
                    int(onboarding.get("completed") or 0),
                ),
            )

    return {
        "ok": True,
        "metric_date": day.isoformat(),
        "events": dict(events or {}),
        "onboarding": dict(onboarding or {}),
    }


async def get_pilot_summary(tenant_id: str, *, days: int = 7) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select metric_date, route_opens, leads_created, telegram_links_created,
                   onboarding_enrollments, onboarding_completions
            from pilot_daily_metrics
            where tenant_id = %s
              and metric_date >= current_date - %s::int
            order by metric_date desc
            """,
            (tenant_id, max(1, min(days, 90))),
        )
    totals = {
        "route_opens": sum(int(r.get("route_opens") or 0) for r in rows),
        "leads_created": sum(int(r.get("leads_created") or 0) for r in rows),
        "telegram_links_created": sum(int(r.get("telegram_links_created") or 0) for r in rows),
        "onboarding_enrollments": sum(int(r.get("onboarding_enrollments") or 0) for r in rows),
    }
    return {"ok": True, "days": days, "totals": totals, "daily": rows}
