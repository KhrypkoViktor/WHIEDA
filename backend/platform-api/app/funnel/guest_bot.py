"""Гость встречи в Telegram и Max: старт по ссылке заявки, меню гостя, кнопки записи (срез 4 бот-воронки, 30.10.2026).

Логика — в ``app/funnel/lead_start.py`` (старт ``lead_<public_id>``), ``app/funnel/registration.py`` (запись,
перенос, отмена) и ``app/funnel/guest.py`` (кто гость, экраны). Здесь — подключение к мессенджерам:

  Telegram  ``handle_lead_start_telegram`` — ``/start lead_<id>``; ``try_handle_guest_menu_text`` — голый «/start»,
            «меню» и приветствие у гостя вместо кабинета партнёра; ``try_handle_guest_callback`` — кнопки.
  Max       ``handle_lead_start_max`` — ``bot_started`` с payload ``lead_<id>``; ``try_handle_guest_menu_max`` —
            голый старт и «меню» у гостя; ``max_guest_callback`` — те же кнопки (префиксы — ``GUEST_MAX_PREFIXES``).

Кнопки одинаковые в обоих каналах (callback_data = payload). Ответы идут новыми сообщениями; в Telegram после
переноса и отмены клавиатура старого сообщения снимается через editMessageReplyMarkup. Не гость, чужой тенант,
группа, сбой базы — обработчик возвращает ``None``/текст ошибки, и бот отвечает как прежде.

В логи имён и телефонов не попадает: ``user_ref`` — последние четыре цифры id.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.advisor.sql.text import detect_service_intent
from app.crm.service import safe_error
from app.db import tenant_connection
from app.funnel import registration, texts
from app.funnel.guest import (
    Reply,
    guest_menu_reply,
    is_guest_menu_word,
    load_guest_view,
    owner_card,
    resolve_guest,
    rows_to_max,
    rows_to_telegram,
)
from app.funnel.lead_start import start_from_lead
from app.funnel.system_word import FUNNEL_TENANT_ID, send_tour_max, send_tour_telegram
from app.leads.channels import accept_channel_referral_start
from app.max.client import send_max_text
from app.referral_bonus.service import accept_referral_start
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import _call_telegram, answer_callback_query, send_telegram_text
from app.telegram.navigation import is_newcomer_panel_request
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger("whieda.funnel.guest")

# Кнопки, которые обрабатывает гость-обработчик: mreg:<встреча>, mmove:<запись>, mmoveto:<запись>:<встреча>,
# mcancel:<запись>, funnel:meet, funnel:tour, funnel:ask. «Личный разбор» (funnel:consult) остаётся у system_word.
_ID = r"(\d{1,18})"
_ACTION_RE = re.compile(
    rf"^(?:(mreg|mmove|mcancel):{_ID}|(mmoveto):{_ID}:{_ID}|(funnel):(meet|tour|ask))$"
)

# Префиксы для диспетчера Max (register_max_callback): самый длинный подходящий побеждает, так что
# «funnel:meet» обгоняет общий префикс «funnel:» системного слова, а «funnel:consult» остаётся за ним.
GUEST_MAX_PREFIXES = (
    texts.REGISTER_CALLBACK_PREFIX,
    texts.MOVE_CALLBACK_PREFIX,
    texts.MOVE_TO_CALLBACK_PREFIX,
    texts.CANCEL_CALLBACK_PREFIX,
    texts.FUNNEL_MEET_CALLBACK,
    texts.FUNNEL_TOUR_CALLBACK,
    texts.FUNNEL_ASK_CALLBACK,
)


@dataclass(frozen=True)
class GuestAction:
    kind: str  # register | move_list | move_to | cancel | meet | tour | ask
    registration_id: int | None = None
    meeting_id: int | None = None


def parse_guest_callback(data: str | None) -> GuestAction | None:
    """callback_data кнопки гостя → действие; не наша кнопка или кривой номер — None."""
    match = _ACTION_RE.match(str(data or "").strip())
    if not match:
        return None
    short, short_id, move_to, move_reg, move_meeting, funnel, funnel_kind = match.groups()
    if short == "mreg":
        return GuestAction("register", meeting_id=int(short_id))
    if short == "mmove":
        return GuestAction("move_list", registration_id=int(short_id))
    if short == "mcancel":
        return GuestAction("cancel", registration_id=int(short_id))
    if move_to:
        return GuestAction("move_to", registration_id=int(move_reg), meeting_id=int(move_meeting))
    return GuestAction(funnel_kind)


async def _run_db_action(tenant_id: str, channel: str, user_id: int, action: GuestAction) -> registration.ActionResult:
    if action.kind == "register":
        return await registration.register(tenant_id, channel, user_id, int(action.meeting_id or 0))
    if action.kind == "move_list":
        return await registration.move_list(tenant_id, channel, user_id, int(action.registration_id or 0))
    if action.kind == "move_to":
        return await registration.move_to(
            tenant_id, channel, user_id, int(action.registration_id or 0), int(action.meeting_id or 0)
        )
    if action.kind == "cancel":
        return await registration.cancel(tenant_id, channel, user_id, int(action.registration_id or 0))
    return await registration.list_meetings(tenant_id, channel, user_id)


def _user_ref(user_id: int) -> str:
    return str(user_id)[-4:]


async def _guest_menu(tenant_id: str, channel: str, user_id: int) -> Reply | None:
    """Меню гостя или None, если человек не гость. Сбой базы — None: бот ответит как прежде."""
    try:
        async with tenant_connection(tenant_id) as conn:
            guest = await resolve_guest(conn, tenant_id, channel, user_id)
            if guest is None:
                return None
            registration_view, open_meetings = await load_guest_view(conn, tenant_id, channel, user_id)
        card = await owner_card(tenant_id, guest.owner_actor_id)
    except Exception as exc:  # noqa: BLE001 — партнёрам и всем остальным бот отвечает как прежде
        logger.warning("guest_menu_failed", extra={"channel": channel, **safe_error(exc)})
        return None
    inviter_name = ((card.display_name if card else "") or "").strip() or None
    return guest_menu_reply(inviter_name, registration=registration_view, open_meetings=open_meetings)


# ---- Telegram --------------------------------------------------------------------------------------


async def _send_telegram(chat_id: int | str, reply: Reply) -> None:
    await send_telegram_text(
        chat_id=str(chat_id), text=reply.text, bot_token=current_bot_binding().bot_token,
        reply_markup=rows_to_telegram(reply.rows),
    )


async def handle_lead_start_telegram(
    tenant: TenantContext, msg: TelegramMessage, public_id: str, *, trace_id: str | None = None
) -> dict[str, Any] | None:
    """``/start lead_<public_id>``. ``None`` — заявки нет (или сбой): вызывающий ведёт обычный старт."""
    if tenant.tenant_id != FUNNEL_TENANT_ID or not public_id:
        return None
    if msg.chat_type != "private":
        return {"ok": True, "route": "lead_start", "status": "private_chat_required", "trace_id": trace_id}

    async def accept_partner(invite_code: str) -> Any:
        return await accept_referral_start(
            tenant.tenant_id, telegram_user_id=msg.user_id, telegram_chat_id=msg.chat_id,
            invite_code=invite_code, raw_update=msg.raw,
        )

    try:
        result = await start_from_lead(
            tenant.tenant_id, channel="telegram", channel_user_id=msg.user_id, public_id=public_id,
            accept_partner=accept_partner,
        )
    except Exception as exc:  # noqa: BLE001 — человек не должен остаться без ответа: дальше обычный старт
        logger.warning("lead_start_failed", extra={"channel": "telegram", "trace_id": trace_id, **safe_error(exc)})
        return None
    if result is None:
        return None
    await _send_telegram(msg.chat_id, result.reply)
    logger.info(
        "lead_start",
        extra={
            "channel": "telegram", "status": result.status, "attribution": result.attribution,
            "with_partner": bool(result.inviter_name), "user_ref": _user_ref(msg.user_id), "trace_id": trace_id,
        },
    )
    return {
        "ok": True, "route": "lead_start", "status": result.status, "attribution": result.attribution,
        "trace_id": trace_id,
    }


async def try_handle_guest_menu_text(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str | None = None
) -> dict[str, Any] | None:
    """Голый «/start», «с чего начать», приветствие и «меню» в личном чате бота WWC: гостю — меню гостя вместо
    кабинета партнёра. Не гость (партнёр и все остальные), другой тенант, группа, другой текст — ``None``."""
    if tenant.tenant_id != FUNNEL_TENANT_ID or msg.chat_type != "private":
        return None
    text = msg.text
    if not (is_guest_menu_word(text) or is_newcomer_panel_request(text) or detect_service_intent(text) == "greeting"):
        return None
    reply = await _guest_menu(tenant.tenant_id, "telegram", msg.user_id)
    if reply is None:
        return None
    await _send_telegram(msg.chat_id, reply)
    logger.info("guest_menu", extra={"channel": "telegram", "user_ref": _user_ref(msg.user_id), "trace_id": trace_id})
    return {"ok": True, "route": "guest_menu", "status": "shown", "trace_id": trace_id}


async def _clear_telegram_keyboard(callback: TelegramCallbackQuery, bot_token: str, trace_id: str | None) -> None:
    """Снять кнопки с сообщения, под которым нажали (editMessageReplyMarkup). Сбой не критичен."""
    message = ((callback.raw or {}).get("callback_query") or {}).get("message") or {}
    message_id = message.get("message_id")
    if not message_id:
        return
    try:
        await _call_telegram(
            "editMessageReplyMarkup",
            {"chat_id": callback.chat_id, "message_id": int(message_id), "reply_markup": {"inline_keyboard": []}},
            bot_token=bot_token,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("guest_clear_keyboard_failed", extra={"trace_id": trace_id, **safe_error(exc)})


async def try_handle_guest_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str | None = None
) -> dict[str, Any] | None:
    """Кнопки записи и меню гостя в Telegram. Не наша кнопка или чужой тенант — ``None``."""
    action = parse_guest_callback(callback.data)
    if action is None or tenant.tenant_id != FUNNEL_TENANT_ID:
        return None
    bot_token = current_bot_binding().bot_token
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=bot_token)
    base: dict[str, Any] = {"ok": True, "route": "guest_callback", "trace_id": trace_id}
    if callback.chat_type != "private":
        return {**base, "status": "private_chat_required"}
    if action.kind == "tour":
        tour = await send_tour_telegram(tenant, callback.chat_id, trace_id=trace_id)
        return {**base, "status": "tour", "tour": tour.get("status")}
    if action.kind == "ask":
        await send_telegram_text(chat_id=str(callback.chat_id), text=texts.ASK_PROMPT, bot_token=bot_token)
        return {**base, "status": "ask"}
    try:
        result = await _run_db_action(tenant.tenant_id, "telegram", callback.user_id, action)
    except Exception as exc:  # noqa: BLE001 — на кнопку надо ответить, даже если база не отозвалась
        logger.warning("guest_callback_failed", extra={"channel": "telegram", "kind": action.kind, "trace_id": trace_id, **safe_error(exc)})
        await send_telegram_text(chat_id=str(callback.chat_id), text=texts.GUEST_ERROR, bot_token=bot_token)
        return {**base, "ok": False, "status": "error"}
    await _send_telegram(callback.chat_id, result.reply)
    if result.clear_keyboard:
        await _clear_telegram_keyboard(callback, bot_token, trace_id)
    return {**base, "status": result.status}


# ---- Max -------------------------------------------------------------------------------------------


async def _send_max(chat_id: int | None, user_id: int, reply: Reply) -> None:
    await send_max_text(chat_id=chat_id or None, user_id=user_id, text=reply.text, buttons=rows_to_max(reply.rows))


async def handle_lead_start_max(
    tenant: TenantContext, event: Any, public_id: str, trace_id: str
) -> dict[str, Any] | None:
    """``bot_started`` с payload ``lead_<public_id>`` в Max. ``None`` — заявки нет (или сбой): обычный старт."""
    if tenant.tenant_id != FUNNEL_TENANT_ID or not public_id:
        return None

    async def accept_partner(invite_code: str) -> Any:
        return await accept_channel_referral_start(
            tenant.tenant_id, channel="max", channel_user_id=event.user_id, channel_chat_id=event.chat_id,
            username=event.username, display_name=event.display_name, invite_code=invite_code,
        )

    try:
        result = await start_from_lead(
            tenant.tenant_id, channel="max", channel_user_id=event.user_id, public_id=public_id,
            accept_partner=accept_partner,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("lead_start_failed", extra={"channel": "max", "trace_id": trace_id, **safe_error(exc)})
        return None
    if result is None:
        return None
    await _send_max(event.chat_id, event.user_id, result.reply)
    logger.info(
        "lead_start",
        extra={
            "channel": "max", "status": result.status, "attribution": result.attribution,
            "with_partner": bool(result.inviter_name), "user_ref": _user_ref(event.user_id), "trace_id": trace_id,
        },
    )
    return {
        "ok": True, "route": "max_lead_start", "status": result.status, "attribution": result.attribution,
        "trace_id": trace_id,
    }


async def try_handle_guest_menu_max(tenant: TenantContext, event: Any, trace_id: str) -> dict[str, Any] | None:
    """Голый старт и «меню» в Max: гостю — меню гостя. Не гость или другой тенант — ``None`` (ответ как прежде)."""
    if tenant.tenant_id != FUNNEL_TENANT_ID:
        return None
    reply = await _guest_menu(tenant.tenant_id, "max", event.user_id)
    if reply is None:
        return None
    await _send_max(event.chat_id, event.user_id, reply)
    logger.info("guest_menu", extra={"channel": "max", "user_ref": _user_ref(event.user_id), "trace_id": trace_id})
    return {"ok": True, "route": "max_guest_menu", "status": "shown", "trace_id": trace_id}


async def max_guest_callback(tenant: TenantContext, event: Any, trace_id: str) -> None:
    """Кнопки записи и меню гостя в Max (регистрируются в app/max/processor.py по ``GUEST_MAX_PREFIXES``).
    Ответ — новое сообщение; сам ответ на нажатие диспетчер даёт пустой."""
    action = parse_guest_callback(str(event.payload))
    if action is None or tenant.tenant_id != FUNNEL_TENANT_ID:
        return None
    if str(getattr(event, "chat_type", "dialog") or "dialog") != "dialog":
        return None
    chat_id = event.chat_id or None
    if action.kind == "tour":
        await send_tour_max(tenant, chat_id=chat_id, user_id=event.user_id, trace_id=trace_id)
        return None
    if action.kind == "ask":
        await send_max_text(chat_id=chat_id, user_id=event.user_id, text=texts.ASK_PROMPT)
        return None
    try:
        result = await _run_db_action(tenant.tenant_id, "max", event.user_id, action)
    except Exception as exc:  # noqa: BLE001
        logger.warning("guest_callback_failed", extra={"channel": "max", "kind": action.kind, "trace_id": trace_id, **safe_error(exc)})
        await send_max_text(chat_id=chat_id, user_id=event.user_id, text=texts.GUEST_ERROR)
        return None
    await _send_max(chat_id, event.user_id, result.reply)
    return None
