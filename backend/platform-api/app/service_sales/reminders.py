"""Daily service notices, run by the partner-reminders timer (09:00 MSK):

* a week before a licence ends — the client gets a nudge and the ticket's
  topic gets a line (a reason to sell the renewal), once per sale.

The «deposit below one licence» notice was removed on the owner's request
(06.10.2026): the owner does not keep a deposit with the administrator.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.db import fetch_all, fetch_one, tenant_connection
from app.support.service import ticket_label
from app.telegram.delivery import send_telegram_text

LICENCE_REMINDER_DAYS = 7


async def list_due_licence_reminders(tenant_id: str, *, now: datetime | None = None) -> list[dict[str, Any]]:
    moment = now or datetime.now(timezone.utc)
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            """
            select s.sale_id, s.offer_code, s.activated_until, t.ticket_no, t.user_chat_id, t.forum_chat_id, t.forum_thread_id
            from service_sales s
            join support_tickets t on t.tenant_id = s.tenant_id and t.ticket_id = s.ticket_id
            where s.tenant_id = %s and s.status = 'activated' and s.reminded_at is null
              and s.activated_until is not null
              and s.activated_until > %s and s.activated_until <= %s
            order by s.activated_until
            """,
            (tenant_id, moment, moment + timedelta(days=LICENCE_REMINDER_DAYS)),
        )


async def mark_licence_reminded(tenant_id: str, *, sale_id: str) -> None:
    async with tenant_connection(tenant_id) as conn:
        await fetch_one(
            conn,
            "update service_sales set reminded_at = now() where tenant_id = %s and sale_id = %s::uuid returning sale_id",
            (tenant_id, sale_id),
        )


def licence_client_text(row: dict[str, Any]) -> str:
    until = row["activated_until"].strftime("%d.%m.%Y")
    return (
        f"Лицензия Gemini Pro заканчивается {until}. Продлить можно здесь: «Сервисы» → выберите вариант — "
        "администратор оформит продление."
    )


async def send_service_notices(
    *,
    tenant_id: str,
    binding_id: str,
    bot_token: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    out: dict[str, Any] = {"licence_reminders": 0}

    for row in await list_due_licence_reminders(tenant_id, now=moment):
        await send_telegram_text(chat_id=str(row["user_chat_id"]), text=licence_client_text(row), bot_token=bot_token)
        if row.get("forum_chat_id") and row.get("forum_thread_id"):
            await send_telegram_text(
                chat_id=str(row["forum_chat_id"]), bot_token=bot_token, message_thread_id=int(row["forum_thread_id"]),
                text=f"Заявка {ticket_label(row)}: лицензия заканчивается {row['activated_until'].strftime('%d.%m.%Y')} — клиенту отправлено напоминание о продлении.",
            )
        await mark_licence_reminded(tenant_id, sale_id=str(row["sale_id"]))
        out["licence_reminders"] += 1

    return out
