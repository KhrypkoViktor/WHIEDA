"""Process due onboarding reminders and weekly leader digests (staging/local)."""

from __future__ import annotations

import logging
from typing import Any

from app.db import fetch_all, get_pool, tenant_connection
from app.jobs.outbox import enqueue_outbox_event
from app.onboarding.reminders import fetch_due_reminders, mark_reminder_sent
from app.reports.delivery import format_leader_digest_message
from app.reports.service import build_leader_digest
from app.settings import get_settings

logger = logging.getLogger(__name__)


async def process_onboarding_reminders(tenant_id: str) -> int:
    due = await fetch_due_reminders(tenant_id)
    sent = 0
    for row in due:
        telegram_user_id = row.get("telegram_user_id")
        if not telegram_user_id:
            continue
        async with tenant_connection(tenant_id) as conn:
            await enqueue_outbox_event(
                conn,
                tenant_id=tenant_id,
                event_type="onboarding_reminder",
                idempotency_key=f"onboarding_reminder:{row['reminder_id']}",
                payload={
                    "reminder_id": str(row["reminder_id"]),
                    "enrollment_id": str(row["enrollment_id"]),
                    "telegram_user_id": int(telegram_user_id),
                    "day_number": int(row["day_number"]),
                },
            )
            await mark_reminder_sent(tenant_id, str(row["reminder_id"]))
        sent += 1
    return sent


async def enqueue_weekly_leader_digest(tenant_id: str, *, owner_id: str | None = None) -> dict[str, Any]:
    digest = await build_leader_digest(tenant_id, owner_id=owner_id, days=7)
    message = format_leader_digest_message(digest)
    idempotency_key = f"leader_digest:{tenant_id}:{digest.get('period_end', '')[:10]}"
    if owner_id:
        idempotency_key += f":{owner_id}"

    async with tenant_connection(tenant_id) as conn:
        row = await enqueue_outbox_event(
            conn,
            tenant_id=tenant_id,
            event_type="leader_digest_weekly",
            idempotency_key=idempotency_key,
            payload={"message_preview": message[:500], "owner_id": owner_id},
        )
    return {"ok": True, "enqueued": bool(row), "idempotency_key": idempotency_key}


async def process_all_tenant_jobs() -> dict[str, int]:
    settings = get_settings()
    pool = get_pool()
    async with pool.connection(timeout=settings.database_timeout_sec) as conn:
        tenants = await fetch_all(
            conn,
            "select tenant_id from tenants where status = 'active'",
        )

    reminders = 0
    digests = 0
    for row in tenants:
        tenant_id = row["tenant_id"]
        reminders += await process_onboarding_reminders(tenant_id)
        result = await enqueue_weekly_leader_digest(tenant_id)
        if result.get("enqueued"):
            digests += 1
    return {"reminders": reminders, "digests": digests}
