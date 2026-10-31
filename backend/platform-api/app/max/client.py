"""HTTP-клиент Max Bot API (https://dev.max.ru/docs-api).

Токен — только из настроек (PLATFORM_MAX_BOT_TOKEN) и только в заголовке
Authorization; в логи и ответы не попадает.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from functools import lru_cache
from pathlib import Path
from typing import Any

import certifi
import httpx

from app.settings import get_settings

logger = logging.getLogger("whieda.max")


class MaxNotConfigured(RuntimeError):
    pass


@lru_cache(maxsize=1)
def max_ssl_context() -> ssl.SSLContext:
    """TLS-контекст для вызовов к API Max: корни certifi (как у httpx по умолчанию) + цепочка Минцифры
    (PLATFORM_MAX_CA_FILE).

    С 19.07.2026 platform-api2.max.ru отдаёт сертификат НУЦ Минцифры; httpx по умолчанию верит
    только certifi, системное хранилище не читает. Цепочку добавляем лишь в этот контекст, а не в
    общее хранилище процесса и не другим клиентам. Нет файла или он не читается — предупреждение в лог
    один раз (результат кэшируется) и контекст по умолчанию; проверку сертификата не отключаем никогда.
    """
    context = ssl.create_default_context(cafile=certifi.where())
    ca_file = get_settings().max_ca_file
    if not ca_file or not Path(ca_file).is_file():
        logger.warning("max_ca_file_missing", extra={"path": ca_file})
        return context
    try:
        context.load_verify_locations(cafile=ca_file)
    except (OSError, ssl.SSLError) as exc:
        logger.warning("max_ca_file_unreadable", extra={"path": ca_file, "error": type(exc).__name__})
    return context


def _headers() -> dict[str, str]:
    token = get_settings().max_bot_token
    if not token:
        raise MaxNotConfigured("PLATFORM_MAX_BOT_TOKEN is not set")
    return {"Authorization": token, "Content-Type": "application/json"}


async def send_max_text(
    *,
    user_id: int | str | None = None,
    chat_id: int | str | None = None,
    text: str,
    disable_link_preview: bool = False,
    buttons: list[list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """POST /messages?chat_id=… | ?user_id=…  {text}. Пустой текст не шлём.
    buttons — ряды кнопок inline-клавиатуры (max_button_*): с ними отправка идёт через send_max_message."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "skipped": "empty"}
    if not user_id and not chat_id:
        raise ValueError("user_id or chat_id required")
    if buttons:
        return await send_max_message(
            chat_id=chat_id or None, user_id=user_id or None, text=text, buttons=buttons,
            disable_link_preview=disable_link_preview,
        )
    params: dict[str, str] = {}
    if chat_id:
        params["chat_id"] = str(chat_id)
    elif user_id:
        params["user_id"] = str(user_id)
    if disable_link_preview:
        params["disable_link_preview"] = "true"
    base = get_settings().max_api_base.rstrip("/")
    async with httpx.AsyncClient(timeout=20.0, verify=max_ssl_context()) as client:
        resp = await client.post(f"{base}/messages", params=params, headers=_headers(), json={"text": text[:4000]})
    if resp.status_code >= 400:
        logger.warning("max_send_failed", extra={"status": resp.status_code, "body": resp.text[:300]})
        return {"ok": False, "status": resp.status_code}
    return {"ok": True, "result": resp.json()}


# bot_added / bot_removed — единственный способ узнать чаты бота: GET /chats убрали в июне 2026.
# user_added — контроль состава группы потока (мост V27, 10.10.2026).
# message_callback — нажатия кнопок inline-клавиатуры (M2, 30.10.2026); подписку в Max нужно переоформить (POST /subscriptions).
UPDATE_TYPES = ["bot_started", "message_created", "bot_added", "bot_removed", "user_added", "message_callback"]


