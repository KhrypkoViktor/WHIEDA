"""Claim pending Telegram inbox rows, persist a delivery plan, then drain outbox."""

from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable

from app.db_feature_readiness import SchemaFeatureUnavailable, get_feature_status, log_feature_unavailable
from app.telegram.bindings import BotBindingContext, binding_context_scope, resolve_bot_binding_context
from app.telegram.delivery import (
    TelegramDeliveryError,
    TelegramDeliveryUnknown,
    capture_delivery_plan,
    outbound_binding_guard,
    send_telegram_photo,
    send_telegram_text,
)
from app.telegram.inbox import (
    DeliveryOutboxRecord,
    InMemoryInboxStore,
    InboxRecord,
    InboxStore,
    get_inbox_store,
    safe_error_summary,
)

logger = logging.getLogger(__name__)

ProcessUpdate = Callable[[BotBindingContext, dict[str, Any], str | None], Awaitable[None]]


def worker_owner() -> str:
    return f"core-pid:{os.getpid()}"


async def dispatch_inbox_work(
    *,
    preferred_inbox_id: str | None,
    webhook_binding: BotBindingContext | None,
    trace_id: str | None,
    store: InboxStore | None = None,
    process_update: ProcessUpdate | None = None,
    owner: str | None = None,
) -> InboxRecord | None:
    inbox_store = store or get_inbox_store()
    claim_owner = owner or worker_owner()
    claimed = None
    if preferred_inbox_id:
        claimed = await inbox_store.claim_by_id(preferred_inbox_id, claim_owner)
    if claimed is None:
        claimed = await inbox_store.claim_next(claim_owner)
    if claimed is not None:
        await handle_claimed_inbox(
            claimed,
            webhook_binding=webhook_binding,
            trace_id=trace_id,
            store=inbox_store,
            process_update=process_update,
        )
    if webhook_binding is not None:
        await drain_binding_outbox(
            webhook_binding,
            trace_id=trace_id,
            store=inbox_store,
            owner=claim_owner,
        )
    return claimed


async def handle_claimed_inbox(
    claimed: InboxRecord,
    *,
    webhook_binding: BotBindingContext | None,
    trace_id: str | None,
    store: InboxStore,
    process_update: ProcessUpdate | None = None,
) -> None:
    binding = webhook_binding
    if binding is None or binding.binding_id != claimed.binding_id:
        binding = await resolve_bot_binding_context(claimed.binding_id)
    if binding is None or binding.binding_id != claimed.binding_id:
        await store.release_for_retry(claimed.inbox_id, "binding_unavailable")
        logger.warning(
            "telegram_inbox_binding_unavailable",
            extra={"binding_id": claimed.binding_id, "trace_id": trace_id},
        )
        return

    if not isinstance(store, InMemoryInboxStore):
        outbox_status = await get_feature_status("telegram_durable_outbox")
        if not outbox_status.ready:
            log_feature_unavailable(outbox_status)
            await store.release_for_retry(claimed.inbox_id, "schema_feature_unavailable")
            return

    from app.telegram.routes import _process_telegram_update

    handler = process_update or _process_telegram_update
    try:
        with capture_delivery_plan() as drafts:
            with binding_context_scope(binding):
                await handler(binding, claimed.payload, trace_id)
        await store.enqueue_delivery_plan(
            inbox_id=claimed.inbox_id,
            binding_id=claimed.binding_id,
            tenant_id=claimed.tenant_id,
            telegram_update_id=claimed.telegram_update_id,
            items=list(drafts),
        )
        await store.mark_processed(claimed.inbox_id)
    except SchemaFeatureUnavailable as exc:
        log_feature_unavailable(exc.status)
        await store.release_for_retry(claimed.inbox_id, "schema_feature_unavailable")
        return
    except Exception as exc:
        await store.release_for_retry(claimed.inbox_id, safe_error_summary(exc))
        logger.exception(
            "telegram_inbox_handler_retry",
            extra={"binding_id": claimed.binding_id, "trace_id": trace_id},
        )
        return
    await drain_binding_outbox(
        binding,
        trace_id=trace_id,
        store=store,
    )


async def drain_binding_outbox(
    binding: BotBindingContext,
    *,
    trace_id: str | None,
    store: InboxStore,
    owner: str | None = None,
    limit: int = 32,
) -> int:
    sent = 0
    claim_owner = owner or worker_owner()
    for _ in range(max(limit, 1)):
        item = await store.claim_delivery(claim_owner, binding_id=binding.binding_id)
        if item is None:
            break
        if item.binding_id != binding.binding_id or item.tenant_id != binding.tenant.tenant_id:
            await store.mark_delivery_unknown(item.outbox_id, "binding_mismatch")
            logger.warning(
                "telegram_outbox_binding_mismatch",
                extra={"binding_id": item.binding_id, "trace_id": trace_id},
            )
            continue
        try:
            await _send_delivery_item(binding, item)
            await store.mark_delivery_sent(item.outbox_id)
            sent += 1
        except TelegramDeliveryUnknown as exc:
            await store.mark_delivery_unknown(item.outbox_id, safe_error_summary(exc))
            logger.warning(
                "telegram_outbox_unknown_delivery",
                extra={
                    "binding_id": binding.binding_id,
                    "trace_id": trace_id,
                    "sequence_no": item.sequence_no,
                },
            )
            break
        except TelegramDeliveryError as exc:
            status = await store.mark_delivery_retryable(item.outbox_id, safe_error_summary(exc))
            logger.warning(
                "telegram_outbox_retryable",
                extra={
                    "binding_id": binding.binding_id,
                    "trace_id": trace_id,
                    "status": status,
                    "sequence_no": item.sequence_no,
                },
            )
            break
        except Exception as exc:
            status = await store.mark_delivery_retryable(item.outbox_id, safe_error_summary(exc))
            logger.exception(
                "telegram_outbox_retryable",
                extra={"binding_id": binding.binding_id, "trace_id": trace_id, "status": status},
            )
            break
    return sent


async def _send_delivery_item(binding: BotBindingContext, item: DeliveryOutboxRecord) -> None:
    chat_id = str(item.payload.get("chat_id") or "").strip()
    if not chat_id:
        raise TelegramDeliveryError("telegram_delivery_missing_chat")
    with outbound_binding_guard(binding.bot_token):
        if item.kind == "photo":
            photo_url = str(item.payload.get("photo_url") or "").strip()
            result = await send_telegram_photo(
                chat_id=chat_id,
                photo_url=photo_url,
                bot_token=binding.bot_token,
            )
        else:
            markup = item.payload.get("reply_markup")
            result = await send_telegram_text(
                chat_id=chat_id,
                text=str(item.payload.get("text") or ""),
                bot_token=binding.bot_token,
                reply_markup=markup if isinstance(markup, dict) else None,
            )
    if not result.get("ok") and not result.get("skipped"):
        raise TelegramDeliveryError("telegram_delivery_rejected")
