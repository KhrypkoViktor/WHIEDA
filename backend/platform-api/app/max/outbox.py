"""Отложенные сообщения в Max через platform_outbox (M3, 30.10.2026).

Строка outbox с payload.channel = "max" уходит в Max, а не в Telegram: её подбирает тот же
process_due_notifications (app/jobs/worker.py), по тем же правилам статусов и попыток. Строки без
channel — Telegram, как раньше.

payload:
  channel      "max"
  binding_id   привязка бота процесса (как у остальных отложенных строк: по ней воркер берёт строку)
  user_id      Max-пользователь (диалог) — или chat_id, если известен чат; chat_id приоритетнее
  text         текст (можно пустой, если есть attachments)
  buttons      ряды кнопок inline-клавиатуры (max_button_callback / max_button_link / …)
  attachments  вложения по токену: {"type": "video"|"audio"|"image"|"file", "token": "…"} либо готовый
               словарь {"type": …, "payload": {"token": …}} из upload_max_media
  image_url    https-адрес картинки (визитка партнёра): Max сам её скачает; Max не принял картинку (400) —
               сообщение уходит ещё раз без неё, только текст и кнопки
  format       "html" — текст с HTML-разметкой Max (необязательно)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import logging

import httpx
from psycopg import AsyncConnection

from app.jobs.outbox import enqueue_outbox_event
from app.max.client import (
    MaxKeyboardError,
    build_max_media_attachment,
    max_image_url_attachment,
    max_inline_keyboard,
    send_max_message,
)
from app.telegram.delivery import TelegramDeliveryError, TelegramDeliveryUnknown

logger = logging.getLogger(__name__)

CHANNEL_MAX = "max"
EVENT_TYPE_MAX_NOTIFICATION = "max_notification"


def payload_channel(payload: dict[str, Any]) -> str:
    """Канал строки outbox: без поля channel — telegram (как до M3)."""
    return str(payload.get("channel") or "telegram").strip().lower()


def _media_attachment(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("max_attachment_not_object")
    if isinstance(item.get("payload"), dict):
        return build_max_media_attachment(str(item.get("type") or ""), str(item["payload"].get("token") or ""))
    return build_max_media_attachment(str(item.get("type") or ""), str(item.get("token") or ""))


def build_max_outbox_payload(
    *,
    binding_id: str,
    user_id: int | str | None = None,
    chat_id: int | str | None = None,
    text: str = "",
    buttons: list[list[dict[str, Any]]] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    html_format: bool = False,
    image_url: str | None = None,
) -> dict[str, Any]:
    """Payload строки outbox для Max. Лимиты кнопок и вложений проверяются сразу, а не при отправке."""
    if not binding_id:
        raise ValueError("binding_id required")
    if not user_id and not chat_id:
        raise ValueError("user_id or chat_id required")
    media = [_media_attachment(item) for item in attachments or []]
    text = str(text or "")
    if not text.strip() and not media:
        raise ValueError("text or attachments required")
    if buttons:
        max_inline_keyboard(buttons)  # MaxKeyboardError при нарушении лимитов
    if image_url:
        max_image_url_attachment(image_url)  # ValueError, если не https-адрес
    payload: dict[str, Any] = {"channel": CHANNEL_MAX, "binding_id": binding_id, "text": text}
    if chat_id:
        payload["chat_id"] = str(chat_id)
    else:
        payload["user_id"] = str(user_id)
    if buttons:
        payload["buttons"] = buttons
    if media:
        payload["attachments"] = media
    if image_url:
        payload["image_url"] = str(image_url).strip()
    if html_format:
        payload["format"] = "html"
    return payload


async def enqueue_max_notification(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    idempotency_key: str,
    due_at: datetime,
    binding_id: str,
    user_id: int | str | None = None,
    chat_id: int | str | None = None,
    text: str = "",
    buttons: list[list[dict[str, Any]]] | None = None,
    attachments: list[dict[str, Any]] | None = None,
    html_format: bool = False,
    image_url: str | None = None,
    event_type: str = EVENT_TYPE_MAX_NOTIFICATION,
) -> dict[str, Any] | None:
    """Поставить отложенное сообщение в Max: один раз на (tenant_id, idempotency_key). due_at обязателен —
    строка со статусом «scheduled», обычная очередь process_pending_outbox её не трогает."""
    payload = build_max_outbox_payload(
        binding_id=binding_id, user_id=user_id, chat_id=chat_id, text=text, buttons=buttons,
        attachments=attachments, html_format=html_format, image_url=image_url,
    )
    return await enqueue_outbox_event(conn, tenant_id, event_type, idempotency_key, payload, due_at=due_at)


async def send_max_outbox_payload(payload: dict[str, Any]) -> None:
    """Отправить строку outbox в Max. Ошибки — в терминах воркера (_final_delivery_error):
    пустая строка и неоднозначная доставка (таймаут, обрыв после отправки) — TelegramDeliveryUnknown, строка
    «dead» без повтора, чтобы не слать дважды; ответ Max 400/403 — окончательный отказ ("…:400"/"…:403");
    остальные коды (429, 5xx) — обычный повтор через 10 минут, до DUE_MAX_ATTEMPTS."""
    chat_id = str(payload.get("chat_id") or "").strip() or None
    user_id = str(payload.get("user_id") or "").strip() or None
    text = str(payload.get("text") or "")
    try:
        media = [_media_attachment(item) for item in payload.get("attachments") or []]
        buttons = payload.get("buttons") or None
        if buttons:
            max_inline_keyboard(buttons)
    except (ValueError, MaxKeyboardError) as exc:
        raise TelegramDeliveryUnknown("max_due_notification_invalid") from exc
    if (not chat_id and not user_id) or (not text.strip() and not media):
        raise TelegramDeliveryUnknown("max_due_notification_empty")
    image = None
    image_url = str(payload.get("image_url") or "").strip()
    if image_url:
        try:
            image = max_image_url_attachment(image_url)
        except ValueError:
            image = None  # негодный адрес: уходит без картинки

    async def _send(attachments: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            return await send_max_message(
                chat_id=chat_id, user_id=None if chat_id else user_id, text=text, attachments=attachments or None,
                buttons=buttons, html_format=str(payload.get("format") or "") == "html",
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TelegramDeliveryUnknown("max_send_ambiguous") from exc

    result = await _send([*media, image] if image else media)
    if image is not None and not result.get("ok"):
        # Max не принял картинку (недоступный адрес, не изображение): текст и кнопки важнее. Отказ — не ответ с «мид»,
        # сообщения нет, второго не будет; таймаут выше уходит в «неоднозначно» без повтора.
        logger.warning("max_image_url_rejected", extra={"status": result.get("status")})
        result = await _send(media)
    if not result.get("ok"):
        raise TelegramDeliveryError(f"max_delivery_rejected:{result.get('status') or 'unknown'}")
