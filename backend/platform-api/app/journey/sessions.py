"""Visitor session read/update for site client."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import HTTPException

from app.db import fetch_one, tenant_connection
from app.identity.service import JOURNEY_TYPES, _resolve_owner_from_ref, _sanitize_context

FORBIDDEN_BODY_FIELDS = frozenset(
    {
        "owner_id",
        "assigned_owner_id",
        "attributed_owner_id",
        "telegram_user_id",
    }
)


def _clean(value: Any, max_len: int = 240) -> str:
    return str(value or "").strip()[:max_len]


def _validate_body(body: dict[str, Any]) -> None:
    for field in FORBIDDEN_BODY_FIELDS:
        if body.get(field):
            raise HTTPException(status_code=400, detail={"ok": False, "error": "forbidden_field"})


async def get_visitor_session(tenant_id: str, session_id: str) -> dict[str, Any]:
    try:
        uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid_session_id"}) from exc

    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select session_id, first_ref, current_ref, attributed_owner_id, assigned_owner_id,
                   journey_type, campaign, source, content, consent_scope, context,
                   created_at, updated_at
            from visitor_sessions
            where tenant_id = %s and session_id = %s::uuid
            limit 1
            """,
            (tenant_id, session_id),
        )
    if not row:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "session_not_found"})

    return {
        "ok": True,
        "visitor_session_id": str(row["session_id"]),
        "first_ref": row.get("first_ref"),
        "current_ref": row.get("current_ref"),
        "journey_type": row.get("journey_type"),
        "campaign": row.get("campaign"),
        "source": row.get("source"),
        "content": row.get("content"),
        "context": row.get("context") or {},
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
    }


async def upsert_visitor_session(tenant_id: str, body: dict[str, Any]) -> dict[str, Any]:
    _validate_body(body)

    session_raw = _clean(body.get("visitor_session_id"), 64)
    session_id = session_raw or str(uuid.uuid4())
    try:
        uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid_session_id"}) from exc

    ref_code = _clean(body.get("ref") or body.get("current_ref"), 64).lower() or None
    journey_type = _clean(body.get("journey_type") or "organic", 32).lower()
    if journey_type not in JOURNEY_TYPES:
        journey_type = "organic"

    context_patch = _sanitize_context(body.get("context"))
    campaign = _clean(body.get("campaign"), 120) or None
    source = _clean(body.get("source"), 120) or None
    content = _clean(body.get("content"), 120) or None
    consent_scope = _clean(body.get("consent_scope"), 120) or None

    ref_code, owner_id = await _resolve_owner_from_ref(tenant_id, ref_code)

    async with tenant_connection(tenant_id) as conn:
        existing = await fetch_one(
            conn,
            """
            select session_id, first_ref, attributed_owner_id
            from visitor_sessions
            where tenant_id = %s and session_id = %s::uuid
            limit 1
            """,
            (tenant_id, session_id),
        )

        first_ref = existing.get("first_ref") if existing else ref_code
        if existing:
            if existing.get("first_ref") and ref_code and existing["first_ref"] != ref_code:
                ref_code = existing["first_ref"]
                owner_id = existing.get("attributed_owner_id")
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update visitor_sessions
                    set current_ref = coalesce(%s, current_ref),
                        journey_type = %s,
                        campaign = coalesce(%s, campaign),
                        source = coalesce(%s, source),
                        content = coalesce(%s, content),
                        consent_scope = coalesce(%s, consent_scope),
                        context = context || %s::jsonb,
                        updated_at = now()
                    where tenant_id = %s and session_id = %s::uuid
                    """,
                    (
                        ref_code,
                        journey_type,
                        campaign,
                        source,
                        content,
                        consent_scope,
                        json.dumps(context_patch, ensure_ascii=False),
                        tenant_id,
                        session_id,
                    ),
                )
        else:
            first_ref = ref_code
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    insert into visitor_sessions (
                      tenant_id, session_id, first_ref, current_ref,
                      attributed_owner_id, journey_type,
                      campaign, source, content, consent_scope, context
                    ) values (
                      %s, %s::uuid, %s, %s,
                      %s, %s,
                      %s, %s, %s, %s, %s::jsonb
                    )
                    """,
                    (
                        tenant_id,
                        session_id,
                        first_ref,
                        ref_code,
                        owner_id,
                        journey_type,
                        campaign,
                        source,
                        content,
                        consent_scope,
                        json.dumps(context_patch, ensure_ascii=False),
                    ),
                )
                if first_ref:
                    await cur.execute(
                        """
                        insert into journey_attributions (
                          tenant_id, session_id, ref_code, attributed_owner_id,
                          journey_type, campaign, source, content, is_first_touch
                        ) values (%s, %s::uuid, %s, %s, %s, %s, %s, %s, true)
                        """,
                        (
                            tenant_id,
                            session_id,
                            first_ref,
                            owner_id,
                            journey_type,
                            campaign,
                            source,
                            content,
                        ),
                    )

    return {
        "ok": True,
        "visitor_session_id": session_id,
        "first_ref": first_ref,
        "current_ref": ref_code,
        "journey_type": journey_type,
        "created": not bool(existing),
    }
