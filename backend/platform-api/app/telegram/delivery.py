from __future__ import annotations

import contextlib
import contextvars
import html
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterator

import httpx

from app.telegram.api_base import TelegramApiBaseError, telegram_bot_api_url
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


def _bot_api_url(bot_token: str, method: str) -> str:
    try:
        return telegram_bot_api_url(bot_token, method)
    except TelegramApiBaseError as exc:
        raise TelegramDeliveryError(str(exc)) from exc


_ALLOWED_HTML_TAGS = ("b", "strong", "i", "em", "code")
# A balanced link to a person (tg://user?id=…, the support «site» forum) or to a
# page; any other href stays escaped text.
_ALLOWED_LINK_RE = re.compile(
    r'&lt;a href="(tg://user\?id=\d+|https?://[^"\s&<>]+)"&gt;(.*?)&lt;/a&gt;', re.IGNORECASE | re.DOTALL
)


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
    return _ALLOWED_LINK_RE.sub(r'<a href="\1">\2</a>', escaped)


async def send_telegram_text(
    *,
    chat_id: str,
    text: str,
    bot_token: str,
    timeout_sec: float = 10.0,
    reply_markup: dict | None = None,
    message_thread_id: int | None = None,
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
    url = _bot_api_url(bot_token, "sendMessage")
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": format_telegram_html(text)[:4096],
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if message_thread_id is not None:
        payload["message_thread_id"] = int(message_thread_id)
    # The bot uses Telegram's compact command menu. Remove any legacy reply
    # keyboard whenever a plain response does not need its own inline controls.
    payload["reply_markup"] = (
        reply_markup
        if isinstance(reply_markup, dict) and reply_markup
        else {"remove_keyboard": True}
    )
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
    url = _bot_api_url(bot_token, "sendPhoto")
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


async def copy_telegram_message(
    *,
    chat_id: str,
    from_chat_id: str,
    message_id: int,
    bot_token: str,
    timeout_sec: float = 10.0,
    message_thread_id: int | None = None,
) -> dict[str, Any]:
    """Copy a proof message to the administrator without exposing a download URL."""
    url = f"https://api.telegram.org/bot{bot_token}/copyMessage"
    payload: dict[str, Any] = {"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id}
    if message_thread_id is not None:
        payload["message_thread_id"] = int(message_thread_id)
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        response = await client.post(url, json=payload)
    data = response.json() if response.text else {}
    if response.status_code >= 400 or not data.get("ok"):
        logger.warning(
            "telegram_copy_message_failed",
            extra={"status": response.status_code, "chat_id": chat_ref(chat_id)},
        )
        return {"ok": False, "status_code": response.status_code, "detail": data}
    return {"ok": True, "message_id": (data.get("result") or {}).get("message_id")}


async def _call_telegram(method: str, payload: dict[str, Any], *, bot_token: str, timeout_sec: float = 10.0) -> dict[str, Any]:
    """One Bot API call; failures are logged by method name only (no token, no chat ids)."""
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        response = await client.post(f"https://api.telegram.org/bot{bot_token}/{method}", json=payload)
    data = response.json() if response.text else {}
    if response.status_code >= 400 or not data.get("ok"):
        logger.warning(
            "telegram_call_failed",
            extra={"method": method, "status": response.status_code, "description": str(data.get("description") or "")[:200]},
        )
        return {"ok": False, "status_code": response.status_code, "detail": data, "description": data.get("description")}
    return {"ok": True, "result": data.get("result")}


async def create_forum_topic(*, chat_id: str, name: str, bot_token: str) -> dict[str, Any]:
    """A topic per support ticket; the bot must be an administrator with «Manage topics»."""
    result = await _call_telegram("createForumTopic", {"chat_id": chat_id, "name": name[:128]}, bot_token=bot_token)
    if not result.get("ok"):
        return result
    return {"ok": True, "message_thread_id": (result.get("result") or {}).get("message_thread_id")}


async def edit_forum_topic(*, chat_id: str, message_thread_id: int, name: str, bot_token: str) -> dict[str, Any]:
    return await _call_telegram(
        "editForumTopic", {"chat_id": chat_id, "message_thread_id": int(message_thread_id), "name": name[:128]}, bot_token=bot_token
    )


async def close_forum_topic(*, chat_id: str, message_thread_id: int, bot_token: str) -> dict[str, Any]:
    return await _call_telegram(
        "closeForumTopic", {"chat_id": chat_id, "message_thread_id": int(message_thread_id)}, bot_token=bot_token
    )


async def set_message_reaction(*, chat_id: str, message_id: int, emoji: str, bot_token: str) -> dict[str, Any]:
    """A quiet delivery receipt for the administrator inside a topic."""
    return await _call_telegram(
        "setMessageReaction",
        {"chat_id": chat_id, "message_id": int(message_id), "reaction": [{"type": "emoji", "emoji": emoji}]},
        bot_token=bot_token,
    )


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
    url = _bot_api_url(bot_token, "answerCallbackQuery")
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


async def configure_telegram_command_menu(
    *,
    bot_token: str,
    commands: list[dict[str, str]],
    timeout_sec: float = 10.0,
) -> dict[str, Any]:
    """Install Telegram's compact command menu without exposing the bot token."""
    requests = (
        ("setMyCommands", {"commands": commands}),
        ("setChatMenuButton", {"menu_button": {"type": "commands"}}),
    )
    async with httpx.AsyncClient(timeout=timeout_sec) as client:
        for method, payload in requests:
            response = await client.post(
                f"https://api.telegram.org/bot{bot_token}/{method}",
                json=payload,
            )
            data = response.json() if response.text else {}
            if response.status_code >= 400 or not data.get("ok"):
                logger.warning(
                    "telegram_menu_configuration_failed",
                    extra={"method": method, "status": response.status_code},
                )
                return {
                    "ok": False,
                    "method": method,
                    "status_code": response.status_code,
                }
    return {"ok": True, "command_count": len(commands)}


def extract_photo_url(media: Any) -> str | None:
    """Read a raw photo_url from media. Delivery does not publish this value."""
    if not isinstance(media, dict):
        return None
    photo = media.get("photo_url")
    if photo and str(photo).strip():
        return str(photo).strip()
    return None


# Tenants whose cards still carry absolute photo URLs and links inside the
# answer text (primary_image_url, video and certificate URLs). Their delivery
# stays byte-for-byte as before the tenant media plane; every other tenant
# publishes only package media from our host and no foreign links.
LEGACY_MEDIA_TENANTS: frozenset[str] = frozenset({"whieda"})


def _legacy_photo_url(media: Any) -> str | None:
    photo = extract_photo_url(media)
    if not photo or "://" not in photo:
        return None
    return photo


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
    if tenant_id in LEGACY_MEDIA_TENANTS:
        photo_url = photo_url or _legacy_photo_url(core_response.get("media"))
        text = raw_text.strip()
    else:
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
