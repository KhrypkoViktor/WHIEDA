"""Keep raw Telegram chat ids and bot tokens out of application logs."""

from __future__ import annotations

import hashlib
import logging
import re

_BOT_URL_RE = re.compile(r"https?://api\.telegram\.org/bot[^/\s]+", re.IGNORECASE)
_BOT_PATH_RE = re.compile(r"/bot\d+:[A-Za-z0-9_-]+", re.IGNORECASE)


def chat_ref(chat_id: object) -> str:
    value = str(chat_id or "").strip()
    if not value:
        return "chat:none"
    if value.startswith("chat:"):
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"chat:{digest}"


def redact_telegram_secrets(text: str) -> str:
    cleaned = _BOT_URL_RE.sub("https://api.telegram.org/bot[REDACTED]", str(text or ""))
    return _BOT_PATH_RE.sub("/bot[REDACTED]", cleaned)


class TelegramSecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_telegram_secrets(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_telegram_secrets(arg) if isinstance(arg, str) else arg
                for arg in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_telegram_secrets(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        chat_id = getattr(record, "chat_id", None)
        if chat_id is not None:
            record.chat_id = chat_ref(chat_id)
        return True


def install_telegram_log_filter() -> None:
    root = logging.getLogger()
    if any(isinstance(item, TelegramSecretFilter) for item in root.filters):
        return
    root.addFilter(TelegramSecretFilter())
