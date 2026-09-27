"""Telegram ingress for public content_access_ deep links."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from app.content_access.service import CONTENT_START_PREFIX, confirm_content_from_telegram
from app.cookie_domain import shared_cookie_domains
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import send_telegram_text
from app.telegram.site_login import with_site_login
from app.telegram.update_parser import parse_start_token, parse_telegram_message

logger = logging.getLogger(__name__)

_SUCCESS_MESSAGE = (
    "✅ Вход подтверждён. Вернитесь на сайт — всё уже открыто.\n\n"
    "Если страница сайта закрылась, нажмите кнопку ниже: она откроет сайт уже с входом."
)
_RETURN_BUTTON = "Вернуться на сайт"

_NEUTRAL_ERRORS: dict[str, str] = {
    "challenge_expired": "Ссылка истекла. Создайте новую на странице материала.",
    "challenge_already_used": "Эта ссылка уже использована. Создайте новую на странице материала.",
    "challenge_not_found": "Ссылка недействительна. Создайте новую на странице материала.",
    "invalid_challenge_token": "Ссылка недействительна. Создайте новую на странице материала.",
    "challenge_not_pending": "Ссылка недействительна. Создайте новую на странице материала.",
}

_DEFAULT_ERROR_MESSAGE = "Не удалось подтвердить доступ. Попробуйте снова со страницы материала."


def parse_content_access_start(text: str) -> str | None:
    token = parse_start_token(text)
    if token and token.startswith(CONTENT_START_PREFIX):
        return token
    return None


def _neutral_message(exc: HTTPException) -> str:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    error = str(detail.get("error") or "")
    return _NEUTRAL_ERRORS.get(error, _DEFAULT_ERROR_MESSAGE)


async def deliver_text(chat_id: int | str, text: str, reply_markup: dict[str, Any] | None = None) -> None:
    if not text.strip():
        return
    await send_telegram_text(
        chat_id=str(chat_id),
        text=text.strip(),
        bot_token=current_bot_binding().bot_token,
        reply_markup=reply_markup,
    )


def _site_url(return_to: str | None) -> str | None:
    """Абсолютный адрес страницы, где нажали «Войти». Старый клиент присылал
    только путь — тогда главный домен семьи (первый в PLATFORM_COOKIE_SHARED_DOMAINS)."""
    value = str(return_to or "").strip()
    if value.startswith("https://"):
        return value
    if value.startswith("/") and not value.startswith("//"):
        domains = shared_cookie_domains()
        return f"https://{domains[0]}{value}" if domains else None
    return None


async def return_link(tenant_id: str, return_to: str | None, telegram_user_id: int) -> str | None:
    """Кнопка «Вернуться на сайт» после подтверждения входа (27.09.2026).

    Во встроенном браузере Telegram вкладка с сайтом закрывается, когда человек
    уходит в бота; на iPhone страница перезагружается. Опрос заявки в браузере
    гибнет, и подтверждённый вход пропадал (за неделю до правки — каждый пятый).
    Ссылка несёт заранее одобренный вход (#wwc-login=…, как ссылки бота на
    ежедневник), поэтому сайт открывается уже вошедшим, откуда бы её ни нажали.
    """
    url = _site_url(return_to)
    if not url:
        return None
    try:
        return await with_site_login(url, tenant_id=tenant_id, telegram_user_id=telegram_user_id)
    except Exception:  # noqa: BLE001 — ссылка без входа лучше, чем без кнопки
        logger.warning("content_access_return_link_failed", exc_info=True)
        return url


def _return_markup(link: str | None) -> dict[str, Any] | None:
    if not link:
        return None
    return {"inline_keyboard": [[{"text": _RETURN_BUTTON, "url": link}]]}


def _update_id(update: dict[str, Any]) -> int | None:
    raw = (update or {}).get("update_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def try_handle_content_access(
    update: dict[str, Any],
    *,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    """Handle `/start content_access_*`; return route metadata or None."""
    msg = parse_telegram_message(update)
    if not msg:
        return None

    challenge_token = parse_content_access_start(msg.text)
    if not challenge_token:
        return None

    update_id = _update_id(update)
    base: dict[str, Any] = {
        "route": "content_access",
        "update_id": update_id,
        "trace_id": trace_id,
    }
    binding = current_bot_binding()

    try:
        result = await confirm_content_from_telegram(
            tenant_id=binding.tenant.tenant_id,
            challenge_token=challenge_token,
            telegram_user_id=msg.user_id,
            telegram_chat_id=msg.chat_id,
        )
        link = await return_link(binding.tenant.tenant_id, result.get("return_to"), msg.user_id)
        await deliver_text(msg.chat_id, _SUCCESS_MESSAGE, _return_markup(link))
        return {
            **base,
            "ok": True,
            "status": result.get("status", "approved"),
            "challenge_id": result.get("challenge_id"),
        }
    except HTTPException as exc:
        await deliver_text(msg.chat_id, _neutral_message(exc))
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        logger.info(
            "content_access_telegram_declined",
            extra={
                "trace_id": trace_id,
                "update_id": update_id,
                "error": detail.get("error"),
                "status_code": exc.status_code,
            },
        )
        return {
            **base,
            "ok": False,
            "status": detail.get("error") or "declined",
        }
