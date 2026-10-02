"""Модерация профиля сайта из кабинета /me/ в боте владельца (02.10.2026).

Партнёр сохраняет профиль на сайте → владельцу (PLATFORM_BILLING_OWNER_TELEGRAM_ID)
приходит карточка «Заявка на изменение сайта — <имя> (<ref>)» с «было → стало»
и кнопками ``prof:apply:<id>`` / ``prof:reject:<id>``; новое фото — отдельным
сообщением выше. «Отклонить» спрашивает причину: владелец отвечает (Reply) на
вопрос одной строкой, «-» — без причины. Партнёру — «Изменения на сайте
применены» или «Изменения на сайте отклонены: …» с кнопкой «Открыть кабинет».

Карточку шлёт бот того же процесса, который принял заявку (staging → staging-бот,
бой → боевой): нажатие обработает тот же Core. Потерялась карточка — владелец
пишет «/profiles» («правки сайтов»), бот присылает все ожидающие заново.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from app.cabinet.profile import FIELD_TITLES, changed_fields, field_value, media_id_from_url
from app.cabinet.service import (
    REQUEST_STATUS_WORDS,
    CabinetError,
    apply_profile_request,
    find_pending_by_short_id,
    list_pending_requests,
    load_media_body,
    load_owner_card,
    load_request_card,
    mark_reason_asked,
    record_owner_card,
    reject_profile_request,
    request_status,
)
from app.db import fetch_one, get_pool
from app.settings import get_settings
from app.subscriptions.service import resolve_partner_hostname
from app.telegram.api_base import TelegramApiBaseError, telegram_bot_api_url
from app.telegram.bindings import (
    BotBindingContext,
    binding_context_scope,
    current_bot_binding,
    resolve_bot_binding_context,
)
from app.telegram.delivery import _call_telegram, answer_callback_query, send_telegram_text
from app.telegram.referral_bonus import CABINET_BUTTON_LABEL, cabinet_page_url
from app.telegram.site_login import with_site_login
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

CALLBACK_RE = re.compile(r"^prof:(apply|reject):([0-9a-f]{32})$")
COMMAND_RE = re.compile(r"^(?:/profiles(?:@\w+)?|правки\s+сайтов)\s*[.!]?$", re.IGNORECASE)
REASON_PROMPT_PREFIX = "Причина отказа"
_SHORT_ID_RE = re.compile(r"№([0-9a-f]{8})")
REASON_MAX = 300
NO_REASON = {"-", "—", "–", "без причины"}
VALUE_MAX = 300  # длинное «о себе» целиком, остальное — до этой длины


def owner_id() -> int | None:
    configured = str(get_settings().platform_billing_owner_telegram_id or "").strip()
    return int(configured) if configured.isdigit() else None


def _owner_allowed(user_id: int) -> bool:
    owner = owner_id()
    return owner is not None and owner == int(user_id)


def _token(request_id: str) -> str:
    return str(request_id).replace("-", "")


def _request_id(token: str) -> str:
    return f"{token[:8]}-{token[8:12]}-{token[12:16]}-{token[16:20]}-{token[20:]}"


def short_id(request_id: str) -> str:
    return _token(request_id)[:8]


def _partner_name(card: dict[str, Any]) -> str:
    profile = card.get("public_profile") if isinstance(card.get("public_profile"), dict) else {}
    name = str(profile.get("display_name") or card.get("actor_name") or "").strip()
    return name or str(card.get("ref_code") or "партнёр")


def _site_host(card: dict[str, Any]) -> str | None:
    try:
        return resolve_partner_hostname(str(card.get("ref_code") or ""), card.get("public_profile"))
    except Exception:
        return None


def _shown(key: str, value: Any) -> str:
    if value in (None, ""):
        return "—"
    text = str(value)
    if key != "bio" and len(text) > VALUE_MAX:
        return text[: VALUE_MAX - 1] + "…"
    return text


def moderation_text(card: dict[str, Any], *, photo_sent: bool) -> str:
    """Карточка владельцу: что было и что станет по каждому изменённому полю."""
    changes = card.get("changes") or {}
    previous = card.get("previous") or {}
    host = _site_host(card)
    head = [
        f"Заявка на изменение сайта — {_partner_name(card)} ({card.get('ref_code')})",
        " · ".join(part for part in (host, f"заявка №{short_id(card['request_id'])}") if part),
        "",
    ]
    lines: list[str] = []
    for key in changed_fields(changes):
        title = FIELD_TITLES[key]
        before, after = field_value(previous, key), field_value(changes, key)
        if key == "photo_url":
            lines.append(f"{title}: новое — " + ("выше." if photo_sent else str(after)))
        elif key == "bio":
            lines.extend([f"{title}:", f"было: {_shown(key, before)}", f"стало: {_shown(key, after)}"])
        else:
            lines.append(f"{title}: {_shown(key, before)} → {_shown(key, after)}")
    return "\n".join(head + lines)


def moderation_keyboard(request_id: str) -> dict[str, Any]:
    token = _token(request_id)
    return {
        "inline_keyboard": [[
            {"text": "Применить", "callback_data": f"prof:apply:{token}"},
            {"text": "Отклонить", "callback_data": f"prof:reject:{token}"},
        ]]
    }


# Как поле звучит в середине фразы «Изменения применены: имя, фото, «о себе».»
_NOTICE_WORDS = {
    "display_name": "имя",
    "bio": "«о себе»",
    "photo_url": "фото",
    "phone": "телефон",
    "address": "адрес",
    "email": "e-mail",
}


def changed_titles_text(changes: dict[str, Any]) -> str:
    return ", ".join(_NOTICE_WORDS.get(key, FIELD_TITLES[key]) for key in changed_fields(changes))


def applied_text(changes: dict[str, Any]) -> str:
    fields = changed_titles_text(changes)
    return "\n".join(
        [
            "Изменения на сайте применены" + (f": {fields}." if fields else "."),
            "Сайт обновится в течение минуты.",
        ]
    )


def rejected_text(reason: str | None) -> str:
    head = f"Изменения на сайте отклонены: {reason}" if reason else "Изменения на сайте отклонены."
    if reason and not reason.endswith((".", "!", "?")):
        head += "."
    return "\n".join([head, "Поправьте в кабинете и отправьте ещё раз."])


async def _send_photo_upload(*, chat_id: str, body: bytes, caption: str, bot_token: str) -> dict[str, Any]:
    """Фото файлом (multipart): Telegram не ходит за ним по адресу — работает и
    до выкладки Core, который раздаёт фото на бою."""
    try:
        url = telegram_bot_api_url(bot_token, "sendPhoto")
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                data={"chat_id": chat_id, "caption": caption[:1024]},
                files={"photo": ("photo.jpg", body, "image/jpeg")},
            )
        data = response.json() if response.text else {}
    except (httpx.HTTPError, TelegramApiBaseError, ValueError):
        logger.warning("cabinet_profile_photo_send_failed")
        return {"ok": False}
    if response.status_code >= 400 or not data.get("ok"):
        logger.warning("cabinet_profile_photo_send_failed", extra={"status": response.status_code})
        return {"ok": False}
    return {"ok": True, "message_id": (data.get("result") or {}).get("message_id")}


async def send_moderation_card(tenant_id: str, request_id: str, *, chat_id: int) -> bool:
    """Фото (если меняется) и карточка с кнопками; в текущем binding-контексте."""
    card = await load_request_card(tenant_id, request_id)
    if card is None or card.get("status") != "pending":
        return False
    token = current_bot_binding().bot_token
    changes = card.get("changes") or {}
    photo_sent = False
    if changes.get("photo_url"):
        media_id = media_id_from_url(changes["photo_url"], get_settings().platform_partner_media_public_base)
        body = await load_media_body(tenant_id, media_id) if media_id else None
        if body:
            sent = await _send_photo_upload(
                chat_id=str(chat_id),
                body=body,
                caption=f"Новое фото — {_partner_name(card)} ({card.get('ref_code')})",
                bot_token=token,
            )
            photo_sent = bool(sent.get("ok"))
    result = await send_telegram_text(
        chat_id=str(chat_id),
        text=moderation_text(card, photo_sent=photo_sent),
        bot_token=token,
        reply_markup=moderation_keyboard(card["request_id"]),
    )
    if not result.get("ok"):
        logger.warning("cabinet_profile_card_send_failed", extra={"request_id": card["request_id"]})
        return False
    await record_owner_card(tenant_id, card["request_id"], chat_id=chat_id, message_id=result.get("message_id"))
    return True


async def process_bot_binding(tenant_id: str) -> BotBindingContext | None:
    """Бот этого процесса (PLATFORM_TELEGRAM_BOT_USERNAME) среди активных у тенанта."""
    username = str(get_settings().telegram_bot_username or "").lstrip("@").strip()
    if not username:
        return None
    async with get_pool().connection(timeout=get_settings().database_timeout_sec) as conn:
        row = await fetch_one(
            conn,
            """
            select b.binding_id
            from tenant_bot_bindings b
            where b.tenant_id = %s and b.status = 'active'
              and lower(to_jsonb(b)->>'bot_username') = lower(%s)
            limit 1
            """,
            (tenant_id, username),
        )
    if not row:
        return None
    return await resolve_bot_binding_context(str(row["binding_id"]))


async def _retire_card(tenant_id: str, request_id: str) -> None:
    """Снять кнопки с карточки заявки, которую заменили или отозвали (в binding-контексте)."""
    card = await load_owner_card(tenant_id, request_id)
    if not card:
        return
    await _call_telegram(
        "editMessageReplyMarkup",
        {"chat_id": card["chat_id"], "message_id": card["message_id"], "reply_markup": {"inline_keyboard": []}},
        bot_token=current_bot_binding().bot_token,
    )


async def notify_owner_about_profile_request(
    tenant_id: str, request_id: str, *, replaced_request_id: str | None = None
) -> bool:
    """Вызывается сайтом после новой заявки (в фоне, после ответа сайту). Сбой не
    теряет заявку: она ждёт, а владелец получит её по «/profiles»."""
    owner = owner_id()
    if owner is None:
        logger.warning("cabinet_profile_owner_not_configured")
        return False
    try:
        binding = await process_bot_binding(tenant_id)
        if binding is None:
            logger.warning("cabinet_profile_bot_binding_missing")
            return False
        with binding_context_scope(binding):
            sent = await send_moderation_card(tenant_id, request_id, chat_id=owner)
            if replaced_request_id:
                await _retire_card(tenant_id, replaced_request_id)
            return sent
    except Exception:
        logger.warning("cabinet_profile_owner_notify_failed", extra={"request_id": str(request_id)}, exc_info=True)
        return False


async def retire_owner_card(tenant_id: str, request_id: str) -> None:
    """Партнёр отозвал заявку на сайте: у владельца пропадают её кнопки."""
    try:
        binding = await process_bot_binding(tenant_id)
        if binding is None:
            return
        with binding_context_scope(binding):
            await _retire_card(tenant_id, request_id)
    except Exception:
        logger.warning("cabinet_profile_card_retire_failed", extra={"request_id": str(request_id)}, exc_info=True)


async def _deliver(chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None) -> dict[str, Any]:
    return await send_telegram_text(
        chat_id=str(chat_id), text=text, bot_token=current_bot_binding().bot_token, reply_markup=reply_markup
    )


async def _partner_markup(tenant_id: str, card: dict[str, Any]) -> dict[str, Any]:
    host = _site_host(card)
    site_url = f"https://{host}/" if host else None
    url = await with_site_login(
        cabinet_page_url(site_url), tenant_id=tenant_id, telegram_user_id=int(card["telegram_user_id"])
    )
    return {"inline_keyboard": [[{"text": CABINET_BUTTON_LABEL, "url": url}]]}


async def _notify_partner(tenant_id: str, request_id: str, text: str) -> None:
    card = await load_request_card(tenant_id, request_id)
    if not card:
        return
    chat = card.get("telegram_chat_id") or card.get("telegram_user_id")
    try:
        await _deliver(int(chat), text, reply_markup=await _partner_markup(tenant_id, card))
    except Exception:
        logger.warning("cabinet_profile_partner_notify_failed", extra={"request_id": str(request_id)}, exc_info=True)


async def _drop_card_buttons(callback: TelegramCallbackQuery) -> None:
    message = ((callback.raw or {}).get("callback_query") or {}).get("message") or {}
    if not message.get("message_id"):
        return
    try:
        await _call_telegram(
            "editMessageReplyMarkup",
            {"chat_id": callback.chat_id, "message_id": int(message["message_id"]), "reply_markup": {"inline_keyboard": []}},
            bot_token=current_bot_binding().bot_token,
        )
    except Exception:
        logger.warning("cabinet_profile_card_edit_failed")


_ERROR_TEXTS = {
    "request_not_found": "Заявка не найдена.",
    "site_not_found": "Сайт партнёра не найден — заявка не применена.",
}


def _status_note(status: str) -> str:
    return f"Заявка уже {REQUEST_STATUS_WORDS.get(status, status)}."


async def try_handle_profile_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str
) -> dict[str, Any] | None:
    match = CALLBACK_RE.fullmatch(callback.data)
    if not match:
        return None
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=current_bot_binding().bot_token)
    base = {"route": "cabinet_profile", "trace_id": trace_id}
    if callback.chat_type != "private":
        return {**base, "ok": True, "status": "private_chat_required"}
    if not _owner_allowed(callback.user_id):
        await _deliver(callback.chat_id, "Команда недоступна.")
        return {**base, "ok": False, "status": "forbidden"}
    action, token = match.groups()
    request_id = _request_id(token)
    try:
        if action == "apply":
            result = await apply_profile_request(tenant.tenant_id, request_id, reviewer_id=callback.user_id)
            if not result["applied"]:
                await _deliver(callback.chat_id, _status_note(result["status"]))
                return {**base, "ok": True, "status": result["status"]}
            request = result["request"]
            await _drop_card_buttons(callback)
            await _notify_partner(tenant.tenant_id, request_id, applied_text(request.get("changes") or {}))
            await _deliver(callback.chat_id, f"Применено: {request['ref_code']}. Сайт обновится в течение минуты.")
            return {**base, "ok": True, "status": "applied"}
        current = await request_status(tenant.tenant_id, request_id)
        if current is None:
            raise CabinetError("request_not_found", 404)
        if current["status"] != "pending":
            await _deliver(callback.chat_id, _status_note(current["status"]))
            return {**base, "ok": True, "status": current["status"]}
        sent = await _deliver(
            callback.chat_id,
            f"{REASON_PROMPT_PREFIX} — заявка №{short_id(request_id)} ({current['ref_code']}). "
            "Ответьте на это сообщение одной строкой; без причины — «-».",
            reply_markup={"force_reply": True, "input_field_placeholder": "Причина отказа"},
        )
        await mark_reason_asked(tenant.tenant_id, request_id, prompt_message_id=sent.get("message_id"))
        return {**base, "ok": True, "status": "reason_asked"}
    except CabinetError as exc:
        await _deliver(callback.chat_id, _ERROR_TEXTS.get(exc.code, "Не получилось, попробуйте ещё раз."))
        return {**base, "ok": False, "status": exc.code}


def _reply_source(msg: TelegramMessage) -> dict[str, Any]:
    return ((msg.raw or {}).get("message") or {}).get("reply_to_message") or {}


async def try_handle_profile_reject_reason(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    """Reply владельца на вопрос «Причина отказа — заявка №…» — это и есть причина."""
    if msg.chat_type != "private" or not (msg.text or "").strip():
        return None
    source = _reply_source(msg)
    if not source or not (source.get("from") or {}).get("is_bot"):
        return None
    prompt = str(source.get("text") or "")
    found = _SHORT_ID_RE.search(prompt) if prompt.startswith(REASON_PROMPT_PREFIX) else None
    if not found or not _owner_allowed(msg.user_id):
        return None
    base = {"route": "cabinet_profile_reason", "trace_id": trace_id}
    request = await find_pending_by_short_id(tenant.tenant_id, found.group(1))
    if request is None:
        await _deliver(msg.chat_id, "Заявка не найдена.")
        return {**base, "ok": False, "status": "not_found"}
    if request["status"] != "pending":
        await _deliver(msg.chat_id, _status_note(request["status"]))
        return {**base, "ok": True, "status": request["status"]}
    text = " ".join(msg.text.split())
    reason = None if text.lower() in NO_REASON else text[:REASON_MAX]
    result = await reject_profile_request(tenant.tenant_id, request["request_id"], reviewer_id=msg.user_id, reason=reason)
    if not result["rejected"]:
        await _deliver(msg.chat_id, _status_note(result["status"]))
        return {**base, "ok": True, "status": result["status"]}
    await _notify_partner(tenant.tenant_id, request["request_id"], rejected_text(reason))
    await _deliver(msg.chat_id, f"Отклонено: {request['ref_code']}. Партнёр получил ответ.")
    return {**base, "ok": True, "status": "rejected"}


async def try_handle_profile_requests_command(
    tenant: TenantContext, msg: TelegramMessage, *, trace_id: str
) -> dict[str, Any] | None:
    """«/profiles» владельца: все ожидающие заявки заново, с кнопками."""
    if msg.chat_type != "private" or not COMMAND_RE.fullmatch((msg.text or "").strip()):
        return None
    if not _owner_allowed(msg.user_id):
        return None
    rows = await list_pending_requests(tenant.tenant_id)
    if not rows:
        await _deliver(msg.chat_id, "Заявок на изменение сайтов нет.")
        return {"ok": True, "route": "cabinet_profile_list", "count": 0, "trace_id": trace_id}
    sent = 0
    for row in rows:
        sent += 1 if await send_moderation_card(tenant.tenant_id, row["request_id"], chat_id=msg.chat_id) else 0
    return {"ok": True, "route": "cabinet_profile_list", "count": sent, "trace_id": trace_id}
