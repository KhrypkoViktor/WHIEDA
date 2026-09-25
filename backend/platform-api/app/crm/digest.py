"""Morning message of the partner diary (worker step, every 5 minutes).

For every account whose local time is in [09:00, 09:10) and who has something
due today: one ``platform_outbox`` row ``crm_daily_digest`` (status «scheduled»,
``due_at = now``) with the key ``crm_digest:<account_id>:<local date>`` — the
second pass inside the window finds the key and adds nothing. Sending is the
generic ``app.jobs.worker.process_due_notifications``; nothing is sent here.

One query per tenant reads everything a pass needs — each account's local time,
chat, what is due today by step and the partner subscription — and one
connection writes the rows. Nothing due → nothing is queued. A person who lost
access (PRO ended, not in the pilot) gets no message: the link would show a lock.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, time, timezone
from typing import Any

from app.crm.rules import digest_idempotency_key, digest_text, in_digest_window
from app.crm.service import crm_url, lock_reason, safe_error, viewer_from_row
from app.db import fetch_all, tenant_connection
from app.jobs.outbox import enqueue_outbox_event

logger = logging.getLogger(__name__)

EVENT_TYPE = "crm_daily_digest"


async def _accounts_due(tenant_id: str) -> list[dict[str, Any]]:
    """Accounts with something due today (by their own timezone), in one query."""
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            """
            with acc as (
              select a.account_id, a.telegram_user_id, (now() at time zone a.timezone) as local_now
              from platform_accounts a
              where a.tenant_id = %(tenant_id)s
            ),
            due as (
              select c.account_id, coalesce(c.next_step, 'ping') as step, count(*)::int as n
              from crm_contacts c
              join acc on acc.account_id = c.account_id
              where c.tenant_id = %(tenant_id)s
                and c.next_at is not null
                and c.next_at <= acc.local_now::date
              group by 1, 2
            ),
            counts as (
              select account_id, jsonb_object_agg(step, n) as counts
              from due
              group by account_id
            )
            select acc.account_id::text as account_id, acc.telegram_user_id, acc.local_now,
                   coalesce(chat.telegram_chat_id, acc.telegram_user_id::text) as chat_id,
                   counts.counts,
                   sub.ref_code, sub.public_profile, sub.paid_until
            from acc
            join counts on counts.account_id = acc.account_id
            left join lateral (
              select l.telegram_chat_id
              from lead_actors l
              where l.tenant_id = %(tenant_id)s
                and l.telegram_user_id = acc.telegram_user_id
                and l.telegram_chat_id is not null
              order by l.active desc
              limit 1
            ) chat on true
            left join lateral (
              -- the same «best profile» as resolve_partner_subscription_by_telegram_user_id
              select rp.ref_code, rp.public_profile, ps.paid_until
              from lead_actors la
              join referral_profiles rp
                on rp.tenant_id = la.tenant_id and rp.owner_id = la.actor_id and rp.enabled = true
              left join partner_subscriptions ps
                on ps.tenant_id = rp.tenant_id and ps.ref_code = rp.ref_code
              where la.tenant_id = %(tenant_id)s
                and la.telegram_user_id = acc.telegram_user_id
                and la.active = true
              order by ps.paid_until desc nulls last, rp.ref_code
              limit 1
            ) sub on true
            """,
            {"tenant_id": tenant_id},
        )


def plan_digests(
    rows: list[dict[str, Any]],
    binding_id: str,
    *,
    in_window: Callable[[time], bool] = in_digest_window,
) -> list[dict[str, Any]]:
    """Rows of one tenant → outbox events to queue (no I/O)."""
    planned = []
    for row in rows:
        local_now: datetime = row["local_now"]
        if not in_window(local_now.time()):
            continue
        viewer = viewer_from_row(row["telegram_user_id"], row.get("ref_code"), row.get("public_profile"), row.get("paid_until"))
        if lock_reason(viewer) is not None:
            continue
        url = crm_url(viewer, "today")
        text = digest_text(dict(row.get("counts") or {}), url)
        if text is None:
            continue
        planned.append(
            {
                "idempotency_key": digest_idempotency_key(row["account_id"], local_now.date()),
                "payload": {
                    "chat_id": str(row["chat_id"]),
                    "text": text,
                    "url": url,
                    "binding_id": binding_id,
                    "account_id": row["account_id"],
                },
            }
        )
    return planned


async def enqueue_crm_digests(
    tenant_bindings: dict[str, str],
    *,
    in_window: Callable[[time], bool] = in_digest_window,
) -> int:
    """``tenant_bindings``: tenant_id → the bot binding that will send. Returns
    how many new rows were queued."""
    queued = 0
    for tenant_id, binding_id in tenant_bindings.items():
        try:
            planned = plan_digests(await _accounts_due(tenant_id), binding_id, in_window=in_window)
            if not planned:
                continue
            now = datetime.now(timezone.utc)
            async with tenant_connection(tenant_id) as conn:
                for item in planned:
                    row = await enqueue_outbox_event(
                        conn,
                        tenant_id=tenant_id,
                        event_type=EVENT_TYPE,
                        idempotency_key=item["idempotency_key"],
                        payload=item["payload"],
                        due_at=now,
                    )
                    queued += 1 if row and row.get("created") else 0
        except Exception as exc:
            logger.warning("crm_digest_plan_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})
    return queued
