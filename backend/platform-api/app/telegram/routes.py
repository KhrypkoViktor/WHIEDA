from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.advisor.service import handle_structured_query
from app.settings import get_settings
from app.telegram.delivery import deliver_structured_advisor_response, send_telegram_text
from app.telegram.modes import is_telegram_deliverable
from app.telegram.processor import process_core_telegram_update
from app.tenancy import get_trace_id, resolve_tenant_from_bot_binding

LEGACY_TELEGRAM_WEBHOOK = "/webhook/advisor-whieda-v0"

router = APIRouter(tags=["telegram"])
logger = logging.getLogger(__name__)


def _verify_telegram_secret(request: Request) -> None:
    settings = get_settings()
    secret = request.headers.get("x-telegram-bot-api-secret-token", "")
    expected = settings.telegram_webhook_secret
    if expected and secret != expected:
        raise HTTPException(status_code=403, detail={"error": "invalid_webhook_secret"})


async def _deliver_core_answer(chat_id: str, core_response: dict) -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return
    await deliver_structured_advisor_response(
        chat_id=str(chat_id),
        core_response=core_response,
        bot_token=settings.telegram_bot_token,
    )


async def _forward_to_legacy_consultant(
    update: dict,
    trace_id: str | None = None,
) -> None:
    settings = get_settings()
    url = f"{settings.legacy_n8n_base_url.rstrip('/')}{LEGACY_TELEGRAM_WEBHOOK}"
    headers = {"content-type": "application/json"}
    if trace_id:
        headers["x-trace-id"] = trace_id
    chat_id = ((update or {}).get("message") or {}).get("chat", {}).get("id")
    try:
        async with httpx.AsyncClient(
            timeout=settings.telegram_legacy_timeout_sec,
            follow_redirects=True,
        ) as client:
            response = await client.post(url, json=update, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(f"legacy_status_{response.status_code}")
    except Exception as exc:
        logger.warning("telegram_legacy_consultant_failed", exc_info=exc)
        if chat_id and settings.telegram_bot_token:
            await send_telegram_text(
                chat_id=str(chat_id),
                text="Сейчас высокая нагрузка на советника. Повторите вопрос через минуту.",
                bot_token=settings.telegram_bot_token,
            )


async def _process_telegram_update(
    binding_id: str,
    update: dict,
    trace_id: str | None,
) -> None:
    settings = get_settings()
    tenant = await resolve_tenant_from_bot_binding(binding_id)
    message = (update or {}).get("message") or {}
    text = str(message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not text or not chat_id:
        return

    if settings.core_route_telegram == "legacy":
        await _forward_to_legacy_consultant(update, trace_id)
        return

    if settings.core_route_telegram == "core":
        try:
            await process_core_telegram_update(tenant, update, trace_id or "")
        except Exception:
            logger.exception("telegram_core_processor_failed")
            if settings.telegram_bot_token and chat_id:
                await send_telegram_text(
                    chat_id=str(chat_id),
                    text="Не удалось обработать сообщение. Попробуйте ещё раз.",
                    bot_token=settings.telegram_bot_token,
                )
        return

    body: dict[str, Any] = {
        "session": f"telegram:{chat_id}",
        "question": text,
        "surface": "telegram",
        "country": "BY",
        "language": "ru",
    }
    try:
        core_response = await handle_structured_query(tenant, body, trace_id or "")
    except Exception:
        logger.exception("telegram_structured_query_failed")
        if settings.telegram_bot_token:
            await send_telegram_text(
                chat_id=str(chat_id),
                text="Не удалось обработать запрос. Попробуйте ещё раз.",
                bot_token=settings.telegram_bot_token,
            )
        return

    if settings.core_route_telegram == "shadow":
        await _forward_to_legacy_consultant(update, trace_id)
        if is_telegram_deliverable(core_response.get("answer_mode")):
            logger.info(
                "telegram_shadow_core_price",
                extra={"trace_id": trace_id, "mode": core_response.get("answer_mode")},
            )
        return

    if is_telegram_deliverable(core_response.get("answer_mode")):
        await _deliver_core_answer(chat_id, core_response)
        return

    await _forward_to_legacy_consultant(update, trace_id)


@router.post("/v1/telegram/{binding_id}/webhook")
async def telegram_webhook(
    binding_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    _verify_telegram_secret(request)
    update = await request.json()
    trace_id = get_trace_id(request)
    background_tasks.add_task(_process_telegram_update, binding_id, update, trace_id)
    return {"ok": True}
