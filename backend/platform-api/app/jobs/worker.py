from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.db import fetch_all, get_pool, tenant_connection
from app.jobs.n8n_integration import trigger_lead_delivery
from app.jobs.outbox import ensure_outbox_table
from app.settings import get_settings
from app.telegram.bindings import BotBindingContext, resolve_bot_binding_context
from app.telegram.delivery import (
    TelegramDeliveryError,
    TelegramDeliveryUnknown,
    outbound_binding_guard,
    send_telegram_text,
)
from app.telegram.inbox import safe_error_summary

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5

# Scheduled notifications (platform_outbox.due_at, V14): the CRM morning message.
DUE_NOTIFY_INTERVAL_SEC = 30.0
CRM_DIGEST_INTERVAL_SEC = 300.0
DUE_BATCH_SIZE = 50
DUE_MAX_ATTEMPTS = 3


async def process_pending_outbox(batch_size: int = 20) -> int:
    settings = get_settings()
    pool = get_pool()
    processed = 0

    async with pool.connection(timeout=settings.database_timeout_sec) as conn:
        await ensure_outbox_table(conn)
        async with conn.transaction():
            rows = await fetch_all(
                conn,
                """
                select outbox_id, tenant_id, event_type, idempotency_key, payload, attempts
                from platform_outbox
                where status in ('pending', 'failed')
                  and attempts < %s
                  -- Rows with due_at belong to process_due_notifications. to_jsonb
                  -- instead of the column: works before and after the V14 migration.
                  and to_jsonb(platform_outbox) ->> 'due_at' is null
                order by created_at
                limit %s
                for update skip locked
                """,
                (MAX_ATTEMPTS, batch_size),
            )

    for row in rows:
        ok = await _dispatch_event(row)
        if ok:
            processed += 1
    return processed


async def _dispatch_event(row: dict[str, Any]) -> bool:
    tenant_id = row["tenant_id"]
    outbox_id = row["outbox_id"]
    event_type = row["event_type"]

    try:
        async with tenant_connection(tenant_id) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update platform_outbox
                    set status = 'processing', updated_at = now()
                    where outbox_id = %s
                    """,
                    (outbox_id,),
                )

            if event_type == "lead_created":
                lead_id = str(row["payload"].get("lead_id") or "")
                if lead_id:
                    await trigger_lead_delivery(lead_id, tenant_id=tenant_id)

            elif event_type == "onboarding_reminder":
                logger.info(
                    "onboarding_reminder_ready",
                    extra={"tenant_id": tenant_id, "payload": row["payload"]},
                )

            elif event_type == "leader_digest_weekly":
                logger.info(
                    "leader_digest_ready",
                    extra={"tenant_id": tenant_id, "payload": row["payload"]},
                )

            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update platform_outbox
                    set status = 'done', updated_at = now(), last_error = null
                    where outbox_id = %s
                    """,
                    (outbox_id,),
                )
        return True
    except Exception as exc:
        attempts = int(row["attempts"]) + 1
        status = "dead" if attempts >= MAX_ATTEMPTS else "failed"
        settings = get_settings()
        pool = get_pool()
        async with pool.connection(timeout=settings.database_timeout_sec) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update platform_outbox
                    set status = %s,
                        attempts = %s,
                        last_error = %s,
                        updated_at = now()
                    where outbox_id = %s
                    """,
                    (status, attempts, str(exc)[:500], outbox_id),
                )
        logger.exception("outbox_dispatch_failed", extra={"outbox_id": outbox_id})
        return False


async def scheduled_notify_bindings() -> dict[str, BotBindingContext]:
    """tenant_id → the bot this process sends scheduled notifications with.

    Production and staging share one database and both run this worker, so the
    bots are named explicitly (PLATFORM_SCHEDULED_NOTIFY_BINDINGS) instead of
    «any active binding of the tenant»: only the production worker sets it.
    """
    bindings: dict[str, BotBindingContext] = {}
    for binding_id in get_settings().parsed_scheduled_notify_bindings():
        try:
            binding = await resolve_bot_binding_context(binding_id)
        except Exception as exc:
            logger.warning(
                "scheduled_notify_binding_unavailable",
                extra={"binding_id": binding_id, "error": safe_error_summary(exc)},
            )
            continue
        if binding is not None:
            bindings.setdefault(binding.tenant.tenant_id, binding)
    return bindings


def _final_delivery_error(exc: Exception) -> bool:
    """Ambiguous delivery and «chat not found / bot blocked» are not retried."""
    if isinstance(exc, TelegramDeliveryUnknown):
        return True
    return str(exc).endswith((":400", ":403"))


async def _send_due_notification(binding: BotBindingContext, payload: dict[str, Any]) -> None:
    chat_id = str(payload.get("chat_id") or "").strip()
    text = str(payload.get("text") or "")
    if not chat_id or not text.strip():
        raise TelegramDeliveryUnknown("due_notification_empty")
    markup = payload.get("reply_markup")
    with outbound_binding_guard(binding.bot_token):
        result = await send_telegram_text(
            chat_id=chat_id,
            text=text,
            bot_token=binding.bot_token,
            reply_markup=markup if isinstance(markup, dict) and markup else None,
        )
    if not result.get("ok"):
        raise TelegramDeliveryError("telegram_delivery_rejected")


async def _finish_due(outbox_id: int, *, status: str, attempts: int, error: str | None, retry: bool) -> None:
    settings = get_settings()
    async with get_pool().connection(timeout=settings.database_timeout_sec) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update platform_outbox
                set status = %s,
                    attempts = %s,
                    last_error = %s,
                    due_at = case when %s then now() + interval '10 minutes' else due_at end,
                    updated_at = now()
                where outbox_id = %s
                """,
                (status, attempts, error, retry, outbox_id),
            )


