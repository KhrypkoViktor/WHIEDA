"""Ритм «Онлайн-старта» в боте: карточка «Сегодня», галочки, табло недели (17.10.2026).

Решение лида: Obsidian ``Online_start/RITM_resheniye.md`` §2–3; данные и тексты —
``app.academy.rhythm``. Утром (``PLATFORM_RHYTHM_CARD_TIME`` по Минску) worker ставит каждому
в ритме карточку в ``platform_outbox`` — как утро CRM: отправляет общий
``process_due_notifications``, кнопка «📖 Урок дня» входит на сайт в момент отправки. Урок
дня — следующий непройденный урок курса. Нажатие галочки правит то же сообщение. В
воскресенье (``PLATFORM_RHYTHM_BOARD_TIME``) — табло в группу курса (``COURSE_GROUPS``; в Max —
через мост, как встречи клуба), один раз за воскресенье (``service_notice_log``).

Расписание — только при ``PLATFORM_RHYTHM_ENABLED`` и только у процесса с
``PLATFORM_SCHEDULED_NOTIFY_BINDINGS`` (бой). Staging делит базу с боем и ничего не шлёт.

Callbacks: ``rh:<p|h|f>:<ГГГГММДД>`` — «пост», «человек», «пятёрка» дня карточки (сегодня или
вчера: вечер после полуночи; старые карточки не правятся).
Владелец (preview-админ), личка: «ритм» — карточка сейчас; «табло» — табло в группу сейчас.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from app.academy import rhythm
from app.academy.service import AcademyError, AcademyViewer, course_outline, preview_admin_ids
from app.db import fetch_all, fetch_one, tenant_connection
from app.jobs.outbox import enqueue_outbox_event
from app.settings import get_settings
from app.telegram.academy import course_lesson_url
from app.telegram.bindings import BotBindingContext, current_bot_binding
from app.telegram.course_groups import COURSE_GROUPS
from app.telegram.delivery import _call_telegram, answer_callback_query, format_telegram_html, send_telegram_text
from app.telegram.site_login import with_site_login
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

EVENT_TYPE = "academy_rhythm_card"
# Worker стоял или перезапускался — карточка ещё уместна в этом окне, табло — в своём.
CARD_WINDOW = timedelta(hours=1)
BOARD_WINDOW = timedelta(minutes=45)
DONE_TEXT = "День засчитан ✅"
STALE_TEXT = "Это карточка прошлого дня — отметки ставятся в сегодняшней."
NOT_MEMBER_TEXT = "Карточка «Сегодня» — для учеников курса."
BOARD_REPLY = {
    "empty": "За эту неделю ни одного засчитанного дня — в группу ничего не отправлено.",
    "no_group": "Группа курса не настроена — табло некуда отправить.",
    "failed": "Табло не ушло в группу: Telegram отказал. Проверьте, что бот — админ группы.",
}

_CALLBACK_RE = re.compile(r"^rh:([phf]):(\d{8})$")
_CARD_TEXT_RE = re.compile(r"^/?ритм\s*$", re.I)
_BOARD_TEXT_RE = re.compile(r"^/?табло\s*$", re.I)


def course_slug() -> str:
    return str(get_settings().platform_rhythm_course or "").strip()


def card_key(course: str, telegram_user_id: int, day: date) -> str:
    return f"rhythm_card:{course}:{int(telegram_user_id)}:{day.isoformat()}"


def in_window(local: datetime, at: time, width: timedelta) -> bool:
    start = datetime.combine(local.date(), at, tzinfo=local.tzinfo)
    return start <= local < start + width


async def lesson_of_day(tenant_id: str, course: str, telegram_user_id: int) -> dict[str, Any] | None:
    """Следующий непройденный открытый урок курса; курс закрыт или пройден — None."""
    uid = int(telegram_user_id)
    viewer = AcademyViewer(telegram_user_id=uid, is_preview_admin=uid in preview_admin_ids(), partner_paid=False)
    try:
        outline = await course_outline(tenant_id, course, viewer)
    except AcademyError:
        return None
    upcoming = outline["course"].get("next_lesson")
    lesson = next((row for row in outline["lessons"] if row["slug"] == upcoming), None)
    if lesson is None or lesson.get("locked"):
        return None
    return {
        "slug": lesson["slug"],
        "title": lesson["title"],
        "minutes": lesson.get("minutes"),
        "url": await course_lesson_url(tenant_id, course, lesson["slug"]),
    }


async def build_card(
    tenant_id: str, telegram_user_id: int, day: date, *, today: date | None = None, sign: bool
) -> tuple[str, dict[str, Any]]:
    """Текст и кнопки карточки. ``sign`` — ссылка урока сразу со входом (личка сейчас);
    карточке из очереди вход ставит worker при отправке (``site_login_user_id``)."""
    today = today or rhythm.local_today()
    course = course_slug()
    state = await rhythm.card_state(tenant_id, telegram_user_id, day, today)
    lesson = await lesson_of_day(tenant_id, course, telegram_user_id)
    url = lesson["url"] if lesson else None
    if url and sign:
        url = await with_site_login(url, tenant_id=tenant_id, telegram_user_id=int(telegram_user_id))
    text = rhythm.card_text(day, state["marks"], state["streak"], broken=state["broken"], lesson=lesson)
    return text, rhythm.card_keyboard(day, state["marks"], url)


async def try_handle_rhythm_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str
) -> dict[str, Any] | None:
    match = _CALLBACK_RE.fullmatch(callback.data or "")
    if not match:
        return None
    route = "academy_rhythm"
    token = current_bot_binding().bot_token

    async def ack(text: str | None = None) -> None:
        await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=token, text=text)

    if callback.chat_type != "private":
        await ack()
        return {"ok": True, "route": route, "status": "private_chat_required", "trace_id": trace_id}
    try:
        day = datetime.strptime(match.group(2), "%Y%m%d").date()
    except ValueError:
        await ack()
        return {"ok": False, "route": route, "status": "bad_day", "trace_id": trace_id}
    today = rhythm.local_today()
    if not today - timedelta(days=1) <= day <= today:
        await ack(STALE_TEXT)
        return {"ok": False, "route": route, "status": "stale_card", "trace_id": trace_id}
    tenant_id = tenant.tenant_id
    if not await rhythm.is_member(tenant_id, course_slug(), callback.user_id):
        await ack(NOT_MEMBER_TEXT)
        return {"ok": False, "route": route, "status": "not_member", "trace_id": trace_id}
    row = await rhythm.toggle_mark(tenant_id, callback.user_id, day, rhythm.CODES[match.group(1)])
    await ack(DONE_TEXT if row.get("done_at") else None)
    text, markup = await build_card(tenant_id, callback.user_id, day, today=today, sign=True)
    message = ((callback.raw or {}).get("callback_query") or {}).get("message") or {}
    if message.get("message_id"):
        await _call_telegram(
            "editMessageText",
            {
                "chat_id": callback.chat_id,
                "message_id": int(message["message_id"]),
                "text": format_telegram_html(text)[:4096],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": markup,
            },
            bot_token=token,
        )
    status = "day_done" if row.get("done_at") else "toggled"
    return {"ok": True, "route": route, "status": status, "trace_id": trace_id}


async def post_to_group(chat_id: int, text_html: str, *, bot_token: str) -> dict[str, Any]:
    """Сообщение в группу курса; группа в мосту Telegram ↔ Max — и в Max, как встречи клуба."""
    from app.max.bridges import bridge_for_telegram
    from app.telegram.club_meetings import post_to_club

    bridge = bridge_for_telegram(chat_id)
    if bridge is not None:
        return await post_to_club(text_html, bot_token=bot_token, bridge=bridge)
    sent = await _call_telegram(
        "sendMessage",
        {"chat_id": chat_id, "text": text_html, "parse_mode": "HTML", "disable_web_page_preview": True},
        bot_token=bot_token,
    )
    return {"telegram_message_id": (sent.get("result") or {}).get("message_id") if sent.get("ok") else None}


async def _claim_board(tenant_id: str, course: str, sunday: date) -> bool:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            insert into service_notice_log (tenant_id, notice_key, sent_on) values (%s, %s, %s)
            on conflict (tenant_id, notice_key, sent_on) do nothing
            returning notice_key
            """,
            (tenant_id, f"rhythm_board:{course}", sunday),
        )
    return row is not None


