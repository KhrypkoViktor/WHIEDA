"""Закрытая группа клуба «WWC Leader CLUB» (владелец, 24.09.2026).

После оплаты клуба бот присылает партнёру личную одноразовую ссылку в группу
и ссылку на официальный канал. Когда человек вступает, бот отмечает его в
группе и просит пройтись по закрепам.

Бот — админ группы, поэтому служебное сообщение «вступил» приходит ему как
обычный message (new_chat_members): менять allowed_updates вебхука не нужно.
"""

from __future__ import annotations

import logging
from typing import Any

from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import _call_telegram, send_telegram_text

logger = logging.getLogger(__name__)

CLUB_CHAT_ID = -1004338290116
CLUB_PINS_URL = "https://t.me/c/4338290116/12"
NEWS_CHANNEL_URL = "https://t.me/Whieda_world_club"
_IN_GROUP = {"member", "administrator", "creator", "restricted"}

NEWS_CHANNEL_TEXT = (
    f"Официальный канал WHIEDA World Club: {NEWS_CHANNEL_URL}\n"
    "Там свежие новости по проекту — обязательно вступайте и приглашайте партнёров."
)


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def club_greeting(member: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Текст приветствия в группе и entities для упоминания без @username."""
    tail = f", привет! Пройдись по закрепам, от начала диалога: {CLUB_PINS_URL} — начиная от этого сообщения."
    username = str(member.get("username") or "").strip()
    if username:
        return f"@{username}{tail}", []
    name = str(member.get("first_name") or "").strip() or "Друг"
    entity = {"type": "text_mention", "offset": 0, "length": _utf16_len(name), "user": {"id": int(member["id"])}}
    return f"{name}{tail}", [entity]


async def invite_to_club(chat_id: int, user_id: int | None, name: str) -> str:
    """Личная ссылка в группу клуба. Возвращает статус: invited / member / failed."""
    token = current_bot_binding().bot_token
    if user_id:
        state = await _call_telegram("getChatMember", {"chat_id": CLUB_CHAT_ID, "user_id": int(user_id)}, bot_token=token)
        if state.get("ok") and (state.get("result") or {}).get("status") in _IN_GROUP:
            await send_telegram_text(chat_id=str(chat_id), text=NEWS_CHANNEL_TEXT, bot_token=token)
            return "member"
    link = await _call_telegram(
        "createChatInviteLink",
        {"chat_id": CLUB_CHAT_ID, "member_limit": 1, "name": f"WWC {name}"[:32]},
        bot_token=token,
    )
    url = str((link.get("result") or {}).get("invite_link") or "")
    if not url:
        logger.warning("club_invite_link_failed")
        return "failed"
    await send_telegram_text(
        chat_id=str(chat_id),
        text=(
            "Добро пожаловать в клуб! Вот ваша личная ссылка в закрытую группу "
            f"«WWC Leader CLUB»:\n{url}\n\n"
            "Ссылка одноразовая — только для вас.\n\n" + NEWS_CHANNEL_TEXT
        ),
        bot_token=token,
    )
    return "invited"


async def try_handle_club_join(update: dict[str, Any], *, trace_id: str) -> dict[str, Any] | None:
    message = (update or {}).get("message") or {}
    if int((message.get("chat") or {}).get("id") or 0) != CLUB_CHAT_ID:
        return None
    members = [m for m in (message.get("new_chat_members") or []) if isinstance(m, dict) and not m.get("is_bot")]
    if not members:
        return None
    token = current_bot_binding().bot_token
    for member in members:
        text, entities = club_greeting(member)
        payload: dict[str, Any] = {"chat_id": CLUB_CHAT_ID, "text": text, "disable_web_page_preview": True}
        if entities:
            payload["entities"] = entities
        await _call_telegram("sendMessage", payload, bot_token=token)
    return {"ok": True, "route": "club_join", "greeted": len(members), "trace_id": trace_id}
