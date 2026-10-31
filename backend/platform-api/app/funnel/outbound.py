"""Постановка сообщения гостю в очередь ``platform_outbox`` для Telegram и Max (срез 5 бот-воронки, 31.10.2026).

Одна функция на оба мессенджера: Telegram — строка outbox с ``chat_id`` (для личного чата он равен id человека),
Max — ``enqueue_max_notification`` (``payload.channel = "max"``). Кнопки — ряды ``(подпись, callback_data или https-адрес)``,
как у экранов гостя (``app/funnel/guest.py``). Отправляет боевой воркер (``process_due_notifications``): строка берётся
по ``binding_id`` процесса. Ключ идемпотентности — один раз на (арендатор, ключ).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.funnel.guest import Rows, rows_to_max, rows_to_telegram
from app.jobs.outbox import enqueue_outbox_event
from app.max.outbox import enqueue_max_notification


async def enqueue_guest_message(
    conn: Any,
    *,
    tenant_id: str,
    binding_id: str,
    channel: str,
    user_id: int,
    idempotency_key: str,
    event_type: str,
    text: str,
    rows: Rows | None = None,
    photo_url: str | None = None,
    due_at: datetime,
) -> dict[str, Any] | None:
    """Поставить сообщение человеку в его мессенджер. ``photo_url`` — фото визитки: Telegram отправит фото с
    подписью и кнопками (``card_photo``), Max прикрепит картинку по адресу; фото не принято — уйдёт текст.
    Возвращает строку outbox (``created`` — поставлено впервые) или None."""
    if channel == "telegram":
        payload: dict[str, Any] = {"chat_id": str(int(user_id)), "text": text, "binding_id": binding_id}
        markup = rows_to_telegram(rows or [])
        if markup:
            payload["reply_markup"] = markup
        if photo_url:
            payload["card_photo"] = photo_url
        return await enqueue_outbox_event(
            conn,
            tenant_id=tenant_id,
            event_type=event_type,
            idempotency_key=idempotency_key,
            payload=payload,
            due_at=due_at,
        )
    if channel == "max":
        return await enqueue_max_notification(
            conn,
            tenant_id,
            idempotency_key=idempotency_key,
            due_at=due_at,
            binding_id=binding_id,
            user_id=int(user_id),
            text=text,
            buttons=rows_to_max(rows or []),
            image_url=photo_url,
            event_type=event_type,
        )
    raise ValueError("channel must be telegram or max")
