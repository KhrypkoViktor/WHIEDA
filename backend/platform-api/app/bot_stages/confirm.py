"""«Да» / «Нет» ведущего под вопросом «{Имя} стал партнёром WHIEDA — подтвердить?» (04.11.2026).

Вопрос ставит ``app/crm/stage_link.py`` в ту минуту, когда карточка CRM перешла на этап «Партнёр» и у неё есть человек
в боте. Здесь — ответ ведущего:

  «Да»   человеку ставится статус ``partner_whieda`` (в Telegram и/или Max, где он открыл бота), ему уходит
         «Поздравляем — вы партнёр WHIEDA…» и новое меню, ведущему — подтверждение, владельцу — уведомление в личку;
  «Нет»  ничего не меняется, ведущий видит «Хорошо, статус не меняю».

Повторное «Да» ничего не дублирует: человек уже партнёр — ведущему отвечаем «уже партнёр», человеку не пишем второй раз.
Отвечать может хозяин карточки и владелец бота; чужой партнёр кнопку нажать не может. Кнопки с вопроса снимаются.
В логи имён не попадает.
"""

from __future__ import annotations

import logging
from typing import Any

from app.bot_stages.service import STAGE_PARTNER_WHIEDA, promote, user_ref
from app.bot_stages.telegram import announce_partner_whieda, send_reply
from app.crm import stage_link
from app.crm.service import safe_error
from app.funnel import texts
from app.funnel.guest import Reply
from app.settings import get_settings
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import _call_telegram, send_telegram_text
from app.telegram.update_parser import TelegramCallbackQuery
from app.tenancy import TenantContext

logger = logging.getLogger("whieda.bot_stages.confirm")


async def _clear_buttons(callback: TelegramCallbackQuery) -> None:
    """Снять «Да» / «Нет» с сообщения, под которым нажали (editMessageReplyMarkup). Сбой не критичен."""
    message = ((callback.raw or {}).get("callback_query") or {}).get("message") or {}
    message_id = message.get("message_id")
    if not message_id:
        return
    try:
        await _call_telegram(
            "editMessageReplyMarkup",
            {"chat_id": callback.chat_id, "message_id": int(message_id), "reply_markup": {"inline_keyboard": []}},
            bot_token=current_bot_binding().bot_token,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("bot_stage_clear_buttons_failed", extra=safe_error(exc))


def _host_label(callback: TelegramCallbackQuery) -> str:
    sender = ((callback.raw or {}).get("callback_query") or {}).get("from") or {}
    username = str(sender.get("username") or "").strip()
    if username:
        return f"@{username}"
    name = " ".join(part for part in (str(sender.get("first_name") or ""), str(sender.get("last_name") or "")) if part)
    return name or f"id {user_ref(callback.user_id)}"


async def _tell_owner(callback: TelegramCallbackQuery, name: str) -> None:
    """Владельцу — строка в личку (как служебные уведомления). Сам владелец подтвердил — ему и так видно."""
    owner = get_settings().platform_billing_owner_telegram_id
    if owner is None or int(owner) == int(callback.user_id):
        return
    try:
        await send_telegram_text(
            chat_id=str(int(owner)),
            text=texts.STAGE_OWNER_NOTICE.format(host=_host_label(callback), name=name),
            bot_token=current_bot_binding().bot_token,
        )
    except Exception as exc:  # noqa: BLE001 — подтверждение уже записано, уведомление не обязательное
        logger.warning("bot_stage_owner_notice_failed", extra=safe_error(exc))


async def handle_confirm_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, yes: bool, card_token: str, *, trace_id: str
) -> dict[str, Any]:
    """Кнопка ``stg:y:<карточка>`` (``yes``) или ``stg:n:<карточка>``. Ответ — новым сообщением ведущему."""
    base: dict[str, Any] = {"ok": True, "route": "stage_confirm", "trace_id": trace_id}
    contact_id = stage_link.card_id_from_token(card_token)
    card = await stage_link.load_card_for_confirm(tenant.tenant_id, contact_id)
    if card is None:
        await send_reply(callback.chat_id, Reply(texts.STAGE_CONFIRM_GONE))
        return {**base, "status": "gone"}
    owner = get_settings().platform_billing_owner_telegram_id
    is_owner = owner is not None and int(callback.user_id) == int(owner)
    if not is_owner and (card.host_user_id is None or int(callback.user_id) != int(card.host_user_id)):
        await send_reply(callback.chat_id, Reply(texts.STAGE_CONFIRM_FORBIDDEN))
        return {**base, "ok": False, "status": "forbidden"}
    name = card.name or "Человек"
    await _clear_buttons(callback)
    if not yes:
        await send_reply(callback.chat_id, Reply(texts.STAGE_CONFIRM_DECLINED.format(name=name)))
        return {**base, "status": "declined"}
    if card.status != stage_link.PARTNER_STATUS:
        await send_reply(callback.chat_id, Reply(texts.STAGE_CONFIRM_MOVED.format(name=name)))
        return {**base, "status": "card_moved"}
    if not card.persons:
        await send_reply(callback.chat_id, Reply(texts.STAGE_CONFIRM_NO_BOT.format(name=name)))
        return {**base, "status": "no_bot"}
    try:
        changed = [
            person
            for person in card.persons
            if await promote(tenant.tenant_id, person[0], person[1], STAGE_PARTNER_WHIEDA) == "changed"
        ]
    except Exception as exc:  # noqa: BLE001 — ведущему отвечаем, даже если база не отозвалась; нажать «Да» можно ещё раз
        logger.warning("bot_stage_confirm_failed", extra={"trace_id": trace_id, **safe_error(exc)})
        await send_reply(callback.chat_id, Reply(texts.GUEST_ERROR))
        return {**base, "ok": False, "status": "error"}
    if not changed:
        await send_reply(callback.chat_id, Reply(texts.STAGE_CONFIRM_ALREADY.format(name=name)))
        return {**base, "status": "already"}
    delivered = False
    for channel, user_id in changed:
        delivered = await announce_partner_whieda(tenant.tenant_id, channel, user_id) or delivered
    line = texts.STAGE_CONFIRM_DONE if delivered else texts.STAGE_CONFIRM_DONE_UNDELIVERED
    await send_reply(callback.chat_id, Reply(line.format(name=name)))
    await _tell_owner(callback, name)
    return {**base, "status": "confirmed", "delivered": delivered, "persons": len(changed)}
