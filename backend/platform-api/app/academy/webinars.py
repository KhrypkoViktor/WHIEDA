"""Напоминания о вебинарах витрины (Виктор через «Настройку рекламы», 18.10.2026).

Где: ``academy_showcases.support.webinars`` — метка ссылки с рекламы → вебинар::

    {"ig_web24": {"at": "2026-10-24T17:30:00+03:00", "date": "24 октября", "url": ""}, …}

``url`` — ссылка на эфир; Виктор присылает её позже, пока пусто — напоминания со ссылкой не идут.

Кому: людям, открывшим бота по ссылке с рекламы (``academy_ad_opens``, V37). Вебинар человека —
по его строке таблицы заявок (интерес «…24 октября»), а без строки — по метке /start.

* за сутки, за 2 часа — без ссылки; за час и за 5 минут — только со ссылкой;
* открыл бота после времени напоминания — получает только оставшиеся;
* каждое — одному человеку один раз (ключ outbox), у каждого своё окно отправки (``REMINDERS``);
* администратору витрины: за 1,5 часа нет ссылки — одна строка; за 30 минут — список заявок
  на этот вебинар, которые бота не открыли (телефон, WhatsApp, Telegram по номеру).

Отправляет обычный цикл уведомлений (``process_due_notifications``): заблокировавший бота —
«dead» без повторов, остальные не страдают.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.academy.ad_leads import (
    MINSK,
    Lead,
    Opened,
    _contacts,
    _plain,
    fetch_leads,
    load_opens,
    opened_for,
    sheet_config,
)

logger = logging.getLogger(__name__)

EVENT = "showcase_webinar_reminder"
NO_LINK_WARNING = timedelta(minutes=90)  # 16:00 при начале в 17:30
CALL_LIST = timedelta(minutes=30)  # 17:00
MESSAGE_LIMIT = 3800  # запас до 4096 после разметки


@dataclass(frozen=True)
class Webinar:
    tag: str
    at: datetime
    date: str
    url: str


# Напоминание: ключ, за сколько до начала, нужна ли ссылка, сколько после своего времени ещё слать
# (опоздавший воркер или поздняя ссылка: «Через час начинаем» за 40 минут до эфира не шлём).
REMINDERS: tuple[tuple[str, timedelta, bool, timedelta], ...] = (
    ("day", timedelta(hours=24), False, timedelta(minutes=45)),
    ("two_hours", timedelta(hours=2), False, timedelta(minutes=30)),
    ("hour", timedelta(hours=1), True, timedelta(minutes=20)),
    ("five", timedelta(minutes=5), True, timedelta(minutes=10)),
)


def webinars_config(support: dict[str, Any] | None) -> list[Webinar]:
    raw = (support or {}).get("webinars")
    if not isinstance(raw, dict):
        return []
    out = []
    for tag, item in raw.items():
        if not isinstance(item, dict):
            continue
        try:
            at = datetime.fromisoformat(str(item.get("at") or ""))
        except ValueError:
            continue
        if at.tzinfo is None:
            continue
        url = str(item.get("url") or "").strip()
        out.append(Webinar(str(tag), at, str(item.get("date") or "").strip(), url if url.startswith("https://") else ""))
    return out


def reminder_text(kind: str, webinar: Webinar) -> str:
    time = webinar.at.astimezone(MINSK).strftime("%H:%M")
    if kind == "day":
        return (
            f"Завтра, {webinar.date}, в {time} по Минску — бесплатный вебинар Александры Пармон по композиции.\n\n"
            "Ссылку на эфир пришлём сюда, в этот чат."
        )
    if kind == "two_hours":
        return f"Сегодня в {time} по Минску — вебинар по композиции с Александрой Пармон. Ссылку пришлём за час до начала."
    if kind == "hour":
        return f"Через час начинаем. Ссылка на эфир: {webinar.url}\n\nНачало в {time} по Минску."
    return f"Начинаем через 5 минут. Заходите: {webinar.url}"


def _link_markup(webinar: Webinar) -> dict[str, Any]:
    return {"inline_keyboard": [[{"text": "Перейти к эфиру", "url": webinar.url}]]}


def due_reminders(webinar: Webinar, now: datetime) -> list[tuple[str, datetime]]:
    """Напоминания, время которых пришло и окно не закрылось: (ключ, время). Со ссылкой — только если она есть."""
    due = []
    for kind, before, needs_url, window in REMINDERS:
        moment = webinar.at - before
        if moment <= now < moment + window and (webinar.url or not needs_url):
            due.append((kind, moment))
    return due


def _in(moment: datetime, now: datetime, until: datetime) -> bool:
    return moment <= now < until


def lead_webinar(lead: Lead | None, webinars: list[Webinar]) -> str | None:
    """Метка вебинара по строке заявки: «Бесплатный вебинар 24 октября» → ig_web24."""
    if lead is None:
        return None
    text = f"{lead.interest} {lead.form_name}".casefold()
    for webinar in webinars:
        short = webinar.at.astimezone(MINSK).strftime("%d.%m")
        if (webinar.date and webinar.date.casefold() in text) or short in text:
            return webinar.tag
    return None


def person_webinar(opened: Opened, leads_by_id: dict[str, Lead], webinars: list[Webinar]) -> str:
    """Вебинар человека, открывшего бота: по его строке таблицы, иначе по метке /start."""
    return lead_webinar(leads_by_id.get(opened.lead_id or ""), webinars) or opened.tag


def call_list_texts(webinar: Webinar, leads: list[Lead]) -> list[str]:
    """«Не в боте — позвонить или написать до 17:30 (N человек)» и по человеку: имя, телефон, ссылки."""
    time = webinar.at.astimezone(MINSK).strftime("%H:%M")
    head = f"📞 Не в боте — позвонить или написать до {time} ({len(leads)} чел.) · вебинар {webinar.date}"
    texts, current = [], head
    for number, lead in enumerate(leads, start=1):
        lines = [f"{number}. <b>{_plain(lead.name) or 'Без имени'}</b>"]
        if lead.phone:
            lines.append(f"📞 {lead.phone}")
            if contacts := _contacts(lead.phone):
                lines.append(contacts)
        if lead.username:
            lines.append(f"✈️ @{lead.username}")
        block = "\n".join(lines)
        if len(current) + len(block) + 2 > MESSAGE_LIMIT:
            texts.append(current)
            current = f"{head} — продолжение"
        current += "\n\n" + block
    texts.append(current)
    return texts


async def webinar_step(tenant_ids: set[str], bindings: dict[str, Any], *, clock: datetime | None = None) -> int:
    """Напоминания и списки для администратора, время которых пришло. Возвращает число новых сообщений."""
    from app.academy.showcases import load_showcases
    from app.db import tenant_connection
    from app.jobs.outbox import enqueue_outbox_event
    from app.telegram.showcase_support import KIND_ADMIN, support_targets

    now = clock or datetime.now(timezone.utc)
    queued = 0
    for tenant_id in sorted(tenant_ids):
        for showcase in await load_showcases(tenant_id):
            webinars = webinars_config(showcase.support)
            binding_id = showcase.telegram_binding_id
            if not webinars or not binding_id or binding_id not in bindings:
                continue
            # Что пора делать — считается без базы; база и таблица — только если что-то пришло.
            work = [
                (webinar, due_reminders(webinar, now),
                 not webinar.url and _in(webinar.at - NO_LINK_WARNING, now, webinar.at - timedelta(hours=1)),
                 _in(webinar.at - CALL_LIST, now, webinar.at))
                for webinar in webinars
            ]
            work = [item for item in work if item[1] or item[2] or item[3]]
            if not work:
                continue
            config = sheet_config(showcase.support)
            leads: list[Lead] = []
            if config:
                try:
                    leads = await fetch_leads(config)
                except Exception as exc:
                    logger.warning("webinar_sheet_failed", extra={"showcase": showcase.showcase_id, "error": type(exc).__name__})
            leads_by_id = {lead.lead_id: lead for lead in leads}
            admin = (await support_targets(tenant_id, showcase)).get(KIND_ADMIN)

            async def enqueue(conn: Any, key: str, chat_id: int, text: str, markup: dict[str, Any] | None = None) -> int:
                payload: dict[str, Any] = {"chat_id": str(int(chat_id)), "text": text, "binding_id": binding_id}
                if markup:
                    payload["reply_markup"] = markup
                row = await enqueue_outbox_event(
                    conn, tenant_id=tenant_id, event_type=EVENT, idempotency_key=key, payload=payload, due_at=now,
                )
                return 1 if row and row.get("created") else 0

            async with tenant_connection(tenant_id) as conn:
                opens = await load_opens(conn, tenant_id, showcase.showcase_id)
                for webinar, reminders, warn, calls in work:
                    prefix = f"webinar:{showcase.showcase_id}:{webinar.tag}"
                    audience = [o for o in opens if person_webinar(o, leads_by_id, webinars) == webinar.tag]
                    for kind, moment in reminders:
                        sent = 0
                        for opened in audience:
                            if opened.opened_at is not None and opened.opened_at > moment:
                                continue  # открыл бота позже — это напоминание ему уже не нужно
                            markup = _link_markup(webinar) if kind in ("hour", "five") else None
                            sent += await enqueue(
                                conn, f"{prefix}:{kind}:{opened.telegram_user_id}", opened.telegram_user_id,
                                reminder_text(kind, webinar), markup,
                            )
                        queued += sent
                        if sent:
                            logger.info("webinar_reminders_queued", extra={"webinar": webinar.tag, "kind": kind, "queued": sent})
                    if admin is None:
                        continue
                    if warn:
                        queued += await enqueue(
                            conn, f"{prefix}:no_link", admin,
                            f"⚠️ Вебинар {webinar.date}: нет ссылки на эфир — напоминания со ссылкой не уйдут.",
                        )
                    if calls:
                        missing = [
                            lead for lead in leads
                            if lead_webinar(lead, webinars) == webinar.tag and opened_for(lead, opens) is None
                        ]
                        if missing:
                            for index, text in enumerate(call_list_texts(webinar, missing)):
                                queued += await enqueue(conn, f"{prefix}:calls:{index}", admin, text)
    return queued
