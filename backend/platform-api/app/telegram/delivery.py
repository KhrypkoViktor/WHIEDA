from __future__ import annotations

import contextlib
import contextvars
import html
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

from app.telegram.log_safe import chat_ref
from app.telegram.tenant_media import resolve_delivery_photo_url, sanitize_delivery_text

logger = logging.getLogger(__name__)

_allowed_bot_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "telegram_allowed_bot_token",
    default=None,
)


class TelegramDeliveryError(RuntimeError):
    """Raised on the durable-inbox worker path when Telegram send fails."""


class TelegramDeliveryUnknown(TelegramDeliveryError):
    """Request outcome is ambiguous; do not automatically resend."""


@contextlib.contextmanager
def outbound_binding_guard(bot_token: str) -> Iterator[None]:
    token = _allowed_bot_token.set(bot_token)
    try:
        yield
    finally:
        _allowed_bot_token.reset(token)


@dataclass(frozen=True)
class DeliveryDraft:
    kind: str
    payload: dict[str, Any]


_delivery_plan: contextvars.ContextVar[list[DeliveryDraft] | None] = contextvars.ContextVar(
    "telegram_delivery_plan",
    default=None,
)


@contextlib.contextmanager
def capture_delivery_plan() -> Iterator[list[DeliveryDraft]]:
    items: list[DeliveryDraft] = []
    token = _delivery_plan.set(items)
    try:
        yield items
    finally:
        _delivery_plan.reset(token)


def current_delivery_plan() -> list[DeliveryDraft] | None:
    return _delivery_plan.get()


def _queue_delivery(kind: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    plan = _delivery_plan.get()
    if plan is None:
        return None
    plan.append(DeliveryDraft(kind=kind, payload=dict(payload)))
    return {"ok": True, "queued": True}


def compact_delivery_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    chat_id = str(payload.get("chat_id") or "").strip()
    if kind == "photo":
        compact: dict[str, Any] = {
            "chat_id": chat_id,
            "photo_url": str(payload.get("photo_url") or "").strip()[:2048],
        }
        return compact
    compact = {"chat_id": chat_id, "text": str(payload.get("text") or "").strip()[:4096]}
    markup = payload.get("reply_markup")
    if isinstance(markup, dict) and markup:
        compact["reply_markup"] = markup
    return compact


def _assert_outbound_token(bot_token: str) -> None:
    allowed = _allowed_bot_token.get()
    if allowed is not None and allowed != bot_token:
        raise RuntimeError("foreign_bot_token_forbidden")


_ALLOWED_HTML_TAGS = ("b", "strong", "i", "em")


def format_telegram_html(text: str) -> str:
    """Preserve the small approved formatting subset and escape everything else."""
    escaped = html.escape(str(text or ""), quote=False)
    for tag in _ALLOWED_HTML_TAGS:
        escaped = re.sub(
            rf"&lt;(/?{tag})&gt;",
            r"<\1>",
            escaped,
            flags=re.IGNORECASE,
        )
    return escaped


async def send_telegram_text(
    *,
    chat_id: str,
    text: str,
    bot_token: str,
    timeout_sec: float = 10.0,
    reply_markup: dict | None = None,
) -> dict[str, Any]:
    if not text.strip():
        return {"ok": False, "skipped": True}
    queued = _queue_delivery(
        "text",
        compact_delivery_payload(
            "text",
            {"chat_id": chat_id, "text": text, "reply_markup": reply_markup},
        ),
    )
    if queued is not None:
        return queued
    _assert_outbound_token(bot_token)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": format_telegram_html(text)[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if isinstance(reply_markup, dict) and reply_markup:
        payload["reply_markup"] = reply_markup
    data, status_code = await _post_telegram(url, payload, timeout_sec)
    if status_code >= 400 or not data.get("ok"):
        logger.warning(
            "telegram_send_failed",
            extra={"status": status_code, "chat_id": chat_ref(chat_id)},
        )
        result = {"ok": False, "status_code": status_code, "detail": data}
        if _allowed_bot_token.get() is not None:
            raise TelegramDeliveryError(f"telegram_send_failed:{status_code}")
        return result
    return {"ok": True, "message_id": (data.get("result") or {}).get("message_id")}


async def send_telegram_photo(
    *,
    chat_id: str,
    photo_url: str,
    bot_token: str,
    timeout_sec: float = 15.0,
) -> dict[str, Any]:
    """Send photo without caption — text is always a separate message."""
    if not photo_url.strip():
        return {"ok": False, "skipped": True}
    queued = _queue_delivery(
        "photo",
        compact_delivery_payload("photo", {"chat_id": chat_id, "photo_url": photo_url}),
    )
    if queued is not None:
        return queued
    _assert_outbound_token(bot_token)
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "photo": photo_url.strip()[:2048],
    }
    data, status_code = await _post_telegram(url, payload, timeout_sec)
    if status_code >= 400 or not data.get("ok"):
        logger.warning(
            "telegram_photo_failed",
            extra={"status": status_code, "chat_id": chat_ref(chat_id)},
        )
        result = {"ok": False, "status_code": status_code, "detail": data}
        if _allowed_bot_token.get() is not None:
            raise TelegramDeliveryError(f"telegram_photo_failed:{status_code}")
        return result
    return {"ok": True, "message_id": (data.get("result") or {}).get("message_id")}


