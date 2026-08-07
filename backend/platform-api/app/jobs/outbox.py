from __future__ import annotations

import json
from typing import Any

from psycopg import AsyncConnection


async def ensure_outbox_table(conn: AsyncConnection) -> None:
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
) -> dict[str, Any] | None:
    await ensure_outbox_table(conn)
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