async def process_due_notifications(
    bindings: dict[str, BotBindingContext] | None = None,
    *,
    batch_size: int = DUE_BATCH_SIZE,
) -> int:
    """Send platform_outbox rows whose due_at has come; status done / failed.

    Only rows planned for a bot of this process (payload.binding_id) are
    claimed. Separate from process_pending_outbox, which has other semantics.
    A failed send is retried in 10 minutes, up to DUE_MAX_ATTEMPTS; an ambiguous
    delivery or a 400/403 from Telegram is final (no double message).
    """
    if bindings is None:
        bindings = await scheduled_notify_bindings()
    by_id = {binding.binding_id: binding for binding in bindings.values()}
    if not by_id:
        return 0
    settings = get_settings()
    async with get_pool().connection(timeout=settings.database_timeout_sec) as conn:
        async with conn.transaction():
            rows = await fetch_all(
                conn,
                """
                select outbox_id, tenant_id, event_type, payload, attempts
                from platform_outbox
                where status = 'pending'
                  and due_at is not null
                  and due_at <= now()
                  and payload ->> 'binding_id' = any(%s)
                order by due_at
                limit %s
                for update skip locked
                """,
                (list(by_id), batch_size),
            )
            if rows:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "update platform_outbox set status = 'processing', updated_at = now() where outbox_id = any(%s)",
                        ([row["outbox_id"] for row in rows],),
                    )

    sent = 0
    for row in rows:
        payload = row["payload"] if isinstance(row["payload"], dict) else {}
        binding = by_id.get(str(payload.get("binding_id") or ""))
        attempts = int(row["attempts"] or 0) + 1
        try:
            if binding is None or binding.tenant.tenant_id != row["tenant_id"]:
                raise TelegramDeliveryUnknown("binding_mismatch")
            await _send_due_notification(binding, payload)
        except Exception as exc:
            final = _final_delivery_error(exc) or attempts >= DUE_MAX_ATTEMPTS
            await _finish_due(
                row["outbox_id"],
                status="failed" if final else "pending",
                attempts=attempts,
                error=safe_error_summary(exc)[:500],
                retry=not final,
            )
            logger.warning(
                "due_notification_failed",
                extra={"outbox_id": row["outbox_id"], "event_type": row["event_type"], "final": final},
            )
            continue
        await _finish_due(row["outbox_id"], status="done", attempts=attempts, error=None, retry=False)
        sent += 1
    return sent


async def scheduled_notifications_step(*, plan_crm: bool) -> dict[str, int]:
    """Plan the CRM mornings (every 5 minutes) and send what is due (every 30 s).
    Nothing happens in a process without PLATFORM_SCHEDULED_NOTIFY_BINDINGS."""
    if not get_settings().parsed_scheduled_notify_bindings():
        return {}
    bindings = await scheduled_notify_bindings()
    result: dict[str, int] = {}
    if plan_crm:
        from app.crm.digest import enqueue_crm_digests
        from app.crm.service import crm_feature_enabled

        if crm_feature_enabled():
            crm_tenants = {
                tenant_id: binding.binding_id
                for tenant_id, binding in bindings.items()
                if binding.tenant.entitlements.get("crm")
            }
            result["crm_digests"] = await enqueue_crm_digests(crm_tenants)
    result["due_sent"] = await process_due_notifications(bindings)
    return result


async def worker_loop(poll_interval_sec: float = 2.0) -> None:
    await init_pool_for_worker()
    last_due = last_plan = float("-inf")
    while True:
        await process_pending_outbox()
        now = time.monotonic()
        if now - last_due >= DUE_NOTIFY_INTERVAL_SEC:
            plan_crm = now - last_plan >= CRM_DIGEST_INTERVAL_SEC
            last_due = now
            if plan_crm:
                last_plan = now
            try:
                await scheduled_notifications_step(plan_crm=plan_crm)
            except Exception:
                logger.exception("scheduled_notifications_step_failed")
        await asyncio.sleep(poll_interval_sec)


async def init_pool_for_worker() -> None:
    from app.db import init_pool

    await init_pool()


def main() -> None:
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
