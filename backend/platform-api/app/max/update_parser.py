"""Разбор обновлений Max Bot API: bot_started (deep link ?start=payload), message_created
и message_callback (нажатие кнопки inline-клавиатуры)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any

START_PREFIXES = ("/start", "start")


@dataclass
class MaxEvent:
    kind: str  # "start" | "message" | "chat_added" | "chat_removed" | "group_message" | "member_added"
    user_id: int
    chat_id: int | None
    text: str  # для start — payload (ref_… / site_…), для message — текст
    username: str | None
    display_name: str
    raw: dict[str, Any]
    # Вложение contact в личном сообщении (кнопка request_contact или «поделиться контактом»):
    contact_phone: str | None = None  # нормализованный номер «+79990000000»
    contact_user_id: int | None = None  # чей это контакт в Max (max_info.user_id); у чужого контакта не равен user_id
    # Номер из vcf_info, hash == HMAC-SHA256(токен бота, vcf_info) и contact_user_id == user_id: номер привязан к аккаунту отправителя в Max
    contact_verified: bool = False


@dataclass
class MaxCallbackEvent:
    """Нажатие кнопки inline-клавиатуры (update_type message_callback)."""

    callback_id: str  # идентификатор нажатия — для POST /answers
    payload: str  # payload кнопки (callback), до 1024 символов
    user_id: int  # кто нажал
    chat_id: int | None  # чат сообщения с клавиатурой (None, если сообщение уже удалено)
    message_id: str | None  # mid сообщения с клавиатурой
    chat_type: str  # "dialog" | "chat" | "channel" (по умолчанию dialog)
    username: str | None
    display_name: str
    raw: dict[str, Any]
    kind: str = "callback"
    text: str = ""  # для единообразия с MaxEvent (логи, общий разбор)


def _user_name(user: dict[str, Any]) -> tuple[str | None, str]:
    username = str(user.get("username") or "").strip() or None
    if username:
        return username, f"@{username}"[:240]
    full = " ".join(p.strip() for p in (str(user.get("first_name") or ""), str(user.get("last_name") or "")) if p.strip())
    name = full or str(user.get("name") or "").strip() or f"max:{user.get('user_id')}"
    return None, name[:240]


def parse_max_update(update: dict[str, Any]) -> MaxEvent | MaxCallbackEvent | None:
    kind = str((update or {}).get("update_type") or "")
    if kind == "message_callback":
        callback = update.get("callback") or {}
        user = callback.get("user") or {}
        callback_id = str(callback.get("callback_id") or "").strip()
        if not callback_id or user.get("user_id") is None or user.get("is_bot"):
            return None
        message = update.get("message") or {}
        recipient = message.get("recipient") or {}
        username, display = _user_name(user)
        mid = (message.get("body") or {}).get("mid")
        return MaxCallbackEvent(
            callback_id=callback_id, payload=str(callback.get("payload") or ""), user_id=int(user["user_id"]),
            chat_id=_int_or_none(recipient.get("chat_id")), message_id=str(mid) if mid else None,
            chat_type=str(recipient.get("chat_type") or "dialog"), username=username, display_name=display, raw=update,
        )
    if kind in {"bot_added", "bot_removed"}:
        # Бота добавили в группу или канал Max (или убрали): только так узнаём чаты бота (V24).
        chat_id = _int_or_none(update.get("chat_id"))
        if chat_id is None:
            return None
        user = update.get("user") or {}
        username, display = _user_name(user) if user else (None, "")
        return MaxEvent(
            kind="chat_added" if kind == "bot_added" else "chat_removed",
            user_id=int(user.get("user_id") or 0), chat_id=chat_id,
            text="channel" if update.get("is_channel") else "chat", username=username, display_name=display, raw=update,
        )
    if kind == "user_added":
        # Человек вступил в группу Max (по ссылке или его добавили): контроль состава моста (V27).
        chat_id = _int_or_none(update.get("chat_id"))
        user = update.get("user") or {}
        if chat_id is None or user.get("user_id") is None or update.get("is_channel"):
            return None
        username, display = _user_name(user)
        return MaxEvent(
            kind="member_added", user_id=int(user["user_id"]), chat_id=chat_id,
            text=str(update.get("inviter_id") or ""), username=username, display_name=display, raw=update,
        )
    if kind == "bot_started":
        user = update.get("user") or {}
        if user.get("user_id") is None:
            return None
        username, display = _user_name(user)
        return MaxEvent(
            kind="start", user_id=int(user["user_id"]), chat_id=_int_or_none(update.get("chat_id")),
            text=str(update.get("payload") or "").strip(), username=username, display_name=display, raw=update,
        )
    if kind == "message_created":
        message = update.get("message") or {}
        sender = message.get("sender") or {}
        recipient = message.get("recipient") or {}
        if sender.get("user_id") is None or sender.get("is_bot"):
            return None
        text = str((message.get("body") or {}).get("text") or "").strip()
        username, display = _user_name(sender)
        if str(recipient.get("chat_type") or "dialog") != "dialog":
            # Группа: советник там не отвечает; сообщение нужно только мосту потока (V27).
            return MaxEvent(
                kind="group_message", user_id=int(sender["user_id"]), chat_id=_int_or_none(recipient.get("chat_id")),
                text=text, username=username, display_name=display, raw=update,
            )
        event = MaxEvent(
            kind="message", user_id=int(sender["user_id"]), chat_id=_int_or_none(recipient.get("chat_id")),
            text=text, username=username, display_name=display, raw=update,
        )
        _attach_contact(event, (message.get("body") or {}).get("attachments"))
        # «/start ref_XXX», набранный руками, — тот же вход, что и deep link.
        lowered = text.lower()
        for prefix in START_PREFIXES:
            if lowered == prefix or lowered.startswith(prefix + " "):
                event.kind = "start"
                event.text = text[len(prefix):].strip()
                break
        return event
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


_TEL_RE = re.compile(r"^TEL[^:\r\n]*:(.+)$", re.IGNORECASE | re.MULTILINE)


def _vcf_text(vcf: Any) -> str:
    # Документация Max: перед хешированием «\r\n» в vcf_info — настоящие переносы строк.
    return str(vcf or "").replace("\\r\\n", "\r\n")


def contact_phone_from_vcf(vcf: str | None) -> str | None:
    """Первая строка TEL из VCF («TEL;TYPE=cell:79990000000») → «+79990000000»; не телефон → None."""
    from app.site_requests.contacts import normalize_phone

    found = _TEL_RE.search(_vcf_text(vcf))
    return normalize_phone(found.group(1).strip()) if found else None


def verify_max_contact_hash(vcf_info: str | None, hash_value: str | None, *, bot_token: str | None = None) -> bool:
    """Контакт прислан кнопкой request_contact, а не пересылкой: hash = HMAC-SHA256(токен бота, vcf_info)
    (https://dev.max.ru/docs-api/use-cases/sending-messages/keyboard, «Кнопка request_contact»). Кодировку
    документация не называет — принимаем hex и base64 (оба выводятся из секрета). Без токена — False."""
    if not vcf_info or not hash_value:
        return False
    if bot_token is None:
        from app.settings import get_settings

        bot_token = get_settings().max_bot_token
    if not bot_token:
        return False
    digest = hmac.new(bot_token.encode("utf-8"), _vcf_text(vcf_info).encode("utf-8"), hashlib.sha256).digest()
    given = str(hash_value).strip()
    return hmac.compare_digest(given.lower(), digest.hex()) or hmac.compare_digest(given, base64.b64encode(digest).decode())


def _attach_contact(event: MaxEvent, attachments: Any) -> None:
    """Вложение contact: payload {vcf_info, hash, max_info{user_id…}} (в запросе на отправку — ещё vcf_phone / contact_id).

    Вложение с payload или max_info не словарём пропускается целиком: сообщение разбирается дальше как обычное.
    ``contact_verified`` — только если номер взят из ``vcf_info``, hash подходит к нему и контакт принадлежит
    отправителю (``contact_user_id == user_id``): чужой контакт, пересланный отправителем, подтверждением не
    считается. Номер из ``vcf_phone`` (его в присланном боту сообщении нет, подписи у него тоже нет) —
    никогда не verified."""
    for item in attachments if isinstance(attachments, list) else []:
        if not isinstance(item, dict) or item.get("type") != "contact":
            continue
        payload = item.get("payload")
        max_info = payload.get("max_info") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or (max_info is not None and not isinstance(max_info, dict)):
            continue
        vcf = payload.get("vcf_info")
        phone = contact_phone_from_vcf(vcf)
        from_vcf = phone is not None
        if phone is None and payload.get("vcf_phone"):
            phone = contact_phone_from_vcf("TEL:" + str(payload["vcf_phone"]))
        event.contact_phone = phone
        event.contact_user_id = _int_or_none((max_info or {}).get("user_id")) or _int_or_none(payload.get("contact_id"))
        event.contact_verified = (
            from_vcf
            and event.contact_user_id is not None
            and event.contact_user_id == event.user_id
            and verify_max_contact_hash(vcf, payload.get("hash"))
        )
        return
