from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection


async def ensure_outbox_table(conn: AsyncConnection) -> None:
    # The table comes from platform_crm_v14.sql now. DDL only when it is really
    # missing: «create table if not exists» still needs CREATE on the schema,
    # which a least-privilege API role does not have (and it locks the catalog
    # on every enqueue).
    async with conn.cursor() as cur:
        await cur.execute("select to_regclass('platform_outbox') is not null as present")
        row = await cur.fetchone()
    if row is not None and (row["present"] if isinstance(row, dict) else row[0]):
        return
    await conn.execute(
        """
        create table if not exists platform_outbox (
          outbox_id bigserial primary key,
          tenant_id text not null,
          event_type text not null,
          idempotency_key text not null,
          payload jsonb not null default '{}'::jsonb,
          status text not null default 'pending'
            check (status in ('pending', 'processing', 'done', 'failed', 'dead')),
          attempts integer not null default 0,
          last_error text,
          due_at timestamptz,
          created_at timestamptz not null default now(),
          updated_at timestamptz not null default now(),
          unique (tenant_id, idempotency_key)
        )
        """
    )


async def enqueue_outbox_event(
    conn: AsyncConnection,
    tenant_id: str,
    event_type: str,
    idempotency_key: str,
    payload: dict[str, Any],
    due_at: datetime | None = None,
) -> dict[str, Any] | None:
    """Queue an event once per (tenant_id, idempotency_key).

    ``due_at`` makes it a scheduled notification: the job worker's
    ``process_due_notifications`` sends it when the time comes, and
    ``process_pending_outbox`` leaves it alone. Without ``due_at`` the insert is
    the same as before V14, so old databases keep working.
    """
    await ensure_outbox_table(conn)
    if due_at is not None:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into platform_outbox (
                  tenant_id, event_type, idempotency_key, payload, due_at
                )
                values (%s, %s, %s, %s::jsonb, %s)
                on conflict (tenant_id, idempotency_key) do update
                  set updated_at = now()
                returning outbox_id, status, (xmax = 0) as created
                """,
                (tenant_id, event_type, idempotency_key, json.dumps(payload, ensure_ascii=False), due_at),
            )
            row = await cur.fetchone()
        return dict(row) if row else None
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into platform_outbox (
              tenant_id, event_type, idempotency_key, payload
            )
            values (%s, %s, %s, %s::jsonb)
            on conflict (tenant_id, idempotency_key) do update
              set updated_at = now()
            returning outbox_id, status, (xmax = 0) as created
            """,
            (tenant_id, event_type, idempotency_key, json.dumps(payload)),
        )
        row = await cur.fetchone()
    return dict(row) if row else None
