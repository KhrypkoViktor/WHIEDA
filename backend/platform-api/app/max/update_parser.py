"""Разбор обновлений Max Bot API: bot_started (deep link ?start=payload) и message_created."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

START_PREFIXES = ("/start", "start")


@dataclass
class MaxEvent:
    kind: str  # "start" | "message"
    user_id: int
    chat_id: int | None
    text: str  # для start — payload (ref_… / site_…), для message — текст
    username: str | None
    display_name: str
    raw: dict[str, Any]


def _user_name(user: dict[str, Any]) -> tuple[str | None, str]:
    username = str(user.get("username") or "").strip() or None
    if username:
        return username, f"@{username}"[:240]
    full = " ".join(p.strip() for p in (str(user.get("first_name") or ""), str(user.get("last_name") or "")) if p.strip())
    name = full or str(user.get("name") or "").strip() or f"max:{user.get('user_id')}"
    return None, name[:240]


def parse_max_update(update: dict[str, Any]) -> MaxEvent | None:
    kind = str((update or {}).get("update_type") or "")
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
        if str(recipient.get("chat_type") or "dialog") != "dialog":
            return None  # группы не обслуживаем
        text = str((message.get("body") or {}).get("text") or "").strip()
        username, display = _user_name(sender)
        event = MaxEvent(
            kind="message", user_id=int(sender["user_id"]), chat_id=_int_or_none(recipient.get("chat_id")),
            text=text, username=username, display_name=display, raw=update,
        )
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
