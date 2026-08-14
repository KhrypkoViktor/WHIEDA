from __future__ import annotations

import html
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

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
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": format_telegram_html(text)[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if isinstance(reply_markup, dict) and reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        response = await client.post(url, json=payload)
    data = response.json() if response.text else {}
    if response.status_code >= 400 or not data.get("ok"):
        logger.warning(
            "telegram_send_failed",
            extra={"status": response.status_code, "chat_id": chat_id},
        )
        return {"ok": False, "status_code": response.status_code, "detail": data}
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
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "photo": photo_url.strip()[:2048],
    }
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        response = await client.post(url, json=payload)
    data = response.json() if response.text else {}
    if response.status_code >= 400 or not data.get("ok"):
        logger.warning(
            "telegram_photo_failed",
            extra={"status": response.status_code, "chat_id": chat_id},
        )
        return {"ok": False, "status_code": response.status_code, "detail": data}
    return {"ok": True, "message_id": (data.get("result") or {}).get("message_id")}


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
    reply_markup: dict | None = None,
) -> dict[str, Any]:
    """
    Photo-first rule: sendPhoto without caption, then sendMessage with full text.
    If sendPhoto fails, still send text (never silence the user).
    """
    text = str(core_response.get("answer_text") or "").strip()
    photo_url = extract_photo_url(core_response.get("media"))
    result: dict[str, Any] = {"photo_sent": False, "text_sent": False}

    if photo_url:
        photo_result = await send_telegram_photo(
            chat_id=str(chat_id),
            photo_url=photo_url,
            bot_token=bot_token,
        )
        result["photo_sent"] = bool(photo_result.get("ok"))
        if not result["photo_sent"]:
            logger.warning("telegram_photo_fallback_to_text", extra={"chat_id": str(chat_id)})

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
