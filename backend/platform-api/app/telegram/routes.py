from __future__ import annotations

import hashlib
import logging
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.advisor.service import handle_structured_query
from app.settings import get_settings
from app.telegram.admin_login import try_handle_admin_login
from app.telegram.bindings import (
    BotBindingContext,
    binding_context_scope,
    current_bot_binding,
    resolve_bot_binding_context,
    verify_webhook_secret,
)
from app.telegram.delivery import deliver_structured_advisor_response, send_telegram_text
from app.telegram.log_safe import chat_ref
from app.telegram.modes import is_telegram_deliverable, should_deliver_telegram_response
from app.telegram.inbox import compact_telegram_payload, get_inbox_store
from app.telegram.processor import process_core_telegram_update
from app.telegram.sequencer import build_message_fingerprint, get_chat_sequencer
from app.telegram.worker import dispatch_inbox_work
from app.telegram.update_parser import (
    parse_telegram_callback,
    parse_telegram_message,
    should_process_telegram_message,
)
from app.tenancy import get_trace_id

LEGACY_TELEGRAM_WEBHOOK = "/webhook/advisor-whieda-v0"

router = APIRouter(tags=["telegram"])
logger = logging.getLogger(__name__)


async def _deliver_core_answer(chat_id: str, core_response: dict) -> None:
    binding = current_bot_binding()
    await deliver_structured_advisor_response(
        chat_id=str(chat_id),
        core_response=core_response,
        bot_token=binding.bot_token,
        tenant_id=binding.tenant.tenant_id,
        binding_status=binding.status,
    )


async def _forward_to_legacy_consultant(
    update: dict,
    trace_id: str | None = None,
) -> None:
    binding = current_bot_binding()
    if binding.tenant.tenant_id != "whieda":
        raise RuntimeError("non_whieda_legacy_fallback_forbidden")
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
        if chat_id:
            await send_telegram_text(
                chat_id=str(chat_id),
                text="Сейчас высокая нагрузка на советника. Повторите вопрос через минуту.",
                bot_token=binding.bot_token,
            )


async def _process_telegram_update_body(
    binding: BotBindingContext,
    update: dict,
    trace_id: str | None,
) -> None:
    tenant = binding.tenant
    route_mode = binding.processing_mode
    callback = parse_telegram_callback(update)
    if callback:
        if callback.chat_type != "private":
            logger.info("telegram_group_callback_ignored", extra={"trace_id": trace_id})
            return
        if route_mode == "legacy":
            return
        if route_mode == "core":
            try:
                await process_core_telegram_update(
                    tenant,
                    update,
                    trace_id or "",
                    binding=binding,
                )
            except Exception:
                logger.exception("telegram_core_callback_failed")
            return
        return

    message = (update or {}).get("message") or {}
    text = str(message.get("text") or "").strip()
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not text or not chat_id:
        return

    parsed_message = parse_telegram_message(update)
    if not parsed_message or not should_process_telegram_message(
        parsed_message, binding.bot_username
    ):
        logger.info("telegram_group_message_ignored", extra={"trace_id": trace_id})
        return

    admin_result = await try_handle_admin_login(update, trace_id=trace_id)
    if admin_result is not None:
        return

    if route_mode == "legacy":
        await _forward_to_legacy_consultant(update, trace_id)
        return

    if route_mode == "core":
        try:
            await process_core_telegram_update(
                tenant,
                update,
                trace_id or "",
                binding=binding,
            )
        except Exception:
            logger.exception("telegram_core_processor_failed")
            if chat_id:
                await send_telegram_text(
                    chat_id=str(chat_id),
                    text="Не удалось обработать сообщение. Попробуйте ещё раз.",
                    bot_token=binding.bot_token,
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
        await send_telegram_text(
            chat_id=str(chat_id),
            text="Не удалось обработать запрос. Попробуйте ещё раз.",
            bot_token=binding.bot_token,
        )
        return

    if route_mode == "shadow":
        await _forward_to_legacy_consultant(update, trace_id)
        if is_telegram_deliverable(core_response.get("answer_mode")):
            logger.info(
                "telegram_shadow_core_price",
                extra={"trace_id": trace_id, "mode": core_response.get("answer_mode")},
            )
        return

    if should_deliver_telegram_response(core_response):
        await _deliver_core_answer(chat_id, core_response)
        return

    await _forward_to_legacy_consultant(update, trace_id)


async def _process_telegram_update(
    binding: BotBindingContext,
    update: dict,
    trace_id: str | None,
) -> None:
    message = (update or {}).get("message") or {}
    callback = (update or {}).get("callback_query") or {}
    message_chat_id = (message.get("chat") or {}).get("id")
    callback_chat_id = ((callback.get("message") or {}).get("chat") or {}).get("id")
    chat_id = message_chat_id or callback_chat_id
    if chat_id is None:
        return
    update_id = (update or {}).get("update_id")
    message_id = message.get("message_id")
    text = str(message.get("text") or "")
    callback_data = str(callback.get("data") or "")
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else None
    callback_hash = (
        hashlib.sha256(callback_data.encode("utf-8")).hexdigest()[:12] if callback_data else None
    )
    message_fingerprint = build_message_fingerprint(text=text, callback_data=callback_data)
    chat_key = str(chat_id)
    sequencer = get_chat_sequencer()

    logger.info(
        "telegram_update_received",
        extra={
            "trace_id": trace_id,
            "update_id": update_id,
            "message_id": message_id,
            "chat_id": chat_ref(chat_key),
            "text_hash": text_hash,
            "callback_hash": callback_hash,
        },
    )

    async def handler() -> None:
        with binding_context_scope(binding):
            await _process_telegram_update_body(binding, update, trace_id)

    result = await sequencer.run_ordered(
        chat_key,
        update_id,
        handler,
        message_fingerprint=message_fingerprint,
        namespace=binding.binding_id,
    )
    if result.duplicate:
        logger.info(
            "telegram_duplicate_update_ignored",
            extra={
                "trace_id": trace_id,
                "update_id": update_id,
                "chat_id": chat_ref(chat_key),
            },
        )


@router.post("/v1/telegram/{binding_id}/webhook")
async def telegram_webhook(
    binding_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    binding = await resolve_bot_binding_context(binding_id)
    if binding is None:
        logger.info(
            "telegram_bot_binding_ignored",
            extra={"binding_id": binding_id, "trace_id": get_trace_id(request)},
        )
        return {"ok": True}
    verify_webhook_secret(
        binding,
        request.headers.get("x-telegram-bot-api-secret-token", ""),
    )
    update = await request.json()
    trace_id = get_trace_id(request)
    payload = compact_telegram_payload(update if isinstance(update, dict) else None)
    if payload is None:
        return {"ok": True}
    try:
        enqueued = await get_inbox_store().enqueue(
            binding_id=binding.binding_id,
            tenant_id=binding.tenant.tenant_id,
            telegram_update_id=int(payload["update_id"]),
            payload=payload,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "telegram_inbox_unavailable"},
        ) from exc
    background_tasks.add_task(
        dispatch_inbox_work,
        preferred_inbox_id=enqueued.inbox_id,
        webhook_binding=binding,
        trace_id=trace_id,
    )
    return {"ok": True}
