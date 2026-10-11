"""Пост в официальный канал через одобрение владельца (11.10.2026).

Владелец: «я пишу тебе (агенту), какой пост сделать, ты шлёшь его в боте мне со
смайликами, я нажимаю апрув — и пост в каналы». Агент отправляет владельцу в личку
бота черновик (n8n/current/channel_draft_incontainer.py) с кнопками «Опубликовать» /
«Отменить». «Опубликовать» — бот копирует ровно этот черновик (текст, разметка,
фото) в Telegram-канал и сразу повторяет в Max (crosspost_channel_post): свои
сообщения бот обновлением channel_post не получает, поэтому Max — отсюда.

Двойное нажатие не публикует дважды: черновик один раз записывается в
channel_crossposts (источник — личка владельца, статус skipped) как ключ.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.db import fetch_one, tenant_connection
from app.settings import get_settings

logger = logging.getLogger(__name__)

APPROVE = "chpost:ok"
CANCEL = "chpost:no"
CHANNEL_USERNAME = "Whieda_world_club"
_COPY_KEYS = ("text", "entities", "caption", "caption_entities", "photo", "video", "document", "animation", "media_group_id")


def draft_markup() -> dict[str, Any]:
    return {"inline_keyboard": [[
        {"text": "✅ Опубликовать", "callback_data": APPROVE},
        {"text": "Отменить", "callback_data": CANCEL},
    ]]}


async def _tg(method: str, bot_token: str, payload: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"https://api.telegram.org/bot{bot_token}/{method}", json=payload)
    body = response.json() if response.text else {}
    if response.status_code >= 400 or not body.get("ok"):
        logger.warning("channel_post_telegram_failed", extra={"method": method, "description": str(body.get("description") or "")[:200]})
        return {"ok": False, "description": body.get("description")}
    return {"ok": True, "result": body.get("result")}


async def send_draft(*, text_html: str, bot_token: str, photo_url: str | None = None) -> dict[str, Any]:
    """Черновик владельцу в личку бота: HTML-текст (b, i, u, a) и, если есть, фото."""
    owner = get_settings().platform_billing_owner_telegram_id
    if not owner:
        return {"ok": False, "description": "owner_not_configured"}
    if photo_url:
        return await _tg("sendPhoto", bot_token, {
            "chat_id": int(owner), "photo": photo_url, "caption": text_html, "parse_mode": "HTML", "reply_markup": draft_markup(),
        })
    return await _tg("sendMessage", bot_token, {
        "chat_id": int(owner), "text": text_html, "parse_mode": "HTML", "disable_web_page_preview": False, "reply_markup": draft_markup(),
    })


async def _claim_draft(tenant_id: str, draft: dict[str, Any]) -> bool:
    """Ключ «этот черновик уже обработан»: строка channel_crossposts от лички владельца."""
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            insert into channel_crossposts (tenant_id, source_chat_id, source_message_id, payload, status)
            values (%s, %s, %s, %s::jsonb, 'skipped')
            on conflict (tenant_id, source_chat_id, source_message_id) do nothing
            returning crosspost_id
            """,
            (tenant_id, int((draft.get("chat") or {}).get("id") or 0), int(draft.get("message_id") or 0),
             json.dumps({"draft": True}, ensure_ascii=False)),
        )
    return row is not None


def channel_post_from_draft(draft: dict[str, Any], message_id: int, channel_id: int) -> dict[str, Any]:
    """Пост канала, каким его увидел бы channel_post: содержимое черновика, id и чат — канала."""
    post = {key: draft[key] for key in _COPY_KEYS if key in draft}
    post.update({"message_id": int(message_id), "chat": {"id": int(channel_id), "type": "channel", "username": CHANNEL_USERNAME}})
    return post


async def handle_draft_callback(callback: dict[str, Any], *, tenant_id: str, bot_token: str) -> dict[str, Any]:
    from app.max.crosspost import crosspost_channel_post

    settings = get_settings()
    owner = settings.platform_billing_owner_telegram_id
    draft = callback.get("message") or {}
    chat_id = (draft.get("chat") or {}).get("id")
    if not owner or int((callback.get("from") or {}).get("id") or 0) != int(owner):
        await _tg("answerCallbackQuery", bot_token, {"callback_query_id": callback.get("id"), "text": "Только для владельца"})
        return {"ok": False, "status": "not_owner"}
    if not draft.get("message_id") or not await _claim_draft(tenant_id, draft):
        await _tg("answerCallbackQuery", bot_token, {"callback_query_id": callback.get("id"), "text": "Этот черновик уже обработан"})
        return {"ok": True, "status": "already_done"}
    await _tg("editMessageReplyMarkup", bot_token, {"chat_id": chat_id, "message_id": draft["message_id"], "reply_markup": {"inline_keyboard": []}})
    if callback.get("data") == CANCEL:
        await _tg("answerCallbackQuery", bot_token, {"callback_query_id": callback.get("id"), "text": "Отменено"})
        await _tg("sendMessage", bot_token, {"chat_id": chat_id, "text": "Черновик отменён.", "reply_parameters": {"message_id": draft["message_id"]}})
        return {"ok": True, "status": "cancelled"}

    channel_id = int(settings.crosspost_telegram_channel_id)
    copied = await _tg("copyMessage", bot_token, {"chat_id": channel_id, "from_chat_id": chat_id, "message_id": draft["message_id"]})
    if not copied.get("ok"):
        await _tg("answerCallbackQuery", bot_token, {"callback_query_id": callback.get("id"), "text": "Не вышло опубликовать"})
        await _tg("sendMessage", bot_token, {"chat_id": chat_id, "text": f"⚠️ В Telegram-канал не опубликовано: {copied.get('description') or 'ошибка'}"})
        return {"ok": False, "status": "telegram_failed"}
    message_id = int((copied.get("result") or {}).get("message_id"))
    await _tg("answerCallbackQuery", bot_token, {"callback_query_id": callback.get("id"), "text": "Опубликовано"})
    lines = [f"✅ Опубликовано в Telegram: https://t.me/{CHANNEL_USERNAME}/{message_id}"]
    try:
        result = await crosspost_channel_post(tenant_id, channel_post_from_draft(draft, message_id, channel_id), bot_token=bot_token, wait=0)
        chats = result.get("chats") or []
        ok = sum(1 for c in chats if c.get("ok"))
        lines.append(f"Max: {ok} из {len(chats)}" if chats else f"Max: {result.get('status')}")
    except Exception:  # noqa: BLE001 — Telegram уже опубликован, о Max — строкой владельцу
        logger.exception("channel_post_max_failed")
        lines.append("⚠️ В Max не ушло — смотрите журнал")
    await _tg("sendMessage", bot_token, {"chat_id": chat_id, "text": "\n".join(lines), "disable_web_page_preview": True,
                                         "reply_parameters": {"message_id": draft["message_id"]}})
    return {"ok": True, "status": "published", "message_id": message_id}
