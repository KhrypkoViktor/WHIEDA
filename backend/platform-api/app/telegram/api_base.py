"""Resolve Telegram Bot API origin. Override is localhost/lab-only."""

from __future__ import annotations

from urllib.parse import urlparse

DEFAULT_TELEGRAM_API_BASE = "https://api.telegram.org"
LOCAL_TELEGRAM_HOSTS = frozenset({"127.0.0.1", "localhost", "host.docker.internal"})


class TelegramApiBaseError(RuntimeError):
    """Configured Bot API origin is not a local lab capture."""


def is_local_telegram_api_base(raw: str) -> bool:
    text = str(raw or "").strip().rstrip("/")
    if not text:
        return False
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"}:
        return False
    if parsed.username or parsed.password:
        return False
    if parsed.query or parsed.fragment:
        return False
    return host in LOCAL_TELEGRAM_HOSTS


def resolve_telegram_api_base(raw: str | None) -> str:
    text = str(raw or "").strip().rstrip("/")
    if not text:
        return DEFAULT_TELEGRAM_API_BASE
    if not is_local_telegram_api_base(text):
        raise TelegramApiBaseError("telegram_api_base_not_local")
    return text


def telegram_bot_api_url(bot_token: str, method: str, *, base_url: str | None = None) -> str:
    from app.settings import get_settings

    origin = resolve_telegram_api_base(
        base_url if base_url is not None else get_settings().telegram_api_base_url
    )
    return f"{origin}/bot{bot_token}/{method}"
