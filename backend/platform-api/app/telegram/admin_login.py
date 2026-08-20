"""Telegram ingress for WHIEDA admin_login_ deep links. Non-WHIEDA bindings are refused."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import send_telegram_text
from app.telegram.update_parser import parse_start_token, parse_telegram_message

logger = logging.getLogger(__name__)

ADMIN_START_PREFIX = "admin_login_"
_SUCCESS_MESSAGE = "Вход в кабинет подтверждён. Вернитесь в браузер."

_NEUTRAL_ERRORS: dict[str, str] = {
    "admin_not_allowed": "Не удалось подтвердить вход. Проверьте, что открыли ссылку из кабинета.",
    "challenge_expired": "Ссылка для входа истекла. Запросите новую в кабинете.",
    "challenge_already_used": "Эта ссылка уже использована. Запросите новую в кабинете.",
    "challenge_not_found": "Ссылка для входа недействительна. Запросите новую в кабинете.",
    "invalid_challenge_token": "Ссылка для входа недействительна. Запросите новую в кабинете.",
    "challenge_not_pending": "Ссылка для входа недействительна. Запросите новую в кабинете.",
}

_DEFAULT_ERROR_MESSAGE = "Не удалось подтвердить вход. Попробуйте снова из кабинета."


def parse_admin_login_start(text: str) -> str | None:
    token = parse_start_token(text)
    if token and token.startswith(ADMIN_START_PREFIX):
        return token
    return None


def _neutral_message(exc: HTTPException) -> str:
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    error = str(detail.get("error") or "")
    return _NEUTRAL_ERRORS.get(error, _DEFAULT_ERROR_MESSAGE)


async def confirm_login_from_telegram(
    *,
    challenge_token: str,
    telegram_user_id: int,
) -> dict[str, Any]:
    from app.admin.auth.service import confirm_login_from_telegram as confirm

    return await confirm(
        challenge_token=challenge_token,
        telegram_user_id=telegram_user_id,
    )


async def deliver_text(chat_id: int | str, text: str) -> None:
    if not text.strip():
        return
    await send_telegram_text(
        chat_id=str(chat_id),
        text=text.strip(),
        bot_token=current_bot_binding().bot_token,
    )


def _update_id(update: dict[str, Any]) -> int | None:
    raw = (update or {}).get("update_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def try_handle_admin_login(
    update: dict[str, Any],
    *,
    trace_id: str | None = None,
) -> dict[str, Any] | None:
    """Handle `/start admin_login_*`; return route metadata or None if not admin login."""
    msg = parse_telegram_message(update)
    if not msg:
        return None

    challenge_token = parse_admin_login_start(msg.text)
    if not challenge_token:
        return None

    update_id = _update_id(update)
    base: dict[str, Any] = {
        "route": "admin_login",
        "update_id": update_id,
        "trace_id": trace_id,
    }
    binding = current_bot_binding()
    if binding.tenant.tenant_id != "whieda":
        logger.warning(
            "admin_login_wrong_tenant_binding",
            extra={
                "trace_id": trace_id,
                "update_id": update_id,
                "binding_id": binding.binding_id,
            },
        )
        return {**base, "ok": False, "status": "binding_not_allowed"}

    try:
        result = await confirm_login_from_telegram(
            challenge_token=challenge_token,
            telegram_user_id=msg.user_id,
        )
        await deliver_text(msg.chat_id, _SUCCESS_MESSAGE)
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
            "admin_login_telegram_declined",
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
