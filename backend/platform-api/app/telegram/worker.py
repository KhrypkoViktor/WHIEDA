"""Claim pending Telegram inbox rows and deliver with binding-scoped outbox."""

from __future__ import annotations

import logging
import os
from typing import Any, Awaitable, Callable

from app.telegram.bindings import BotBindingContext, binding_context_scope, resolve_bot_binding_context
from app.telegram.delivery import TelegramDeliveryError, outbound_binding_guard
from app.telegram.inbox import (
    InboxRecord,
    InboxStore,
    business_idempotency_key,
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
    if claimed is None:
        return None
    await handle_claimed_inbox(
        claimed,
        webhook_binding=webhook_binding,
        trace_id=trace_id,
        store=inbox_store,
        process_update=process_update,
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

    outbox = await store.reserve_outbox(
        inbox_id=claimed.inbox_id,
        binding_id=claimed.binding_id,
        tenant_id=claimed.tenant_id,
        idempotency_key=business_idempotency_key(claimed.inbox_id),
    )
    if outbox.state == "sent":
        await store.mark_processed(claimed.inbox_id)
        logger.info(
            "telegram_inbox_already_delivered",
            extra={"binding_id": claimed.binding_id, "trace_id": trace_id},
        )
        return

    from app.telegram.routes import _process_telegram_update

    handler = process_update or _process_telegram_update
    try:
        with binding_context_scope(binding), outbound_binding_guard(binding.bot_token):
            await handler(binding, claimed.payload, trace_id)
        await store.mark_outbox_sent(outbox.outbox_id)
        await store.mark_processed(claimed.inbox_id)
    except TelegramDeliveryError as exc:
        await store.release_for_retry(claimed.inbox_id, safe_error_summary(exc))
        logger.warning(
            "telegram_inbox_delivery_retry",
            extra={"binding_id": claimed.binding_id, "trace_id": trace_id},
        )
    except Exception as exc:
        await store.release_for_retry(claimed.inbox_id, safe_error_summary(exc))
        logger.exception(
            "telegram_inbox_handler_retry",
            extra={"binding_id": claimed.binding_id, "trace_id": trace_id},
        )