async def publish_board(tenant_id: str, day: date, *, bot_token: str, once: bool = False) -> dict[str, Any]:
    """Табло недели дня ``day`` в группу курса. ``once`` — расписание: одно табло за воскресенье."""
    course = course_slug()
    group = COURSE_GROUPS.get(course)
    if group is None:
        logger.info("rhythm_board_no_group", extra={"course": course})
        return {"status": "no_group"}
    sunday = rhythm.week_of(day)[1]
    week, people = await rhythm.board_people(tenant_id, course, sunday)
    text = rhythm.board_text(week, people)
    if text is None:
        return {"status": "empty"}
    if once and not await _claim_board(tenant_id, course, sunday):
        return {"status": "already_posted"}
    sent = await post_to_group(group[0], html.escape(text, quote=False), bot_token=bot_token)
    return {"status": "posted" if sent.get("telegram_message_id") else "failed", "text": text, **sent}


async def try_handle_rhythm_text(tenant: TenantContext, msg: TelegramMessage, *, trace_id: str) -> dict[str, Any] | None:
    """«ритм» и «табло» владельца (preview-админа) в личке; у остальных эти слова идут дальше."""
    text = str(msg.text or "").strip()
    card, board = _CARD_TEXT_RE.match(text), _BOARD_TEXT_RE.match(text)
    if not (card or board) or msg.chat_type != "private" or int(msg.user_id) not in preview_admin_ids():
        return None
    token = current_bot_binding().bot_token
    tenant_id = tenant.tenant_id
    today = rhythm.local_today()
    if card:
        body, markup = await build_card(tenant_id, msg.user_id, today, today=today, sign=True)
        await send_telegram_text(chat_id=str(msg.chat_id), text=body, bot_token=token, reply_markup=markup)
        return {"ok": True, "route": "academy_rhythm_card", "status": "sent", "trace_id": trace_id}
    result = await publish_board(tenant_id, today, bot_token=token)
    status = result["status"]
    reply = f"Табло отправлено в группу курса:\n\n{result['text']}" if status == "posted" else BOARD_REPLY[status]
    await send_telegram_text(chat_id=str(msg.chat_id), text=reply, bot_token=token)
    return {"ok": status == "posted", "route": "academy_rhythm_board", "status": status, "trace_id": trace_id}