async def send_max_message(
    *,
    chat_id: int | str | None = None,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    html_format: bool = False,
    reply_to_mid: str | None = None,
    user_id: int | str | None = None,
    buttons: list[list[dict[str, Any]]] | None = None,
    disable_link_preview: bool = False,
) -> dict[str, Any]:
    """POST /messages?chat_id=… (или ?user_id=…) с вложениями. Вложение только что загружено — Max
    может ответить attachment.not.ready: ждём и повторяем. reply_to_mid — ответ
    на сообщение (link type reply). В ответе — mid нового сообщения.
    buttons — ряды кнопок: уходят последним вложением inline_keyboard (лимиты — max_inline_keyboard)."""
    if not chat_id and not user_id:
        raise ValueError("user_id or chat_id required")
    params = {"chat_id": str(chat_id)} if chat_id else {"user_id": str(user_id)}
    if disable_link_preview:
        params["disable_link_preview"] = "true"
    body: dict[str, Any] = {"text": (text or "")[:4000] or None}
    if attachments or buttons:
        body["attachments"] = [*(attachments or []), *([max_inline_keyboard(buttons)] if buttons else [])]
    if html_format:
        body["format"] = "html"
    if reply_to_mid:
        body["link"] = {"type": "reply", "mid": str(reply_to_mid)}
    base = get_settings().max_api_base.rstrip("/")
    async with httpx.AsyncClient(timeout=30.0, verify=max_ssl_context()) as client:
        for delay in (0, 1, 2, 4, 8):
            if delay:
                await asyncio.sleep(delay)
            resp = await client.post(f"{base}/messages", params=params, headers=_headers(), json=body)
            if resp.status_code < 400:
                result = resp.json()
                mid = (((result or {}).get("message") or {}).get("body") or {}).get("mid")
                return {"ok": True, "result": result, "mid": str(mid) if mid else None}
            if "attachment.not.ready" not in resp.text:
                break
    logger.warning("max_send_failed", extra={"status": resp.status_code, "body": resp.text[:300]})
    return {"ok": False, "status": resp.status_code}


DEFAULT_UPLOAD_NAMES = {"image": "photo.jpg", "video": "video.mp4", "audio": "audio.ogg", "file": "file.bin"}


async def upload_max_media(kind: str, body: bytes, *, filename: str | None = None) -> dict[str, Any]:
    """POST /uploads?type=image|video|audio|file → загрузка файла (поле data) → вложение
    для /messages. Токен картинки приходит в ответе загрузки, видео и аудио — сразу в
    /uploads, у файла — в ответе загрузки; берём тот, что есть."""
    base = get_settings().max_api_base.rstrip("/")
    name = filename or DEFAULT_UPLOAD_NAMES.get(kind, "file.bin")
    async with httpx.AsyncClient(timeout=120.0, verify=max_ssl_context()) as client:
        slot = await client.post(f"{base}/uploads", params={"type": kind}, headers=_headers())
        slot.raise_for_status()
        info = slot.json()
        done = await client.post(str(info["url"]), files={"data": (name, body)})
        done.raise_for_status()
    token = info.get("token")
    try:
        uploaded = done.json() or {}
    except ValueError:
        uploaded = {}
    if kind == "image":
        photos = uploaded.get("photos") or {}
        token = next(iter(photos.values()))["token"] if photos else token
    elif not token:
        token = uploaded.get("token")
    if not token:
        raise RuntimeError("max_upload_without_token")
    return {"type": kind, "payload": {"token": token}}


async def get_chat(chat_id: int | str) -> dict[str, Any]:
    base = get_settings().max_api_base.rstrip("/")
    async with httpx.AsyncClient(timeout=20.0, verify=max_ssl_context()) as client:
        resp = await client.get(f"{base}/chats/{chat_id}", headers=_headers())
    return resp.json() if resp.status_code < 400 else {}


async def subscribe_webhook(url: str, *, secret: str | None, update_types: list[str] | None = None) -> dict[str, Any]:
    """POST /subscriptions — регистрирует webhook (делается один раз при выкладке)."""
    base = get_settings().max_api_base.rstrip("/")
    body: dict[str, Any] = {"url": url, "update_types": update_types or UPDATE_TYPES}
    if secret:
        body["secret"] = secret
    async with httpx.AsyncClient(timeout=20.0, verify=max_ssl_context()) as client:
        resp = await client.post(f"{base}/subscriptions", headers=_headers(), json=body)
    return {"ok": resp.status_code < 400, "status": resp.status_code, "body": resp.text[:500]}


async def get_me() -> dict[str, Any]:
    base = get_settings().max_api_base.rstrip("/")
    async with httpx.AsyncClient(timeout=20.0, verify=max_ssl_context()) as client:
        resp = await client.get(f"{base}/me", headers=_headers())
    return {"ok": resp.status_code < 400, "status": resp.status_code, "body": resp.json() if resp.status_code < 400 else resp.text[:300]}


