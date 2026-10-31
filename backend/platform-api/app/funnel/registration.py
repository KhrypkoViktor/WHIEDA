"""Запись на встречу из бота: «Записаться», «Перенести», «Отменить запись» (срез 4 бот-воронки, 30.10.2026).

Одни и те же действия для Telegram и Max: callback_data и payload кнопок совпадают.

  mreg:<встреча>                    записать (повтор — «уже записаны»);
  mmove:<запись>                    показать другие ближайшие встречи кнопками;
  mmoveto:<запись>:<встреча>        перенести: прежняя запись ``moved``, новая ``registered`` с ``moved_from``;
  mcancel:<запись>                  отменить: ``cancelled``;
  funnel:meet                       список встреч под видео-туром (те же кнопки ``mreg:``);
  mjoin:<запись>                    «Присоединиться» (срез 5): ``joined`` и ссылка входа кнопкой.

Каждое действие открывает одно подключение, меняет запись и, если у заявки человека есть карточка CRM, её
метку «записан <дд.мм>» и заметку. Результат — ``ActionResult`` с экраном (``Reply``), который отправляет канал
(``app/funnel/guest_bot.py``). Прошедшая или отменённая встреча — вежливый отказ с другими встречами.
Чужая, перенесённая или отменённая запись по старой кнопке — «запись уже изменена».

Логи — без имён и телефонов: номер записи, номер встречи и последние четыре цифры id человека.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.db import tenant_connection
from app.funnel import texts
from app.funnel.guest import (
    LIST_MEETINGS,
    Reply,
    Rows,
    control_rows,
    register_rows,
    short_when,
)
from app.leads.meeting_leads import card_registration_event, load_lead_by_channel_user, load_lead_card_id
from app.meetings import registrations
from app.meetings.service import format_msk, normalize_join_url, utcnow

logger = logging.getLogger("whieda.funnel.registration")


@dataclass(frozen=True)
class ActionResult:
    """``status`` — коротко для журнала и ответа обработчика; ``clear_keyboard`` — снять кнопки с сообщения,
    под которым нажали (Telegram: editMessageReplyMarkup), чтобы старые кнопки не оставались."""

    reply: Reply
    status: str
    clear_keyboard: bool = False


def _user_ref(user_id: int) -> str:
    return str(user_id)[-4:]


def _log(action: str, channel: str, user_id: int, status: str, **extra: Any) -> None:
    logger.info("funnel_registration", extra={"action": action, "channel": channel, "status": status, "user_ref": _user_ref(user_id), **extra})


def _when(meeting: dict[str, Any] | None, now: datetime) -> str:
    return format_msk(meeting["starts_at"], now=now) if meeting else ""


def _stale(*, clear: bool = True) -> ActionResult:
    return ActionResult(Reply(texts.REGISTRATION_STALE), "stale", clear_keyboard=clear)


def _too_late() -> ActionResult:
    return ActionResult(Reply(texts.REGISTRATION_TOO_LATE), "too_late")


def _move_rows(registration_id: int, meetings: list[dict[str, Any]], now: datetime) -> Rows:
    return [
        [(short_when(m["starts_at"], now=now), f"{texts.MOVE_TO_CALLBACK_PREFIX}{int(registration_id)}:{int(m['meeting_id'])}")]
        for m in meetings
    ]


async def list_meetings(tenant_id: str, channel: str, channel_user_id: int, *, now: datetime | None = None) -> ActionResult:
    """``funnel:meet``: ближайшие живые встречи (до трёх) кнопками «Записаться: …»; куда человек уже записан —
    «Вы записаны: …»."""
    current = now or utcnow()
    async with tenant_connection(tenant_id) as conn:
        meetings = await registrations.open_meetings(conn, tenant_id, limit=LIST_MEETINGS, now=current)
        active = await registrations.active_registrations(
            conn, tenant_id, channel=channel, channel_user_id=channel_user_id, limit=10, now=current
        ) if meetings else []
    if not meetings:
        _log("meet", channel, channel_user_id, "no_meetings")
        return ActionResult(Reply(texts.GUEST_NO_MEETINGS), "no_meetings")
    registered = frozenset(int(meeting["meeting_id"]) for _, meeting in active)
    _log("meet", channel, channel_user_id, "listed", count=len(meetings))
    return ActionResult(Reply(texts.GUEST_PICK_MEETING, register_rows(meetings, registered, now=current)), "listed")


async def register(
    tenant_id: str, channel: str, channel_user_id: int, meeting_id: int, *, now: datetime | None = None
) -> ActionResult:
    """``mreg:<встреча>``."""
    current = now or utcnow()
    async with tenant_connection(tenant_id) as conn:
        lead = await load_lead_by_channel_user(conn, tenant_id, channel, channel_user_id)
        outcome = await registrations.register(
            conn,
            tenant_id,
            meeting_id=meeting_id,
            channel=channel,
            channel_user_id=channel_user_id,
            lead_id=lead.lead_id if lead else None,
            now=current,
        )
        if outcome.status == "registered" and lead is not None and outcome.meeting is not None:
            await card_registration_event(
                conn, tenant_id, lead.crm_card, "registered", starts_at=outcome.meeting["starts_at"], now=current
            )
        others: list[dict[str, Any]] = []
        if outcome.status in ("closed", "not_found"):
            others = await registrations.open_meetings(conn, tenant_id, limit=LIST_MEETINGS, now=current)
    registration = outcome.registration
    _log(
        "register", channel, channel_user_id, outcome.status, meeting_id=meeting_id,
        registration_id=registration.registration_id if registration else None,
    )
    if outcome.status in ("registered", "already") and registration is not None:
        text = (texts.REGISTRATION_CONFIRMED if outcome.status == "registered" else texts.REGISTRATION_ALREADY).format(
            when=_when(outcome.meeting, current)
        )
        rows = control_rows(registration.registration_id) if registration.status == "registered" else []
        return ActionResult(Reply(text, rows), outcome.status)
    if outcome.status == "already":
        return ActionResult(Reply(texts.REGISTRATION_ALREADY.format(when=_when(outcome.meeting, current))), "already")
    if others:
        text = f"{texts.REGISTRATION_CLOSED}\n{texts.REGISTRATION_CLOSED_PICK}"
        return ActionResult(Reply(text, register_rows(others, now=current)), "closed")
    return ActionResult(Reply(f"{texts.REGISTRATION_CLOSED}\n{texts.GUEST_NO_MEETINGS}"), "closed")


async def move_list(
    tenant_id: str, channel: str, channel_user_id: int, registration_id: int, *, now: datetime | None = None
) -> ActionResult:
    """``mmove:<запись>``: другие ближайшие встречи кнопками ``mmoveto:<запись>:<встреча>``."""
    current = now or utcnow()
    async with tenant_connection(tenant_id) as conn:
        registration = await registrations.get_registration(
            conn, tenant_id, registration_id=registration_id, channel=channel, channel_user_id=channel_user_id
        )
        if registration is None or registration.status != "registered":
            _log("move_list", channel, channel_user_id, "stale", registration_id=registration_id)
            return _stale(clear=False)
        meeting = await registrations.get_meeting_row(conn, tenant_id, registration.meeting_id)
        if not registrations.changeable(meeting, current):
            _log("move_list", channel, channel_user_id, "too_late", registration_id=registration_id)
            return _too_late()
        others = await registrations.open_meetings(
            conn, tenant_id, limit=LIST_MEETINGS, exclude_meeting_id=registration.meeting_id, now=current
        )
    if not others:
        _log("move_list", channel, channel_user_id, "no_others", registration_id=registration_id)
        return ActionResult(
            Reply(texts.MOVE_NO_OTHERS.format(when=_when(meeting, current)), control_rows(registration_id)), "no_others"
        )
    _log("move_list", channel, channel_user_id, "listed", registration_id=registration_id, count=len(others))
    return ActionResult(Reply(texts.MOVE_PICK, _move_rows(registration_id, others, current)), "listed")


async def move_to(
    tenant_id: str, channel: str, channel_user_id: int, registration_id: int, meeting_id: int, *, now: datetime | None = None
) -> ActionResult:
    """``mmoveto:<запись>:<встреча>``: перенос. Старая запись — ``moved``, новая — ``registered`` с ``moved_from``."""
    current = now or utcnow()
    async with tenant_connection(tenant_id) as conn:
        outcome = await registrations.move(
            conn,
            tenant_id,
            registration_id=registration_id,
            to_meeting_id=meeting_id,
            channel=channel,
            channel_user_id=channel_user_id,
            now=current,
        )
        if outcome.status == "moved" and outcome.registration is not None and outcome.meeting is not None:
            card = await load_lead_card_id(conn, tenant_id, outcome.registration.lead_id)
            old_meeting = outcome.old_meeting
            if card and old_meeting is not None:
                await card_registration_event(
                    conn, tenant_id, card, "moved",
                    starts_at=outcome.meeting["starts_at"], old_starts_at=old_meeting["starts_at"], now=current,
                )
        others: list[dict[str, Any]] = []
        if outcome.status == "closed" and outcome.old_registration is not None:
            others = await registrations.open_meetings(
                conn, tenant_id, limit=LIST_MEETINGS, exclude_meeting_id=outcome.old_registration.meeting_id, now=current
            )
    new = outcome.registration
    _log(
        "move_to", channel, channel_user_id, outcome.status, registration_id=registration_id, meeting_id=meeting_id,
        new_registration_id=new.registration_id if new and outcome.status == "moved" else None,
    )
    if outcome.status == "moved" and new is not None:
        text = texts.MOVE_DONE.format(when=_when(outcome.meeting, current))
        return ActionResult(Reply(text, control_rows(new.registration_id)), "moved", clear_keyboard=True)
    if outcome.status in ("not_found", "not_active"):
        return _stale()
    if outcome.status == "too_late":
        return _too_late()
    if outcome.status in ("same", "already"):
        return ActionResult(Reply(texts.MOVE_ALREADY), outcome.status)
    if others and outcome.old_registration is not None:
        text = f"{texts.REGISTRATION_CLOSED}\n{texts.MOVE_PICK}"
        return ActionResult(Reply(text, _move_rows(registration_id, others, current)), "closed", clear_keyboard=True)
    return ActionResult(Reply(texts.REGISTRATION_CLOSED), "closed")


async def cancel(
    tenant_id: str, channel: str, channel_user_id: int, registration_id: int, *, now: datetime | None = None
) -> ActionResult:
    """``mcancel:<запись>``: запись ``cancelled`` (строка остаётся для цифр воронки), метка снимается."""
    current = now or utcnow()
    async with tenant_connection(tenant_id) as conn:
        outcome = await registrations.cancel(
            conn, tenant_id, registration_id=registration_id, channel=channel, channel_user_id=channel_user_id, now=current
        )
        if outcome.status == "cancelled" and outcome.registration is not None and outcome.meeting is not None:
            card = await load_lead_card_id(conn, tenant_id, outcome.registration.lead_id)
            await card_registration_event(
                conn, tenant_id, card, "cancelled", starts_at=outcome.meeting["starts_at"], now=current
            )
    _log("cancel", channel, channel_user_id, outcome.status, registration_id=registration_id)
    if outcome.status == "cancelled":
        text = texts.CANCEL_DONE.format(when=_when(outcome.meeting, current))
        return ActionResult(Reply(text), "cancelled", clear_keyboard=True)
    if outcome.status == "too_late":
        return _too_late()
    return _stale()


async def join(
    tenant_id: str, channel: str, channel_user_id: int, registration_id: int, *, now: datetime | None = None
) -> ActionResult:
    """``mjoin:<запись>``: первое нажатие в окне встречи — ``joined`` и кнопка-ссылка «Открыть встречу»; повторное —
    та же ссылка; ссылки ещё нет — «пришлём за несколько минут»; не в окне — вежливый ответ, запись не меняется."""
    current = now or utcnow()
    async with tenant_connection(tenant_id) as conn:
        outcome = await registrations.join(
            conn, tenant_id, registration_id=registration_id, channel=channel, channel_user_id=channel_user_id, now=current
        )
    _log("join", channel, channel_user_id, outcome.status, registration_id=registration_id)
    meeting = outcome.meeting
    if outcome.status in ("joined", "again"):
        url = normalize_join_url((meeting or {}).get("join_url"))
        if url is None:
            return ActionResult(Reply(texts.JOIN_NO_LINK), outcome.status)
        return ActionResult(Reply(texts.JOIN_OPEN, [[(texts.OPEN_MEETING_BUTTON, url)]]), outcome.status)
    if outcome.status == "too_early":
        return ActionResult(Reply(texts.JOIN_TOO_EARLY.format(when=_when(meeting, current))), "too_early")
    if outcome.status == "too_late":
        return ActionResult(Reply(texts.JOIN_TOO_LATE), "too_late")
    if outcome.status == "cancelled":
        return ActionResult(Reply(texts.JOIN_CANCELLED), "cancelled")
    return _stale(clear=False)

