"""Встречи клуба: расписание для закрепа и напоминание за 2 часа (владелец, 11.10.2026).

Две постоянные встречи в Zoom — участник сам выбирает, куда ходить. Бот публикует
расписание в чатах клуба (Telegram «WWC Leader CLUB» и Max «WWC Club»), владелец
закрепляет. За 2 часа до встречи — пост «Сегодня встреча в …» в те же чаты: прийти,
включить камеру, выделить 1,5 часа. Время меняется по договорённости — тогда правится
MEETINGS ниже и выкладывается Core.

Напоминание шлёт фоновый worker (app/jobs/worker.py) раз в минуту; один раз в день на
встречу — ключ в service_notice_log. Свои сообщения бот обновлением не получает,
поэтому в Max — отсюда же, со связкой для ответов (chat_bridge_links).
"""

from __future__ import annotations

import datetime as dt
import html
import logging
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from app.db import fetch_one, tenant_connection

logger = logging.getLogger(__name__)

TENANT_ID = "whieda"
MSK = ZoneInfo("Europe/Moscow")
REMIND_BEFORE = dt.timedelta(hours=2)
# Worker перезапускался или стоял — напоминание ещё уместно в этом окне, позже — нет.
REMIND_WINDOW = dt.timedelta(minutes=45)


@dataclass(frozen=True)
class ClubMeeting:
    key: str
    group: str
    weekday: int  # 0 — понедельник … 6 — воскресенье
    day_name: str
    at: dt.time  # по Москве
    zoom_url: str


MEETINGS: tuple[ClubMeeting, ...] = (
    ClubMeeting(
        key="tue", group="Первая группа", weekday=1, day_name="вторник", at=dt.time(10, 30),
        zoom_url="https://us06web.zoom.us/j/81097590923?pwd=FqIjwEw7vICaHJokSbzjGp3Jzs7Byz.1",
    ),
    ClubMeeting(
        key="sun", group="Вторая группа", weekday=6, day_name="воскресенье", at=dt.time(14, 0),
        zoom_url="https://us06web.zoom.us/j/85887153238?pwd=JeoQYrcqRAhZky2yr8cLUG5U5TwLab.1",
    ),
)


def _hhmm(value: dt.time) -> str:
    return value.strftime("%H:%M")


def _zoom(meeting: ClubMeeting) -> str:
    return f'<a href="{html.escape(meeting.zoom_url, quote=True)}">Войти в Zoom</a>'


def schedule_text() -> str:
    """Пост для закрепа: обе встречи, выбор за участником."""
    lines = ["📅 <b>Встречи клуба WWC</b>", "", "Две группы — выбирайте сами, куда ходить: в одну или в обе.", ""]
    for meeting in MEETINGS:
        lines += [f"<b>{meeting.group} — {meeting.day_name}, {_hhmm(meeting.at)} по Москве</b>", _zoom(meeting), ""]
    lines += [
        "Ссылки постоянные. Время может меняться по договорённости — об изменениях напишем здесь.",
        "За 2 часа до встречи бот напомнит в этом чате.",
    ]
    return "\n".join(lines)


def reminder_text(meeting: ClubMeeting) -> str:
    return "\n".join([
        f"⏰ <b>Сегодня встреча клуба в {_hhmm(meeting.at)} по Москве</b>",
        f"{meeting.group}, {meeting.day_name}.",
        "",
        "Приходите 🙂",
        "Включайте камеру и выделите встрече 1,5 часа — без отвлечений, так она даёт больше всего.",
        "",
        _zoom(meeting),
    ])


def due_meetings(now: dt.datetime) -> list[tuple[ClubMeeting, dt.date]]:
    """Встречи, о которых пора напомнить сейчас: от «за 2 часа» и ещё REMIND_WINDOW."""
    local = now.astimezone(MSK)
    due = []
    for meeting in MEETINGS:
        if local.weekday() != meeting.weekday:
            continue
        remind_at = dt.datetime.combine(local.date(), meeting.at, tzinfo=MSK) - REMIND_BEFORE
        if remind_at <= local < remind_at + REMIND_WINDOW:
            due.append((meeting, local.date()))
    return due


async def _claim(meeting: ClubMeeting, day: dt.date) -> bool:
    async with tenant_connection(TENANT_ID) as conn:
        row = await fetch_one(
            conn,
            """
            insert into service_notice_log (tenant_id, notice_key, sent_on) values (%s, %s, %s)
            on conflict (tenant_id, notice_key, sent_on) do nothing
            returning notice_key
            """,
            (TENANT_ID, f"club_meeting:{meeting.key}", day),
        )
    return row is not None


async def post_to_club(text_html: str, *, bot_token: str) -> dict[str, Any]:
    """Одно сообщение в оба чата клуба: Telegram и Max (если Max настроен)."""
    from app.max.bridge import _link
    from app.max.bridges import BRIDGES
    from app.max.client import send_max_message
    from app.settings import get_settings
    from app.telegram.delivery import _call_telegram

    club = next(b for b in BRIDGES if b.access == "club")
    sent = await _call_telegram(
        "sendMessage",
        {"chat_id": club.tg_chat_id, "text": text_html, "parse_mode": "HTML", "disable_web_page_preview": True},
        bot_token=bot_token,
    )
    tg_message_id = (sent.get("result") or {}).get("message_id") if sent.get("ok") else None
    max_mid = None
    if get_settings().max_bot_token:
        delivered = await send_max_message(chat_id=club.max_chat_id, text=text_html, html_format=True)
        max_mid = delivered.get("mid") if delivered.get("ok") else None
    if tg_message_id and max_mid:
        await _link(TENANT_ID, club, [int(tg_message_id)], str(max_mid), "tg_to_max")
    return {"telegram_message_id": tg_message_id, "max_mid": max_mid}


async def club_meetings_step(now: dt.datetime | None = None) -> int:
    """Шаг worker: напомнить о сегодняшней встрече за 2 часа. Возвращает число отправленных."""
    from app.settings import get_settings
    from app.telegram.bindings import resolve_bot_binding_context

    # Staging делит базу и бота с боем — напоминает только боевой worker.
    if get_settings().environment != "production":
        return 0
    due = due_meetings(now or dt.datetime.now(dt.timezone.utc))
    if not due:
        return 0
    binding = await resolve_bot_binding_context("whieda-advisor-bot")
    if binding is None:
        return 0
    sent = 0
    for meeting, day in due:
        if not await _claim(meeting, day):
            continue
        result = await post_to_club(reminder_text(meeting), bot_token=binding.bot_token)
        logger.info("club_meeting_reminder", extra={"meeting": meeting.key, **result})
        sent += 1
    return sent
