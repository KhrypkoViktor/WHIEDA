"""Напоминание партнёрам, которые не довели оформление сайта до конца (владелец, 23–24.10.2026).

Берёт заявки в статусах awaiting_* (анкета не закончена или ждём оплату), которые стоят без
движения дольше суток и не дольше семи дней. Заявки в pending_* (чек уже прислан),
provisioned, cancelled, rejected не трогаем. Письмо идёт через обращение по сайту, поэтому
ответ партнёра попадает в тему владельца, а не к советнику.

Три защиты от дублей, все через ``service_notice_log`` (SQL в ``CANDIDATES_SQL``):
1. ключ ``site_nudge:<request_id>`` — одной заявке одно письмо;
2. человеку, которому напоминание уходило по любой его заявке за 30 дней, второе не шлём;
3. тому, кому мы сами писали в обращении за последние 24 часа, не шлём.

Раз в сутки в 10:00 по Москве письма рассылает фоновый worker (``site_request_nudge_step``,
app/jobs/worker.py): тот же приём, что у встреч клуба (app/telegram/club_meetings.py) —
окно после 10:00 для перезапуска worker и ключ дня в ``service_notice_log``. Staging делит
базу и бота с боем — письма шлёт только боевой worker. Ручной запуск и список без отправки —
``run_nudge`` (n8n/current/site_request_nudge_incontainer.py).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any
from zoneinfo import ZoneInfo

from app.db import fetch_all, fetch_one, tenant_connection
from app.settings import get_settings
from app.support.service import open_or_reuse_ticket, record_relayed_message
from app.telegram import support
from app.telegram.bindings import BotBindingContext, binding_context_scope, resolve_bot_binding_context
from app.telegram.delivery import send_telegram_text

logger = logging.getLogger(__name__)

TENANT_ID = "whieda"
BINDING_ID = "whieda-advisor-bot"
MSK = ZoneInfo("Europe/Moscow")
# Раз в сутки в 10:00 по Москве. Worker стоял или перезапускался — письма ещё уместны
# до вечера (не позже 20:00), позже — ждут следующего дня.
NUDGE_AT = dt.time(10, 0)
NUDGE_WINDOW = dt.timedelta(hours=10)
DAILY_KEY = "site_request_nudge:daily"
NOTICE_PREFIX = "site_nudge:"
MIN_HOURS = 24
MAX_DAYS = 7

# Заявки-кандидаты. Условия — один в один из ручного скрипта (23–24.10.2026).
CANDIDATES_SQL = """
    select r.request_id::text as rid, r.status, r.partner_name, r.updated_at::text as upd,
           la.telegram_chat_id, la.telegram_user_id, la.display_name, la.telegram_username
      from partner_site_requests r
      join lead_actors la on la.tenant_id = r.tenant_id and la.actor_id = r.actor_id
     where r.tenant_id = 'whieda' and r.status like %s
       and r.updated_at < now() - make_interval(hours => %s)
       and r.updated_at > now() - make_interval(days => %s)
       and not exists (select 1 from service_notice_log n where n.tenant_id = r.tenant_id
                        and n.notice_key = 'site_nudge:' || r.request_id::text)
       -- человеку, которому напоминание уже уходило по любой его заявке за 30 дней, второе не шлём
       and not exists (select 1 from partner_site_requests r2
                         join service_notice_log n2 on n2.tenant_id = r2.tenant_id
                          and n2.notice_key = 'site_nudge:' || r2.request_id::text
                        where r2.tenant_id = r.tenant_id and r2.actor_id = r.actor_id
                          and n2.created_at > now() - interval '30 days')
       -- и тому, кому мы сами писали в обращении за последние сутки (владелец, 24.10.2026)
       and not exists (select 1 from support_messages m join support_tickets t on t.ticket_id = m.ticket_id
                        where t.tenant_id = r.tenant_id and t.user_telegram_user_id = la.telegram_user_id
                          and m.direction = 'admin_to_user' and m.created_at > now() - interval '24 hours')
     order by r.updated_at"""


def template(first: str) -> str:
    nl = chr(10)
    head = f"{first}, здравствуйте!" if first else "Здравствуйте!"
    return nl.join([
        head + " Вы начали оформлять личный сайт WHIEDA, а до конца мы вас пока не увидели. Очень ждём вас в команде!",
        "",
        "Что-то остановило: вопрос, непонятный шаг, раздумья? Напишите сюда, подскажем и поможем дойти до результата.",
        "",
        "Если удобно, расскажу о системе полного запуска онлайн-бизнеса в МЛМ, три части работают вместе:",
        "• Клуб — ядро изменений мышления.",
        "• Курс — навыки соцсетей и ИИ.",
        "• Сайт — премиум-визитка и сервисы, которые ускоряют вашу работу.",
        "",
        "Напишите, что вам откликается, разберём вашу ситуацию.",
    ])


def first_name(row: dict[str, Any]) -> str:
    raw_name = str(row.get("partner_name") or "").strip()
    if not raw_name and not str(row.get("display_name") or "").startswith("@"):
        raw_name = str(row.get("display_name") or "").strip()
    return raw_name.split()[0] if raw_name else ""


async def find_candidates(*, min_hours: int = MIN_HOURS, max_days: int = MAX_DAYS) -> list[dict[str, Any]]:
    async with tenant_connection(TENANT_ID) as conn:
        return await fetch_all(conn, CANDIDATES_SQL, ("awaiting_%", min_hours, max_days))


async def _deliver(binding: BotBindingContext, row: dict[str, Any], first: str, item: dict[str, Any]) -> None:
    """Письмо одному партнёру через обращение по сайту; успех — в service_notice_log."""
    uid = int(row["telegram_user_id"] or row["telegram_chat_id"])
    text = template(first)
    with binding_context_scope(binding):
        admin = support._admin_for_kind(support.FORUM_KIND_SITE)
        display = (first or "Партнёр") + (f" (@{row['telegram_username']})" if row.get("telegram_username") else "")
        ticket = await open_or_reuse_ticket(
            TENANT_ID, channel_code=support.CHANNEL_SITE, offer_code=None, offer_title=None,
            user_telegram_user_id=uid, user_chat_id=uid, user_display=display, admin_telegram_user_id=admin,
        )
        if ticket["created"]:
            site = await support._site_card(binding.tenant, ticket)
            ticket = await support._open_forum_topic(binding.tenant, ticket, site=site)
            header = chr(10).join(support._site_header_lines(ticket, site))
            delivered = await support._send_site_header(ticket, header + chr(10) * 2 + "Открыто командой WWC: напоминание по незавершённой заявке. Пишите в эту тему — ответ уйдёт партнёру.")
            await record_relayed_message(TENANT_ID, ticket_id=str(ticket["ticket_id"]), direction="system", text=header,
                                         delivered_chat_id=int(ticket["forum_chat_id"]) if support._in_forum(ticket) else admin,
                                         delivered_message_id=delivered.get("message_id"))
        label = support.ticket_label(ticket)
        sent = await send_telegram_text(chat_id=str(uid), bot_token=binding.bot_token,
                                        text=f"Ответ команды WWC по обращению {label}:" + chr(10) + text)
        item["ticket"] = label
        item["sent"] = bool(sent.get("ok"))
        if sent.get("ok"):
            await record_relayed_message(TENANT_ID, ticket_id=str(ticket["ticket_id"]), direction="admin_to_user", text=text,
                                         delivered_chat_id=uid, delivered_message_id=sent.get("message_id"))
            if ticket.get("forum_chat_id") and ticket.get("forum_thread_id"):
                await send_telegram_text(chat_id=str(ticket["forum_chat_id"]), message_thread_id=int(ticket["forum_thread_id"]),
                                         bot_token=binding.bot_token, text="→ отправлено партнёру (напоминание по заявке):" + chr(10) + text)
            async with tenant_connection(TENANT_ID) as conn:
                async with conn.cursor() as cur:
                    await cur.execute("insert into service_notice_log (tenant_id, notice_key, sent_on) values ('whieda', %s, current_date) on conflict do nothing",
                                      (NOTICE_PREFIX + row["rid"],))


async def run_nudge(
    *,
    send: bool,
    binding: BotBindingContext | None = None,
    min_hours: int = MIN_HOURS,
    max_days: int = MAX_DAYS,
    only: str = "",
) -> list[dict[str, Any]]:
    """Список кандидатов; при ``send`` — ещё и письма. Сбой одного письма не останавливает остальные:
    у него в отчёте ``sent`` = False и ``error``, а без записи в service_notice_log заявка
    вернётся в список на следующий день (пока ей не больше max_days)."""
    if send and binding is None:
        binding = await resolve_bot_binding_context(BINDING_ID)
        if binding is None:
            raise RuntimeError("bot_binding_unavailable")
    report: list[dict[str, Any]] = []
    for row in await find_candidates(min_hours=min_hours, max_days=max_days):
        if only and row["rid"] != only:
            continue
        first = first_name(row)
        item: dict[str, Any] = {
            "rid": row["rid"], "status": row["status"], "stalled_since": row["upd"][:16],
            "to": first or row.get("display_name"),
        }
        if send:
            try:
                await _deliver(binding, row, first, item)
            except Exception as exc:
                item["sent"] = bool(item.get("sent"))
                item["error"] = type(exc).__name__
                logger.exception("site_request_nudge_item_failed", extra={"request_id": row["rid"]})
        report.append(item)
    return report


def nudge_enabled() -> bool:
    """Staging делит базу и бота с боем — письма шлёт только боевой worker (как встречи клуба)."""
    settings = get_settings()
    return settings.environment == "production" and settings.platform_site_request_nudge_enabled


def due_day(now: dt.datetime) -> dt.date | None:
    """Московская дата, если сейчас время рассылки: от 10:00 и ещё NUDGE_WINDOW; иначе None."""
    local = now.astimezone(MSK)
    start = dt.datetime.combine(local.date(), NUDGE_AT, tzinfo=MSK)
    return local.date() if start <= local < start + NUDGE_WINDOW else None


async def _claim_day(day: dt.date) -> bool:
    async with tenant_connection(TENANT_ID) as conn:
        row = await fetch_one(
            conn,
            """
            insert into service_notice_log (tenant_id, notice_key, sent_on) values (%s, %s, %s)
            on conflict (tenant_id, notice_key, sent_on) do nothing
            returning notice_key
            """,
            (TENANT_ID, DAILY_KEY, day),
        )
    return row is not None


async def site_request_nudge_step(now: dt.datetime | None = None) -> int:
    """Шаг worker: раз в сутки после 10:00 по Москве напомнить незаконченным заявкам.
    Возвращает число отправленных писем."""
    if not nudge_enabled():
        return 0
    day = due_day(now or dt.datetime.now(dt.timezone.utc))
    if day is None:
        return 0
    binding = await resolve_bot_binding_context(BINDING_ID)
    if binding is None:
        return 0
    # Ключ дня берём до писем: два worker подряд (выкладка) не разошлют дважды.
    if not await _claim_day(day):
        return 0
    report = await run_nudge(send=True, binding=binding)
    sent = sum(1 for item in report if item.get("sent"))
    logger.info("site_request_nudge", extra={"candidates": len(report), "sent": sent})
    return sent
