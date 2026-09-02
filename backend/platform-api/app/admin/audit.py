from __future__ import annotations

import json
from typing import Any

from app.db import admin_connection, fetch_all


async def write_audit_log(
    *,
    principal_id: str | None,
    action: str,
    target_tenant_id: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    safe_details = _sanitize_details(details or {})
    async with admin_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into platform_admin_audit_log (
                  principal_id, action, target_tenant_id, object_type, object_id, details
                ) values (%s::uuid, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    principal_id,
                    action,
                    target_tenant_id,
                    object_type,
                    object_id,
                    json.dumps(safe_details, ensure_ascii=False),
                ),
            )


def _sanitize_details(details: dict[str, Any]) -> dict[str, Any]:
    blocked = {"contact", "phone", "email", "telegram", "token", "secret", "password", "name"}
    out: dict[str, Any] = {}
    for key, value in details.items():
        if key in blocked:
            continue
        if isinstance(value, dict):
            out[key] = _sanitize_details(value)
        elif isinstance(value, list):
            out[key] = [
                _sanitize_details(item) if isinstance(item, dict) else item for item in value[:20]
            ]
        else:
            out[key] = value
    return out


async def fetch_recent_audit(
    principal_id: str,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    async with admin_connection() as conn:
        return await fetch_all(
            conn,
            """
            select audit_id, action, target_tenant_id, object_type, object_id, created_at
            from platform_admin_audit_log
            where principal_id = %s::uuid
            order by created_at desc
            limit %s
            """,
            (principal_id, limit),
        )
