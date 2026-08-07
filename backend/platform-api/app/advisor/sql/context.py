"""Session context load/persist for structured advisor follow-ups."""

from __future__ import annotations

import json
from typing import Any

from app.db import fetch_one


async def load_session_context(conn, tenant_id: str, session_id: str) -> dict[str, Any]:
    row = await fetch_one(
        conn,
        """
        select context
        from platform_session_context
        where tenant_id = %s and session_id = %s
        limit 1
        """,
        (tenant_id, session_id),
    )
    if not row:
        return {}
    raw = row.get("context")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def merge_session_context(
    conn,
    tenant_id: str,
    session_id: str,
    patch: dict[str, Any],
) -> None:
    if not patch:
        return
    existing = await load_session_context(conn, tenant_id, session_id)
    merged = {**existing, **patch}
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into platform_session_context (tenant_id, session_id, context, updated_at)
            values (%s, %s, %s::jsonb, now())
            on conflict (tenant_id, session_id) do update
              set context = excluded.context,
                  updated_at = now()
            """,
            (tenant_id, session_id, json.dumps(merged, ensure_ascii=False)),
        )
