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
    text: str
    raw: dict[str, Any]


def parse_telegram_message(update: dict[str, Any]) -> TelegramMessage | None:
    message = (update or {}).get("message") or {}
    text = str(message.get("text") or "").strip()
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = user.get("id")
    if not text or chat_id is None or user_id is None:
        return None
    return TelegramMessage(
        chat_id=int(chat_id),
        user_id=int(user_id),
        text=text,
        raw=update,
    )


def parse_start_token(text: str) -> str | None:
    match = START_TOKEN_RE.match(text.strip())
    if not match:
        return None
    token = (match.group(1) or "").strip()
    return token or None
