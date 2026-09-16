"""Daily service notices, run by the partner-reminders timer (09:00 MSK):

* a week before a licence ends — the client gets a nudge and the ticket's
  topic gets a line (a reason to sell the renewal), once per sale;
* the deposit with the administrator is below one licence — the «Отчёты»
  topic (or the owner and the administrator directly) hears it once a day.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.db import fetch_all, fetch_one, tenant_connection
from app.service_sales.service import month_report
from app.support.service import get_forum, ticket_label
from app.telegram.delivery import send_telegram_text
from app.telegram.money import money

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


async def notice_already_sent(tenant_id: str, *, key: str, on: date) -> bool:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            "select 1 as x from service_notice_log where tenant_id = %s and notice_key = %s and sent_on = %s",
            (tenant_id, key, on),
        )
    return bool(row)


async def mark_notice_sent(tenant_id: str, *, key: str, on: date) -> None:
    async with tenant_connection(tenant_id) as conn:
        await fetch_one(
            conn,
            "insert into service_notice_log (tenant_id, notice_key, sent_on) values (%s, %s, %s) on conflict do nothing returning notice_key",
            (tenant_id, key, on),
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
    admin_telegram_user_id: int | None,
    owner_telegram_user_id: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    out: dict[str, Any] = {"licence_reminders": 0, "low_deposit_notice": False}

    for row in await list_due_licence_reminders(tenant_id, now=moment):
        await send_telegram_text(chat_id=str(row["user_chat_id"]), text=licence_client_text(row), bot_token=bot_token)
        if row.get("forum_chat_id") and row.get("forum_thread_id"):
            await send_telegram_text(
                chat_id=str(row["forum_chat_id"]), bot_token=bot_token, message_thread_id=int(row["forum_thread_id"]),
                text=f"Заявка {ticket_label(row)}: лицензия заканчивается {row['activated_until'].strftime('%d.%m.%Y')} — клиенту отправлено напоминание о продлении.",
            )
        await mark_licence_reminded(tenant_id, sale_id=str(row["sale_id"]))
        out["licence_reminders"] += 1

    if admin_telegram_user_id is not None:
        report = await month_report(tenant_id, admin_telegram_user_id=admin_telegram_user_id)
        today = moment.date()
        if report["low_balance"] and not await notice_already_sent(tenant_id, key="low_deposit", on=today):
            text = f"Депозит у администратора: {money(report['deposit_balance_minor'], 'RUB')} — меньше одной лицензии. Виктор, пополните («перевёл 20000»)."
            forum = await get_forum(tenant_id, binding_id=binding_id)
            if forum and forum.get("reports_thread_id"):
                await send_telegram_text(chat_id=str(forum["chat_id"]), text=text, bot_token=bot_token, message_thread_id=int(forum["reports_thread_id"]))
            else:
                for chat in {admin_telegram_user_id, owner_telegram_user_id} - {None}:
                    await send_telegram_text(chat_id=str(chat), text=text, bot_token=bot_token)
            await mark_notice_sent(tenant_id, key="low_deposit", on=today)
            out["low_deposit_notice"] = True
    return out
