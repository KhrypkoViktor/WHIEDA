"""Schedule and record onboarding reminders (deduplicated)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from app.db import fetch_one, tenant_connection

REMINDER_HOUR_UTC = 9


async def schedule_next_reminder(tenant_id: str, enrollment_id: str, day_number: int) -> dict | None:
    scheduled = _next_reminder_time()
    idempotency_key = f"reminder:{enrollment_id}:day:{day_number}"

    async with tenant_connection(tenant_id) as conn:
        paused = await fetch_one(
            conn,
            """
            select reminders_paused, status
            from onboarding_enrollments
            where tenant_id = %s and enrollment_id = %s::uuid
            limit 1
            """,
            (tenant_id, enrollment_id),
        )
        if not paused or paused.get("reminders_paused") or paused.get("status") != "active":
            return None

        existing = await fetch_one(
            conn,
            """
            select reminder_id from onboarding_reminders
            where tenant_id = %s and idempotency_key = %s
            limit 1
            """,
            (tenant_id, idempotency_key),
        )
        if existing:
            return {"ok": True, "created": False, "reminder_id": str(existing["reminder_id"])}

        reminder_id = str(uuid.uuid4())
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into onboarding_reminders (
                  reminder_id, tenant_id, enrollment_id, day_number,
                  scheduled_for, idempotency_key
                ) values (%s::uuid, %s, %s::uuid, %s, %s, %s)
                """,
                (reminder_id, tenant_id, enrollment_id, day_number, scheduled, idempotency_key),
            )
    return {"ok": True, "created": True, "reminder_id": reminder_id, "scheduled_for": scheduled.isoformat()}


async def fetch_due_reminders(tenant_id: str, limit: int = 50) -> list[dict]:
    now = datetime.now(timezone.utc)
    async with tenant_connection(tenant_id) as conn:
        from app.db import fetch_all

        return await fetch_all(
            conn,
            """
            select r.reminder_id, r.enrollment_id, r.day_number, e.telegram_user_id
            from onboarding_reminders r
            join onboarding_enrollments e
              on e.tenant_id = r.tenant_id and e.enrollment_id = r.enrollment_id
            where r.tenant_id = %s
              and r.sent_at is null
              and r.scheduled_for <= %s
              and e.status = 'active'
              and e.reminders_paused = false
            order by r.scheduled_for
            limit %s
            """,
            (tenant_id, now, limit),
        )


async def mark_reminder_sent(tenant_id: str, reminder_id: str) -> None:
    async with tenant_connection(tenant_id) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update onboarding_reminders
                set sent_at = now()
                where tenant_id = %s and reminder_id = %s::uuid
                """,
                (tenant_id, reminder_id),
            )


def _next_reminder_time() -> datetime:
    now = datetime.now(timezone.utc)
    candidate = now.replace(hour=REMINDER_HOUR_UTC, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate
