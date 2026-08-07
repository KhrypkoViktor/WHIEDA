"""Core Telegram update handler — local/staging ready, no live webhook change."""

from __future__ import annotations

import logging
from typing import Any

from app.advisor.service import handle_structured_query
from app.identity.service import exchange_telegram_link_token
from app.onboarding.service import handle_onboarding_text
from app.settings import get_settings
from app.telegram.delivery import deliver_structured_advisor_response, send_telegram_text
from app.telegram.modes import is_telegram_deliverable
from app.telegram.update_parser import TelegramMessage, parse_start_token, parse_telegram_message
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)


async def deliver_text(chat_id: int | str, text: str) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token or not text.strip():
        return
    await send_telegram_text(
        chat_id=str(chat_id),
        text=text.strip(),
        bot_token=settings.telegram_bot_token,
    )


async def deliver_advisor_response(chat_id: int | str, core_response: dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    await deliver_structured_advisor_response(
        chat_id,
        core_response,
        bot_token=settings.telegram_bot_token,
    )


async def handle_start_token(
    tenant: TenantContext,
    msg: TelegramMessage,
    token: str,
    trace_id: str,
) -> dict[str, Any]:
    result = await exchange_telegram_link_token(
        tenant.tenant_id,
        token,
        telegram_user_id=msg.user_id,
        telegram_chat_id=msg.chat_id,
    )
    lines = ["Связь с сайтом установлена."]
    if result.mentor_display_name:
        lines.append(f"Ваш наставник: {result.mentor_display_name}.")
    elif result.first_ref:
        lines.append("Наставник назначен по вашей персональной ссылке.")
    topic = (result.context or {}).get("topic")
    product = (result.context or {}).get("last_product_name") or (result.context or {}).get("last_product_sku")
    if topic or product:
        lines.append(f"Последняя тема: {product or topic}.")
    lines.append("Напишите вопрос по товару или «начать обучение» для 7-дневного плана.")
    await deliver_text(msg.chat_id, "\n".join(lines))
    return {"ok": True, "route": "start_token", "link_id": result.link_id, "trace_id": trace_id}


async def handle_onboarding(
    tenant: TenantContext,
    msg: TelegramMessage,
    *,
    first_ref: str | None = None,
) -> dict[str, Any] | None:
    result = await handle_onboarding_text(
        tenant.tenant_id,
        telegram_user_id=msg.user_id,
        text=msg.text,
        first_ref=first_ref,
    )
    if not result:
        return None
    answer = str(result.get("answer_text") or "").strip()
    if answer:
        await deliver_text(msg.chat_id, answer)
    return {"ok": True, "route": "onboarding", **result}


async def handle_advisor_query(
    tenant: TenantContext,
    msg: TelegramMessage,
    trace_id: str,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "session": f"telegram:{msg.chat_id}",
        "question": msg.text,
        "surface": "telegram",
        "country": "BY",
        "language": "ru",
    }
    core_response = await handle_structured_query(tenant, body, trace_id)
    mode = core_response.get("answer_mode")
    if is_telegram_deliverable(mode):
        await deliver_advisor_response(msg.chat_id, core_response)
    elif mode:
        logger.warning("telegram_mode_not_deliverable", extra={"answer_mode": mode, "trace_id": trace_id})
    return {"ok": True, "route": "advisor", "answer_mode": mode, "trace_id": trace_id}


async def process_core_telegram_update(
    tenant: TenantContext,
    update: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    """Full Core path: start token → onboarding → SQL advisor."""
    msg = parse_telegram_message(update)
    if not msg:
        return {"ok": True, "route": "ignored"}

    start_token = parse_start_token(msg.text)
    if start_token:
        return await handle_start_token(tenant, msg, start_token, trace_id)

    onboarding_result = await handle_onboarding(tenant, msg)
    if onboarding_result:
        return onboarding_result

    return await handle_advisor_query(tenant, msg, trace_id)
