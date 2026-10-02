"""Morning message of WWC CRM (worker step, every 5 minutes).

For every account whose local time is in [09:00, 09:10) and who has something
today — a meeting or a step due — one ``platform_outbox`` row
``crm_daily_digest`` (status «scheduled», ``due_at = now``) with the key
``crm_digest:<account_id>:<local date>``: the second pass inside the window
finds the key and adds nothing. Sending is the generic
``app.jobs.worker.process_due_notifications``; nothing is sent here.

CRM v2: the message names people (up to 10 lines) in three groups — meetings
today, call, remind — and has buttons «Открыть Сегодня» and one per the first
three people (``app.crm.messages``). Two reads per tenant: a cheap one for
every account (local time, counts, chat, subscription), then the names only
for the accounts that get a message now. Nothing today → nothing is queued.
A person who lost access (PRO ended, not in the pilot) gets no message: the
link would show a lock. Names and phones never go to the logs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, time, timezone
from typing import Any

from app.crm.messages import digest_message
from app.crm.rules import digest_idempotency_key, in_digest_window, today_sections
from app.crm.service import crm_contact_url, crm_url, lock_reason, safe_error, viewer_from_row
from app.db import fetch_all, tenant_connection
from app.jobs.outbox import enqueue_outbox_event

logger = logging.getLogger(__name__)

EVENT_TYPE = "crm_daily_digest"


def chat_lateral(user_column: str) -> str:
    """The chat the bot writes to: the person's lead_actors chat, else the user id."""
    return f"""
            left join lateral (
              select l.telegram_chat_id
              from lead_actors l
              where l.tenant_id = %(tenant_id)s
                and l.telegram_user_id = {user_column}
                and l.telegram_chat_id is not null
              order by l.active desc
              limit 1
            ) chat on true
    """


def subscription_lateral(user_column: str) -> str:
    """The same «best profile» as resolve_partner_subscription_by_telegram_user_id."""
    return f"""
            left join lateral (
              select rp.ref_code, rp.public_profile, ps.paid_until
              from lead_actors la
              join referral_profiles rp
                on rp.tenant_id = la.tenant_id and rp.owner_id = la.actor_id and rp.enabled = true
              left join partner_subscriptions ps
                on ps.tenant_id = rp.tenant_id and ps.ref_code = rp.ref_code
              where la.tenant_id = %(tenant_id)s
                and la.telegram_user_id = {user_column}
                and la.active = true
              order by ps.paid_until desc nulls last, rp.ref_code
              limit 1
            ) sub on true
    """


async def _accounts_due(tenant_id: str) -> list[dict[str, Any]]:
    """Accounts with a meeting or a step today (by their own timezone), in one query."""
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            f"""
            with acc as (
              select a.account_id, a.telegram_user_id, a.timezone,
                     (now() at time zone a.timezone) as local_now
              from platform_accounts a
              where a.tenant_id = %(tenant_id)s
            ),
            due as (
              select c.account_id, count(*)::int as n
              from crm_contacts c
              join acc on acc.account_id = c.account_id
              where c.tenant_id = %(tenant_id)s
                and c.deleted_at is null
                and (
                  (c.next_at is not null and c.next_at <= acc.local_now::date)
                  or (c.meeting_at is not null
                      and (c.meeting_at at time zone acc.timezone)::date = acc.local_now::date)
                )
              group by c.account_id
            )
            select acc.account_id::text as account_id, acc.telegram_user_id, acc.local_now,
                   coalesce(chat.telegram_chat_id, acc.telegram_user_id::text) as chat_id,
                   due.n as due_count,
                   sub.ref_code, sub.public_profile, sub.paid_until
            from acc
            join due on due.account_id = acc.account_id
            {chat_lateral("acc.telegram_user_id")}
            {subscription_lateral("acc.telegram_user_id")}
            """,
            {"tenant_id": tenant_id},
        )


async def _digest_contacts(tenant_id: str, account_ids: list[str]) -> list[dict[str, Any]]:
    """Today's people of the chosen accounts: due steps and meetings today."""
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            """
            select c.account_id::text as account_id, c.contact_id::text as contact_id, c.name,
                   c.next_step, c.next_at, c.meeting_at,
                   coalesce((c.meeting_at at time zone a.timezone)::date
                            = (now() at time zone a.timezone)::date, false) as meeting_today,
                   to_char(c.meeting_at at time zone a.timezone, 'HH24:MI') as meeting_local
            from crm_contacts c
            join platform_accounts a on a.tenant_id = c.tenant_id and a.account_id = c.account_id
            where c.tenant_id = %(tenant_id)s
              and c.account_id = any(%(ids)s::uuid[])
              and c.deleted_at is null
              and (
                (c.next_at is not null and c.next_at <= (now() at time zone a.timezone)::date)
                or (c.meeting_at is not null
                    and (c.meeting_at at time zone a.timezone)::date = (now() at time zone a.timezone)::date)
              )
            order by c.account_id, c.priority desc, c.next_at nulls last, c.updated_at desc, c.contact_id
            """,
            {"tenant_id": tenant_id, "ids": account_ids},
        )


def select_digest_accounts(
    rows: list[dict[str, Any]],
    *,
    in_window: Callable[[time], bool] = in_digest_window,
) -> list[dict[str, Any]]:
    """Accounts in their 09:00 window, with something today and with access (no I/O)."""
    chosen = []
    for row in rows:
        local_now: datetime = row["local_now"]
        if not in_window(local_now.time()) or int(row.get("due_count") or 0) <= 0:
            continue
        viewer = viewer_from_row(row["telegram_user_id"], row.get("ref_code"), row.get("public_profile"), row.get("paid_until"))
        if lock_reason(viewer) is not None:
            continue
        chosen.append({**row, "viewer": viewer})
    return chosen


def plan_digests(
    accounts: list[dict[str, Any]],
    contacts: list[dict[str, Any]],
    binding_id: str,
) -> list[dict[str, Any]]:
    """Chosen accounts + their people today → outbox events (no I/O)."""
    by_account: dict[str, list[dict[str, Any]]] = {}
    for row in contacts:
        by_account.setdefault(str(row["account_id"]), []).append(row)
    planned = []
    for account in accounts:
        viewer = account.get("viewer") or viewer_from_row(
            account["telegram_user_id"], account.get("ref_code"), account.get("public_profile"), account.get("paid_until")
        )
        local_today = account["local_now"].date()
        sections = today_sections(by_account.get(str(account["account_id"]), []), local_today, split_overdue=False)
        today_url = crm_url(viewer, "today")
        message = digest_message(
            sections,
            local_today,
            today_url=today_url,
            contact_url=lambda contact_id, viewer=viewer: crm_contact_url(viewer, contact_id),
        )
        if message is None:
            continue
        planned.append(
            {
                "idempotency_key": digest_idempotency_key(account["account_id"], local_today),
                "payload": {
                    "chat_id": str(account["chat_id"]),
                    "text": message["text"],
                    "reply_markup": message["reply_markup"],
                    "url": today_url,
                    "binding_id": binding_id,
                    "account_id": account["account_id"],
                    # The worker signs the site buttons in for this person when it sends.
                    "site_login_user_id": int(account["telegram_user_id"]),
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
            accounts = select_digest_accounts(await _accounts_due(tenant_id), in_window=in_window)
            if not accounts:
                continue
            contacts = await _digest_contacts(tenant_id, [account["account_id"] for account in accounts])
            planned = plan_digests(accounts, contacts, binding_id)
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
