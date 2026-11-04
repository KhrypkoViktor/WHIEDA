from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.advisor.service import handle_structured_query
from app.db_feature_readiness import SchemaFeatureUnavailable, log_feature_unavailable
from app.max.bridges import bridge_for_telegram
from app.settings import get_settings
from app.telegram.admin_login import try_handle_admin_login
from app.telegram.content_access import try_handle_content_access
from app.telegram.bindings import (
    BotBindingContext,
    binding_context_scope,
    current_bot_binding,
    resolve_bot_binding_context,
    verify_webhook_secret,
)
from app.telegram.delivery import (
    answer_callback_query,
    deliver_structured_advisor_response,
    send_chat_action,
    send_telegram_text,
)
from app.telegram.log_safe import chat_ref
from app.telegram.modes import is_telegram_deliverable, should_deliver_telegram_response
from app.telegram.inbox import compact_telegram_payload, get_inbox_store
from app.telegram.processor import process_core_telegram_update
from app.telegram.sequencer import build_message_fingerprint, get_chat_sequencer
from app.telegram.support import is_support_forum_traffic
from app.telegram.worker import dispatch_inbox_work
from app.telegram.update_parser import (
    parse_telegram_callback,
    parse_shared_message,
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
    # Support forum group (topic per ticket): its registration command, topic
    # messages and the close button are the only group traffic Core serves.
    if route_mode == "core" and is_support_forum_traffic(update):
        try:
            await process_core_telegram_update(tenant, update, trace_id or "", binding=binding)
        except Exception:
            logger.exception("telegram_core_forum_failed")
        return

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

    # Фото и файлы без подписи — тоже сообщения: шаг «фото» в заявке на сайт и
    # чек об оплате приходят именно так. Раньше здесь пропускался только текст,
    # и такие сообщения молча терялись (23.09.2026: ни одна заявка не прошла
    # шаг «фото»). parse_telegram_message сам отбрасывает пустое.
    message = (update or {}).get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return

    # Выбор людей и переслан контакт («Добавить из Telegram») — тоже без текста: их пропускает отдельный разбор,
    # иначе они терялись бы здесь, как фото без подписи 23.09.2026.
    parsed_message = parse_telegram_message(update) or parse_shared_message(update)
    if not parsed_message or not should_process_telegram_message(
        parsed_message, binding.bot_username
    ):
        logger.info("telegram_group_message_ignored", extra={"trace_id": trace_id})
        return

    admin_result = await try_handle_admin_login(update, trace_id=trace_id)
    if admin_result is not None:
        return

    content_result = await try_handle_content_access(update, trace_id=trace_id)
    if content_result is not None:
        return

    # Советнику (legacy/shadow) нужен текст вопроса; медиа без подписи ему не шлём.
    text = parsed_message.text
    if route_mode == "legacy":
        if text:
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

    if not text:
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


# Задачи «бот печатает…»: держим ссылку, пока идут, иначе сборщик мусора может убрать их на лету.
_typing_tasks: set[asyncio.Task] = set()


async def _send_typing(binding: BotBindingContext, chat_id: int | str, trace_id: str | None) -> None:
    try:
        await send_chat_action(chat_id=str(chat_id), bot_token=binding.bot_token)
    except Exception:
        # Индикатор — удобство: Telegram не ответил, обработка кнопки идёт как шла.
        logger.warning(
            "telegram_typing_failed",
            extra={"trace_id": trace_id, "chat_id": chat_ref(str(chat_id))},
        )


def _schedule_typing(binding: BotBindingContext, callback: dict, trace_id: str | None) -> None:
    """Нажатие кнопки в личном чате бота WWC принято: «печатает…» уходит сразу, не дожидаясь очереди
    чата и обработчика (отдельная задача; сбой не влияет на обработку). Бот витрины автора Академии
    и чужие арендаторы — как были."""
    try:
        if (
            not get_settings().telegram_callback_typing
            or binding.processing_mode != "core"
            or binding.tenant.tenant_id != "whieda"
            or binding.showcase_id
        ):
            return
        chat = (callback.get("message") or {}).get("chat") or {}
        if str(chat.get("type") or "private") != "private" or chat.get("id") is None:
            return
        task = asyncio.get_running_loop().create_task(_send_typing(binding, chat["id"], trace_id))
        _typing_tasks.add(task)
        task.add_done_callback(_typing_tasks.discard)
    except Exception:
        logger.warning("telegram_typing_schedule_failed", extra={"trace_id": trace_id}, exc_info=True)


async def _ack_duplicate_callback(binding: BotBindingContext, callback: dict, trace_id: str | None) -> None:
    """Повторный тап отброшен как дубль: ответ без текста снимает «часики» на кнопке."""
    callback_query_id = str(callback.get("id") or "")
    if not callback_query_id:
        return
    try:
        await answer_callback_query(callback_query_id=callback_query_id, bot_token=binding.bot_token)
    except Exception:
        logger.warning("telegram_duplicate_ack_failed", extra={"trace_id": trace_id}, exc_info=True)


def _callback_kind(callback_data: str) -> str:
    """Семейство кнопки для журнала («svc:confirm», «site:skip»): без хвоста, где бывают id."""
    return ":".join(callback_data.split(":", 2)[:2]) or "-"


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
    # Кнопка — это callback_data и сообщение, под которым её нажали: «Пропустить» с одним и тем же
    # callback_data на следующем шаге анкеты стоит под новым сообщением и не считается повтором.
    message_fingerprint = build_message_fingerprint(
        text=text,
        callback_data=callback_data,
        message_id=(callback.get("message") or {}).get("message_id") if callback_data else None,
    )
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
        # Нажатия кнопок: повтор, пока первый тап ещё обрабатывается, отбрасывается сразу (а не после
        # очереди чата), «печатает…» уходит до очереди. Текстовые сообщения — как были.
        drop_inflight_duplicate=bool(callback_data),
        on_accepted=(lambda: _schedule_typing(binding, callback, trace_id)) if callback_data else None,
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
        if callback_data:
            await _ack_duplicate_callback(binding, callback, trace_id)
        return
    # Числа — в тексте строки, не в extra: формат журнала Core поля extra не печатает.
    logger.info(
        "telegram_update_processed kind=%s wait_ms=%d run_ms=%d",
        _callback_kind(callback_data) if callback_data else "message",
        round(result.wait_ms),
        round(result.run_ms),
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
    group = ((update.get("message") or {}).get("chat") or {})
    if group.get("type") in {"group", "supergroup"}:
        # Группы, где бот состоит (курс, клуб, поддержка): id и название в журнал — Bot API
        # списка своих групп не даёт, а владелец просит сверять участников (09.10.2026).
        # В тексте строки, не в extra: формат журнала Core поля extra не печатает.
        logger.info("telegram_group_seen chat_id=%s title=%s", group.get("id"), str(group.get("title") or "")[:120])
    bridge = bridge_for_telegram(group.get("id"))
    if bridge is not None:
        # Группа потока с мостом в Max (V27): сообщения владельца/куратора → Max, вступившие —
        # проверка оплаты. Обычная обработка обновления идёт дальше как была…
        background_tasks.add_task(_bridge_group_message, binding, bridge, update.get("message") or {}, trace_id)
        from app.max.bridge import bot_id_from_token, is_reply_to_bot_only

        # …кроме реплая на сообщение самого бота (переслано из Max) без @бота и не команды:
        # мост его взял, а подборщик ответил бы в группу «Не понял вопрос…».
        if is_reply_to_bot_only(
            update.get("message") or {}, bot_id=bot_id_from_token(binding.bot_token), bot_username=binding.bot_username
        ):
            return {"ok": True}
    callback = update.get("callback_query") or {}
    if str(callback.get("data") or "").startswith("brg:"):
        # Кнопки владельца «Удалить» / «Оставить» по вступившему без оплаты (V27).
        background_tasks.add_task(_bridge_owner_callback, binding, callback, trace_id)
        return {"ok": True}
    if str(callback.get("data") or "").startswith("chpost:"):
        # «Опубликовать» / «Отменить» под черновиком поста в канал (11.10.2026).
        background_tasks.add_task(_channel_draft_callback, binding, callback, trace_id)
        return {"ok": True}
    if "channel_post" in update or "edited_channel_post" in update:
        # Пост «WWC Official channel» → чаты Max (V24); правки постов и чужие каналы — мимо.
        post = update.get("channel_post")
        source = get_settings().crosspost_telegram_channel_id
        if post and source and int((post.get("chat") or {}).get("id") or 0) == int(source):
            background_tasks.add_task(_crosspost_to_max, binding, post, trace_id)
        return {"ok": True}
    if _durable_inbox_enabled(binding):
        if await _enqueue_durable_update(binding, update, trace_id, background_tasks):
            return {"ok": True}
    background_tasks.add_task(_process_telegram_update, binding, update, trace_id)
    return {"ok": True}


async def _crosspost_to_max(binding: BotBindingContext, post: dict, trace_id: str | None) -> None:
    from app.max.crosspost import crosspost_channel_post

    if not get_settings().max_bot_token:
        return
    try:
        result = await crosspost_channel_post(binding.tenant.tenant_id, post, bot_token=binding.bot_token)
        logger.info("telegram_channel_post_crossposted", extra={"trace_id": trace_id, "status": result.get("status")})
    except Exception:
        logger.exception("telegram_channel_post_crosspost_failed", extra={"trace_id": trace_id})


async def _bridge_group_message(binding: BotBindingContext, bridge: Any, message: dict, trace_id: str | None) -> None:
    from app.max.bridge import bot_id_from_token, bridge_telegram_message, owner_telegram_id, telegram_role, watch_telegram_join

    tenant_id = binding.tenant.tenant_id
    try:
        if message.get("new_chat_members"):
            await watch_telegram_join(tenant_id, bridge, message, bot_token=binding.bot_token)
            return
        if not get_settings().max_bot_token:
            return
        role = telegram_role(message, bridge, owner_id=owner_telegram_id(), bot_id=bot_id_from_token(binding.bot_token))
        if role is None:
            return
        result = await bridge_telegram_message(tenant_id, bridge, message, role=role, bot_token=binding.bot_token)
        logger.info("telegram_group_bridged", extra={"trace_id": trace_id, "status": result.get("status")})
    except Exception:
        logger.exception("telegram_group_bridge_failed", extra={"trace_id": trace_id})


async def _bridge_owner_callback(binding: BotBindingContext, callback: dict, trace_id: str | None) -> None:
    from app.max.bridge import handle_owner_callback

    try:
        await handle_owner_callback(callback, bot_token=binding.bot_token)
    except Exception:
        logger.exception("telegram_bridge_callback_failed", extra={"trace_id": trace_id})


async def _channel_draft_callback(binding: BotBindingContext, callback: dict, trace_id: str | None) -> None:
    from app.telegram.channel_posts import handle_draft_callback

    try:
        result = await handle_draft_callback(callback, tenant_id=binding.tenant.tenant_id, bot_token=binding.bot_token)
        logger.info("channel_draft_callback", extra={"trace_id": trace_id, "status": result.get("status")})
    except Exception:
        logger.exception("channel_draft_callback_failed", extra={"trace_id": trace_id})


def _durable_inbox_enabled(binding: BotBindingContext) -> bool:
    enabled = get_settings().parsed_telegram_durable_inbox_bindings
    return "*" in enabled or binding.binding_id in enabled


async def _enqueue_durable_update(
    binding: BotBindingContext,
    update: Any,
    trace_id: str | None,
    background_tasks: BackgroundTasks,
) -> bool:
    """Persist the update and hand it to the inbox worker.

    Returns False when the inbox tables are known to be missing (degraded), so
    the caller keeps the in-process path instead of dropping the update. An
    unknown probe (database unreachable) stays a 503 so Telegram retries.
    """
    payload = compact_telegram_payload(update if isinstance(update, dict) else None)
    if payload is None:
        return True
    try:
        enqueued = await get_inbox_store().enqueue(
            binding_id=binding.binding_id,
            tenant_id=binding.tenant.tenant_id,
            telegram_update_id=int(payload["update_id"]),
            payload=payload,
        )
    except SchemaFeatureUnavailable as exc:
        log_feature_unavailable(exc.status)
        if exc.status.state == "degraded":
            return False
        raise HTTPException(
            status_code=503,
            detail={"error": "telegram_inbox_unavailable"},
        ) from exc
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
    return True