async def get_max_video_url(token: str) -> str | None:
    """GET /videos/{token}: во входящем видео Max ссылки нет — только токен; берём лучшее качество."""
    base = get_settings().max_api_base.rstrip("/")
    async with httpx.AsyncClient(timeout=20.0, verify=max_ssl_context()) as client:
        resp = await client.get(f"{base}/videos/{token}", headers=_headers())
    if resp.status_code >= 400:
        return None
    urls = (resp.json() or {}).get("urls") or {}
    for key in ("mp4_1080", "mp4_720", "mp4_480", "mp4_360", "mp4_240", "mp4_144"):
        if urls.get(key):
            return str(urls[key])
    return next((str(v) for v in urls.values() if v), None)


async def remove_max_member(chat_id: int | str, user_id: int | str) -> dict[str, Any]:
    """DELETE /chats/{chat_id}/members?user_id=… — убрать участника из группы (бот — админ)."""
    base = get_settings().max_api_base.rstrip("/")
    async with httpx.AsyncClient(timeout=20.0, verify=max_ssl_context()) as client:
        resp = await client.delete(f"{base}/chats/{chat_id}/members", params={"user_id": str(user_id)}, headers=_headers())
    if resp.status_code >= 400:
        logger.warning("max_remove_member_failed", extra={"status": resp.status_code, "body": resp.text[:300]})
        return {"ok": False, "status": resp.status_code}
    return {"ok": True}


# --- Кнопки (inline-клавиатура), ответы на нажатия, вложения по токену (M2, 30.10.2026) ---------
# Лимиты и поля — https://dev.max.ru/docs-api/methods/POST/messages (attachment inline_keyboard)
# и https://dev.max.ru/docs-api/use-cases/sending-messages/keyboard.
MAX_BUTTON_TYPES = frozenset({"callback", "link", "request_geo_location", "request_contact", "open_app", "message", "clipboard"})
# В ряду с такой кнопкой — не больше 3 кнопок (в остальных рядах — 7).
_MAX_ROW_LIMITED_TYPES = frozenset({"link", "open_app", "request_geo_location", "request_contact"})
MAX_KEYBOARD_ROWS = 30
MAX_KEYBOARD_ROW_BUTTONS = 7
MAX_KEYBOARD_ROW_BUTTONS_LIMITED = 3
MAX_BUTTON_TEXT = 128
MAX_BUTTON_PAYLOAD = 1024
MAX_BUTTON_URL = 2048
MEDIA_ATTACHMENT_KINDS = frozenset({"image", "video", "audio", "file"})


class MaxKeyboardError(ValueError):
    """Клавиатура или кнопка не проходит лимиты Max — собираем сообщение до обращения к сети."""


def max_button_callback(text: str, payload: str) -> dict[str, Any]:
    """Кнопка-нажатие: Max пришлёт message_callback с этим payload (до 1024 символов)."""
    return _checked_button({"type": "callback", "text": text, "payload": payload})


def max_button_link(text: str, url: str) -> dict[str, Any]:
    """Кнопка-ссылка (url до 2048 символов)."""
    return _checked_button({"type": "link", "text": text, "url": url})


def max_button_request_contact(text: str) -> dict[str, Any]:
    """Кнопка «Поделиться контактом»: человек присылает свой номер вложением contact (см. update_parser)."""
    return _checked_button({"type": "request_contact", "text": text})


def _checked_button(button: Any) -> dict[str, Any]:
    if not isinstance(button, dict):
        raise MaxKeyboardError("max_button_not_object")
    kind = button.get("type")
    if kind not in MAX_BUTTON_TYPES:
        raise MaxKeyboardError(f"max_button_type_unknown:{kind}")
    text = button.get("text")
    if not isinstance(text, str) or not 1 <= len(text) <= MAX_BUTTON_TEXT:
        raise MaxKeyboardError(f"max_button_text_length:1..{MAX_BUTTON_TEXT}")
    if kind in {"callback", "clipboard"}:
        payload = button.get("payload")
        if not isinstance(payload, str) or not 1 <= len(payload) <= MAX_BUTTON_PAYLOAD:
            raise MaxKeyboardError(f"max_button_payload_length:1..{MAX_BUTTON_PAYLOAD}")
    if kind == "link":
        url = button.get("url")
        if not isinstance(url, str) or not 1 <= len(url) <= MAX_BUTTON_URL:
            raise MaxKeyboardError(f"max_button_url_length:1..{MAX_BUTTON_URL}")
    return dict(button)


