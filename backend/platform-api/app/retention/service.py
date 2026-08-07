"""Retention registry and export hooks — no auto-delete."""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.db import fetch_all, fetch_one, tenant_connection


async def list_retention_registry(tenant_id: str) -> list[dict[str, Any]]:
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select data_class, table_name, retention_days, export_enabled, delete_enabled, notes
            from data_retention_registry
            where tenant_id = %s
            order by data_class, table_name
            """,
            (tenant_id,),
        )
    return [dict(r) for r in rows]


async def request_export(
    tenant_id: str,
    *,
    data_class: str,
    requester_scope: str,
    idempotency_key: str,
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        existing = await fetch_one(
            conn,
            "select export_id, status from data_export_requests where tenant_id = %s and idempotency_key = %s",
            (tenant_id, idempotency_key),
        )
        if existing:
            return {"ok": True, "created": False, "export_id": str(existing["export_id"]), "status": existing["status"]}

        export_id = str(uuid.uuid4())
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into data_export_requests (
                  export_id, tenant_id, requester_scope, data_class, status, idempotency_key
                ) values (%s::uuid, %s, %s, %s, 'pending', %s)
                """,
                (export_id, tenant_id, requester_scope, data_class, idempotency_key),
            )
    return {"ok": True, "created": True, "export_id": export_id, "status": "pending"}
