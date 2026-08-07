from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.db import fetch_all, get_pool, tenant_connection
from app.jobs.n8n_integration import trigger_lead_delivery
from app.jobs.outbox import ensure_outbox_table
from app.settings import get_settings

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


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


async def worker_loop(poll_interval_sec: float = 2.0) -> None:
    await init_pool_for_worker()
    while True:
        await process_pending_outbox()
        await asyncio.sleep(poll_interval_sec)


async def init_pool_for_worker() -> None:
    from app.db import init_pool

    await init_pool()


def main() -> None:
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
