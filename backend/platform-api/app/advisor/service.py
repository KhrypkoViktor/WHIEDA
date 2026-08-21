from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from app.advisor.sql.engine import run_structured_query
from app.db import fetch_one, tenant_connection
from app.errors import advisor_error_response
from app.tenancy import TenantContext


async def handle_structured_query(
    tenant: TenantContext,
    body: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    body = dict(body)
    body.pop("tenant", None)
    question = str(body.get("question") or "").strip()
    session = str(body.get("session") or body.get("session_id") or "").strip()
    if not question or not session:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "session_and_question_required"})

    ref = str(body.get("ref") or "").strip().lower() or None
    await _upsert_session_context(tenant.tenant_id, session, ref)
    return await run_structured_query(tenant, body, trace_id)


async def _upsert_session_context(tenant_id: str, session: str, ref: str | None) -> None:
    async with tenant_connection(tenant_id) as conn:
        existing = await fetch_one(
            conn,
            """
            select first_ref
            from platform_session_context
            where tenant_id = %s and session_id = %s
            limit 1
            """,
            (tenant_id, session),
        )
        first_ref = existing["first_ref"] if existing and existing.get("first_ref") else ref
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into platform_session_context (
                  tenant_id, session_id, first_ref, active_ref, updated_at
                )
                values (%s, %s, %s, %s, now())
                on conflict (tenant_id, session_id) do update
                  set active_ref = coalesce(excluded.active_ref, platform_session_context.active_ref),
                      first_ref = coalesce(platform_session_context.first_ref, excluded.first_ref),
                      updated_at = now()
                """,
                (tenant_id, session, first_ref, ref),
            )


def advisor_error(request: Request, message: str | None = None) -> dict[str, Any]:
    return advisor_error_response(request, message or "")