async def _post_telegram(
    url: str,
    payload: dict[str, Any],
    timeout_sec: float,
) -> tuple[dict[str, Any], int]:
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            response = await client.post(url, json=payload)
        data = response.json() if response.text else {}
        return data if isinstance(data, dict) else {}, response.status_code
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        raise TelegramDeliveryUnknown("telegram_send_ambiguous") from exc


async def answer_callback_query(
    *,
    callback_query_id: str,
    bot_token: str,
    text: str | None = None,
    show_alert: bool = False,
    timeout_sec: float = 10.0,
) -> dict[str, Any]:
    """Acknowledge callback_query without logging user-visible callback text."""
    if not callback_query_id or not bot_token:
        return {"ok": False, "skipped": True}
    _assert_outbound_token(bot_token)
    url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text and text.strip():
        payload["text"] = text.strip()[:200]
        payload["show_alert"] = show_alert
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        response = await client.post(url, json=payload)
    data = response.json() if response.text else {}
    if response.status_code >= 400 or not data.get("ok"):
        logger.warning(
            "telegram_callback_ack_failed",
            extra={"status": response.status_code},
        )
        return {"ok": False, "status_code": response.status_code, "detail": data}
    return {"ok": True}


def extract_photo_url(media: Any) -> str | None:
    """Read a raw photo_url from media. Delivery does not publish this value."""
    if not isinstance(media, dict):
        return None
    photo = media.get("photo_url")
    if photo and str(photo).strip():
        return str(photo).strip()
    return None


async def deliver_structured_advisor_response(
    chat_id: int | str,
    core_response: dict[str, Any],
    *,
    bot_token: str,
    tenant_id: str | None = None,
    binding_status: str = "active",
    reply_markup: dict | None = None,
) -> dict[str, Any]:
    """
    Photo-first rule: sendPhoto without caption, then sendMessage with full text.
    Photo URL is constructed from the current binding tenant only.
    If sendPhoto fails, still send text (never silence the user).
    """
    if binding_status != "active" or not tenant_id:
        return {"photo_sent": False, "text_sent": False, "skipped": True}

    raw_text = str(core_response.get("answer_text") or "")
    photo_url = resolve_delivery_photo_url(core_response, tenant_id=tenant_id)
    text = sanitize_delivery_text(raw_text, allowed_url=photo_url)
    result: dict[str, Any] = {"photo_sent": False, "text_sent": False, "photo_url": photo_url}

    if photo_url:
        photo_result = await send_telegram_photo(
            chat_id=str(chat_id),
            photo_url=photo_url,
            bot_token=bot_token,
        )
        result["photo_sent"] = bool(photo_result.get("ok"))
        if not result["photo_sent"]:
            logger.warning(
                "telegram_photo_fallback_to_text", extra={"chat_id": chat_ref(chat_id)}
            )

    if text:
        text_result = await send_telegram_text(
            chat_id=str(chat_id),
            text=text,
            bot_token=bot_token,
            reply_markup=reply_markup,
        )
        result["text_sent"] = bool(text_result.get("ok"))
    elif not photo_url:
        result["skipped"] = True

    return result