async def enqueue_rhythm_cards(bindings: dict[str, BotBindingContext], today: date) -> int:
    """Карточки дня в platform_outbox (одна на человека в день); возвращает число новых."""
    course = course_slug()
    queued = 0
    for tenant_id, binding in bindings.items():
        try:
            members = await rhythm.rhythm_members(tenant_id, course)
            if not members:
                continue
            keys = {uid: card_key(course, uid, today) for uid in members}
            async with tenant_connection(tenant_id) as conn:
                rows = await fetch_all(
                    conn,
                    "select idempotency_key from platform_outbox where tenant_id = %s and idempotency_key = any(%s)",
                    (tenant_id, list(keys.values())),
                )
            planned = {row["idempotency_key"] for row in rows}
        except Exception:
            logger.warning("rhythm_cards_plan_failed", extra={"tenant_id": tenant_id}, exc_info=True)
            continue
        for uid, key in keys.items():
            if key in planned:
                continue
            try:
                text, markup = await build_card(tenant_id, uid, today, today=today, sign=False)
                async with tenant_connection(tenant_id) as conn:
                    row = await enqueue_outbox_event(
                        conn,
                        tenant_id=tenant_id,
                        event_type=EVENT_TYPE,
                        idempotency_key=key,
                        payload={
                            "chat_id": str(uid),
                            "text": text,
                            "reply_markup": markup,
                            "binding_id": binding.binding_id,
                            # Worker подписывает «📖 Урок дня» входом на сайт в момент отправки.
                            "site_login_user_id": uid,
                        },
                        due_at=datetime.now(timezone.utc),
                    )
                queued += 1 if row and row.get("created") else 0
            except Exception:
                logger.warning("rhythm_card_plan_failed", extra={"tenant_id": tenant_id}, exc_info=True)
    return queued


async def post_weekly_boards(bindings: dict[str, BotBindingContext], sunday: date) -> int:
    posted = 0
    for tenant_id, binding in bindings.items():
        try:
            result = await publish_board(tenant_id, sunday, bot_token=binding.bot_token, once=True)
        except Exception:
            logger.warning("rhythm_board_failed", extra={"tenant_id": tenant_id}, exc_info=True)
            continue
        if result["status"] in ("posted", "failed"):
            logger.info("rhythm_board", extra={"tenant_id": tenant_id, "status": result["status"]})
        posted += result["status"] == "posted"
    return posted


async def rhythm_step(bindings: dict[str, BotBindingContext], now: datetime | None = None) -> dict[str, int]:
    """Шаг планировщика (каждые 5 минут): утром — карточки, в воскресенье вечером — табло."""
    if not get_settings().platform_rhythm_enabled or not bindings or not course_slug():
        return {}
    local = rhythm.local_now(now)
    result: dict[str, int] = {}
    if in_window(local, rhythm.card_time(), CARD_WINDOW):
        result["rhythm_cards"] = await enqueue_rhythm_cards(bindings, local.date())
    if local.weekday() == 6 and in_window(local, rhythm.board_time(), BOARD_WINDOW):
        result["rhythm_boards"] = await post_weekly_boards(bindings, local.date())
    return result
