"""«Через час встреча» (worker step, every 5 minutes with the morning plan).

A card whose meeting is 50–65 minutes away and has no reminder yet gets one
``platform_outbox`` row ``crm_meeting_reminder`` (status «scheduled», due now):
the name, the phone and a button «Открыть карточку». Twice-proof:
``crm_contacts.meeting_reminded_at`` is set in the same transaction as the
outbox row (only while it is still empty and the meeting time unchanged), and
the outbox key is ``crm_meeting:<contact>:<meeting epoch>``. Moving or clearing
the meeting resets ``meeting_reminded_at`` (service), so the new time gets
its own reminder. Same audience rules as the morning message.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.crm.digest import chat_lateral, subscription_lateral
from app.crm.messages import meeting_reminder_message
from app.crm.rules import MEETING_REMIND_FROM, MEETING_REMIND_TO, meeting_idempotency_key
from app.crm.service import crm_contact_url, lock_reason, safe_error, viewer_from_row
from app.db import fetch_all, fetch_one, tenant_connection
from app.jobs.outbox import enqueue_outbox_event

logger = logging.getLogger(__name__)

EVENT_TYPE = "crm_meeting_reminder"


async def _meetings_due(tenant_id: str) -> list[dict[str, Any]]:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            f"""
            select c.contact_id::text as contact_id, c.account_id::text as account_id,
                   a.telegram_user_id, c.name, c.phone_e164, c.phone_raw, c.meeting_at,
                   to_char(c.meeting_at at time zone a.timezone, 'HH24:MI') as meeting_local,
                   coalesce(chat.telegram_chat_id, a.telegram_user_id::text) as chat_id,
                   sub.ref_code, sub.public_profile, sub.paid_until
            from crm_contacts c
            join platform_accounts a on a.tenant_id = c.tenant_id and a.account_id = c.account_id
            {chat_lateral("a.telegram_user_id")}
            {subscription_lateral("a.telegram_user_id")}
            where c.tenant_id = %(tenant_id)s
              and c.meeting_at is not null
              and c.meeting_reminded_at is null
              and c.deleted_at is null
              and c.meeting_at > now() + %(remind_from)s
              and c.meeting_at <= now() + %(remind_to)s
            order by c.meeting_at
            """,
            {"tenant_id": tenant_id, "remind_from": MEETING_REMIND_FROM, "remind_to": MEETING_REMIND_TO},
        )


def plan_meeting_reminders(rows: list[dict[str, Any]], binding_id: str) -> list[dict[str, Any]]:
    """Cards with a meeting in the window → outbox events (no I/O)."""
    planned = []
    for row in rows:
        viewer = viewer_from_row(row["telegram_user_id"], row.get("ref_code"), row.get("public_profile"), row.get("paid_until"))
        if lock_reason(viewer) is not None:
            continue
        message = meeting_reminder_message(
            name=row.get("name"),
            phone=row.get("phone_e164") or row.get("phone_raw"),
            local_time=str(row.get("meeting_local") or ""),
            card_url=crm_contact_url(viewer, row["contact_id"]),
        )
        planned.append(
            {
                "contact_id": row["contact_id"],
                "meeting_at": row["meeting_at"],
                "idempotency_key": meeting_idempotency_key(row["contact_id"], row["meeting_at"]),
                "payload": {
                    "chat_id": str(row["chat_id"]),
                    "text": message["text"],
                    "reply_markup": message["reply_markup"],
                    "binding_id": binding_id,
                    "account_id": row["account_id"],
                    "contact_id": row["contact_id"],
                    "site_login_user_id": int(row["telegram_user_id"]),
                },
            }
        )
    return planned


async def _queue_one(conn: Any, tenant_id: str, item: dict[str, Any], now: datetime) -> bool:
    """Claim the card (only if not reminded yet and the meeting did not move) and
    queue its reminder, in one savepoint: either both happen or neither."""
    async with conn.transaction():
        claimed = await fetch_one(
            conn,
            """
            update crm_contacts set meeting_reminded_at = now()
            where tenant_id = %s and contact_id = %s::uuid
              and meeting_reminded_at is null and deleted_at is null
              and meeting_at = %s
            returning contact_id
            """,
            (tenant_id, item["contact_id"], item["meeting_at"]),
        )
        if claimed is None:
            return False
        row = await enqueue_outbox_event(
            conn,
            tenant_id=tenant_id,
            event_type=EVENT_TYPE,
            idempotency_key=item["idempotency_key"],
            payload=item["payload"],
            due_at=now,
        )
        return bool(row and row.get("created"))


async def enqueue_meeting_reminders(tenant_bindings: dict[str, str]) -> int:
    """``tenant_bindings``: tenant_id → the bot binding that will send. Returns
    how many reminders were queued. A card that fails is skipped (its savepoint
    is rolled back) and the others of the pass are still queued."""
    queued = 0
    for tenant_id, binding_id in tenant_bindings.items():
        try:
            planned = plan_meeting_reminders(await _meetings_due(tenant_id), binding_id)
            if not planned:
                continue
            now = datetime.now(timezone.utc)
            async with tenant_connection(tenant_id) as conn:
                for item in planned:
                    try:
                        queued += 1 if await _queue_one(conn, tenant_id, item, now) else 0
                    except Exception as exc:
                        logger.warning("crm_meeting_queue_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})
        except Exception as exc:
            logger.warning("crm_meeting_plan_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})
    return queued
