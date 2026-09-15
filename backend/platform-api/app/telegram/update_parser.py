"""Parse Telegram updates: /start token, onboarding, advisor routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

START_TOKEN_RE = re.compile(r"^/start(?:@\w+)?(?:\s+(.+))?$", re.I)


@dataclass
class TelegramMessage:
    chat_id: int
    user_id: int
    message_id: int
    text: str
    chat_type: str
    file_id: str | None
    raw: dict[str, Any]
    username: str | None = None
    # Forum groups: the topic the message was posted in (None outside topics).
    thread_id: int | None = None
    is_forum: bool = False
    from_bot: bool = False


@dataclass
class TelegramCallbackQuery:
    chat_id: int
    user_id: int
    callback_query_id: str
    data: str
    chat_type: str
    raw: dict[str, Any]
    thread_id: int | None = None


def parse_telegram_callback(update: dict[str, Any]) -> TelegramCallbackQuery | None:
    callback = (update or {}).get("callback_query") or {}
    data = str(callback.get("data") or "").strip()
    callback_id = str(callback.get("id") or "").strip()
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    user = callback.get("from") or {}
    chat_id = chat.get("id")
    user_id = user.get("id")
    if not data or not callback_id or chat_id is None or user_id is None:
        return None
    return TelegramCallbackQuery(
        chat_id=int(chat_id),
        user_id=int(user_id),
        callback_query_id=callback_id,
        data=data,
        chat_type=str(chat.get("type") or "private"),
        raw=update,
        thread_id=_thread_id(message),
    )


def _thread_id(message: dict[str, Any]) -> int | None:
    """Topic id of a forum message; Telegram sets it only for topic messages."""
    if not message.get("is_topic_message"):
        return None
    value = message.get("message_thread_id")
    return int(value) if value is not None else None


def parse_telegram_message(update: dict[str, Any]) -> TelegramMessage | None:
    message = (update or {}).get("message") or {}
    text = str(message.get("text") or message.get("caption") or "").strip()
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = user.get("id")
    message_id = message.get("message_id")
    photos = message.get("photo") or []
    document = message.get("document") or {}
    file_id = ""
    if isinstance(photos, list) and photos:
        file_id = str((photos[-1] or {}).get("file_id") or "").strip()
    if not file_id and isinstance(document, dict):
        file_id = str(document.get("file_id") or "").strip()
    if (not text and not file_id) or chat_id is None or user_id is None:
        return None
    username = str(user.get("username") or "").strip() or None
    return TelegramMessage(
        chat_id=int(chat_id),
        user_id=int(user_id),
        message_id=int(message_id or 0),
        text=text,
        chat_type=str(chat.get("type") or "private"),
        file_id=file_id or None,
        raw=update,
        username=username,
        thread_id=_thread_id(message),
        is_forum=bool(chat.get("is_forum")),
        from_bot=bool(user.get("is_bot")),
    )


def should_process_telegram_message(msg: TelegramMessage, bot_username: str | None) -> bool:
    """Keep group chats quiet unless the user explicitly addresses this bot."""
    if msg.chat_type == "private":
        return True
    if msg.chat_type not in {"group", "supergroup"}:
        return False

    username = (bot_username or "").lstrip("@").strip().lower()
    if not username:
        return False

    if re.search(rf"(?<!\w)@{re.escape(username)}\b", msg.text, re.I):
        return True

    reply_from = ((msg.raw.get("message") or {}).get("reply_to_message") or {}).get("from") or {}
    return str(reply_from.get("username") or "").lstrip("@").lower() == username


def is_start_command(text: str) -> bool:
    return bool(START_TOKEN_RE.match(str(text or "").strip()))


def parse_start_token(text: str) -> str | None:
    match = START_TOKEN_RE.match(text.strip())
    if not match:
        return None
    token = (match.group(1) or "").strip()
    return token or None
