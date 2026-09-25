"""Morning message of the partner diary (worker step, every 5 minutes).

For every account whose local time is in [09:00, 09:10) and who has something
due today: one ``platform_outbox`` row ``crm_daily_digest`` with ``due_at = now``
and the key ``crm_digest:<account_id>:<local date>`` — the second pass inside the
window finds the key and adds nothing. Sending is the generic
``app.jobs.worker.process_due_notifications``; nothing is sent from here.

Nothing due → nothing is queued. A person who lost access (PRO ended, not in
the pilot) gets no message: the link would only show a lock.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, time, timezone
from typing import Any

from app.crm.rules import digest_idempotency_key, digest_text, in_digest_window
from app.crm.service import crm_url, load_viewer, lock_reason
from app.db import fetch_all, tenant_connection
from app.jobs.outbox import enqueue_outbox_event

logger = logging.getLogger(__name__)

EVENT_TYPE = "crm_daily_digest"


async def _accounts_with_contacts(tenant_id: str) -> list[dict[str, Any]]:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            """
            select a.account_id::text as account_id, a.telegram_user_id, a.timezone,
                   (now() at time zone a.timezone) as local_now,
                   coalesce(la.telegram_chat_id, a.telegram_user_id::text) as chat_id
            from platform_accounts a
            left join lateral (
              select l.telegram_chat_id
              from lead_actors l
              where l.tenant_id = a.tenant_id
                and l.telegram_user_id = a.telegram_user_id
                and l.telegram_chat_id is not null
              order by l.active desc
              limit 1
            ) la on true
            where a.tenant_id = %s
              and exists (
                select 1 from crm_contacts c
                where c.tenant_id = a.tenant_id and c.account_id = a.account_id
              )
            """,
            (tenant_id,),
        )


async def _due_counts(tenant_id: str, account_id: str, local_date: Any) -> dict[str, int]:
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select coalesce(next_step, 'ping') as step, count(*) as n
            from crm_contacts
            where tenant_id = %s and account_id = %s::uuid
              and next_at is not null and next_at <= %s
            group by 1
            """,
            (tenant_id, account_id, local_date),
        )
    return {row["step"]: int(row["n"]) for row in rows}


async def enqueue_crm_digests(
    tenant_bindings: dict[str, str],
    *,
    in_window: Callable[[time], bool] = in_digest_window,
) -> int:
    """``tenant_bindings``: tenant_id → the bot binding that will send. Returns
    how many new rows were queued."""
    queued = 0
    for tenant_id, binding_id in tenant_bindings.items():
        for account in await _accounts_with_contacts(tenant_id):
            local_now: datetime = account["local_now"]
            if not in_window(local_now.time()):
                continue
            try:
                queued += await _plan_one(tenant_id, binding_id, account, local_now)
            except Exception:
                logger.exception(
                    "crm_digest_plan_failed", extra={"tenant_id": tenant_id, "account_id": account["account_id"]}
                )
    return queued


async def _plan_one(tenant_id: str, binding_id: str, account: dict[str, Any], local_now: datetime) -> int:
    local_date = local_now.date()
    counts = await _due_counts(tenant_id, account["account_id"], local_date)
    if not counts:
        return 0
    viewer = await load_viewer(tenant_id, int(account["telegram_user_id"]))
    if lock_reason(viewer) is not None:
        return 0
    url = crm_url(viewer, "today")
    text = digest_text(counts, url)
    if text is None:
        return 0
    async with tenant_connection(tenant_id) as conn:
        row = await enqueue_outbox_event(
            conn,
            tenant_id=tenant_id,
            event_type=EVENT_TYPE,
            idempotency_key=digest_idempotency_key(account["account_id"], local_date),
            payload={
                "chat_id": str(account["chat_id"]),
                "text": text,
                "url": url,
                "binding_id": binding_id,
                "account_id": account["account_id"],
            },
            due_at=datetime.now(timezone.utc),
        )
    return 1 if row and row.get("created") else 0