def max_inline_keyboard(rows: list[list[dict[str, Any]]]) -> dict[str, Any]:
    """Вложение inline_keyboard из рядов кнопок (двумерный список). Лимиты Max: до 30 рядов (то есть до 210 кнопок),
    до 7 кнопок в ряду, а если в ряду есть link / open_app / request_geo_location / request_contact — до 3."""
    if not isinstance(rows, (list, tuple)) or not rows:
        raise MaxKeyboardError("max_keyboard_empty")
    if len(rows) > MAX_KEYBOARD_ROWS:
        raise MaxKeyboardError(f"max_keyboard_rows:{len(rows)}>{MAX_KEYBOARD_ROWS}")
    checked: list[list[dict[str, Any]]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or not row:
            raise MaxKeyboardError("max_keyboard_row_must_be_non_empty_list")
        buttons = [_checked_button(button) for button in row]
        limit = MAX_KEYBOARD_ROW_BUTTONS_LIMITED if any(b["type"] in _MAX_ROW_LIMITED_TYPES for b in buttons) else MAX_KEYBOARD_ROW_BUTTONS
        if len(buttons) > limit:
            raise MaxKeyboardError(f"max_keyboard_row_buttons:{len(buttons)}>{limit}")
        checked.append(buttons)
    return {"type": "inline_keyboard", "payload": {"buttons": checked}}


def build_max_media_attachment(kind: str, token: str) -> dict[str, Any]:
    """Вложение по токену, который вернула загрузка (upload_max_media отдаёт то же самое готовым словарём)."""
    if kind not in MEDIA_ATTACHMENT_KINDS:
        raise ValueError(f"max_attachment_kind_unknown:{kind}")
    token = str(token or "").strip()
    if not token:
        raise ValueError("max_attachment_token_required")
    return {"type": kind, "payload": {"token": token}}


def max_image_url_attachment(url: str) -> dict[str, Any]:
    """Картинка по внешнему адресу: Max сам скачает её (поле ``payload.url`` вложения ``image``, документация
    https://dev.max.ru/docs-api/methods/POST/messages; поля url, token и photos взаимоисключающие). Только https, до 2048 знаков."""
    value = str(url or "").strip()
    if not value.lower().startswith("https://") or len(value) > 2048 or any(ch.isspace() for ch in value):
        raise ValueError("max_image_url_invalid")
    return {"type": "image", "payload": {"url": value}}


async def send_max_attachment(
    *,
    user_id: int | str | None = None,
    chat_id: int | str | None = None,
    attachment: dict[str, Any] | None = None,
    kind: str | None = None,
    token: str | None = None,
    text: str = "",
    buttons: list[list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Отправить видео / аудио / файл / картинку по готовому токену, без повторной загрузки: либо attachment
    (словарь из upload_max_media), либо kind + token. Текст и кнопки — необязательные. Повтор при
    attachment.not.ready делает send_max_message."""
    if attachment is None:
        attachment = build_max_media_attachment(str(kind or ""), str(token or ""))
    return await send_max_message(chat_id=chat_id, user_id=user_id, text=text, attachments=[attachment], buttons=buttons)


async def answer_max_callback(
    callback_id: str,
    *,
    notification: str | None = None,
    text: str | None = None,
    buttons: list[list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """POST /answers?callback_id=… — ответ на нажатие кнопки (https://dev.max.ru/docs-api/methods/POST/answers).
    Тело по документации — только message (NewMessageBody): text заменяет текст сообщения с кнопкой,
    buttons — его клавиатуру (без buttons вложения остаются как были). notification — одноразовое
    уведомление человеку; в схеме тела оно сейчас не описано (в описании метода упомянуто), поэтому
    уходит только если передано явно. Без аргументов — пустое подтверждение. Лимит Max — 2 ответа в секунду в один диалог."""
    callback_id = str(callback_id or "").strip()
    if not callback_id:
        raise ValueError("callback_id required")
    body: dict[str, Any] = {}
    message: dict[str, Any] = {}
    if text is not None:
        message["text"] = str(text)[:4000]
    if buttons:
        message["attachments"] = [max_inline_keyboard(buttons)]
    if message:
        body["message"] = message
    if notification:
        body["notification"] = str(notification)
    base = get_settings().max_api_base.rstrip("/")
    async with httpx.AsyncClient(timeout=20.0, verify=max_ssl_context()) as client:
        resp = await client.post(f"{base}/answers", params={"callback_id": callback_id}, headers=_headers(), json=body)
    try:
        result = resp.json() if resp.content else {}
    except ValueError:
        result = {}
    if resp.status_code >= 400 or (isinstance(result, dict) and result.get("success") is False):
        logger.warning("max_answer_failed", extra={"status": resp.status_code, "body": resp.text[:300]})
        return {"ok": False, "status": resp.status_code}
    return {"ok": True, "result": result}
