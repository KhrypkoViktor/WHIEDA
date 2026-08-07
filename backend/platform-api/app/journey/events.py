"""Idempotent interaction event ingestion for site/Telegram routes."""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import HTTPException

from app.db import fetch_one, tenant_connection

ROUTE_EVENT_TYPES = frozenset(
    {
        "route_opened",
        "product_viewed",
        "useful_action_completed",
        "advisor_question",
        "telegram_link_created",
        "telegram_opened",
        "lead_created",
        "partner_contacted",
        "outcome_updated",
    }
)

INTERNAL_EVENT_TYPES = frozenset(
    {
        "telegram_link_exchanged",
    }
)

ALLOWED_EVENT_TYPES = ROUTE_EVENT_TYPES | INTERNAL_EVENT_TYPES

FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "owner_id",
        "assigned_owner_id",
        "attributed_owner_id",
        "telegram_user_id",
        "phone",
        "email",
        "contact",
    }
)

MAX_PAYLOAD_KEYS = 24


def _clean(value: Any, max_len: int = 240) -> str:
    return str(value or "").strip()[:max_len]


def _sanitize_payload(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    clean: dict[str, Any] = {}
    for key, value in raw.items():
        if key in FORBIDDEN_PAYLOAD_KEYS:
            continue
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[str(key)[:64]] = value if not isinstance(value, str) else value[:500]
        if len(clean) >= MAX_PAYLOAD_KEYS:
            break
    return clean


def parse_event_body(body: dict[str, Any]) -> dict[str, Any]:
    event_type = _clean(body.get("event_type"), 64)
    if event_type not in ALLOWED_EVENT_TYPES:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid_event_type"})

    idempotency_key = _clean(body.get("idempotency_key"), 160)
    if not idempotency_key:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "idempotency_key_required"})

    session_raw = _clean(body.get("visitor_session_id"), 64) or None
    if session_raw:
        try:
            uuid.UUID(session_raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid_session_id"}) from exc

    return {
        "event_type": event_type,
        "idempotency_key": idempotency_key,
        "visitor_session_id": session_raw,
        "payload": _sanitize_payload(body.get("payload")),
    }


async def record_interaction_event(tenant_id: str, parsed: dict[str, Any]) -> dict[str, Any]:
    event_id = str(uuid.uuid4())
    async with tenant_connection(tenant_id) as conn:
        existing = await fetch_one(
            conn,
            """
            select event_id, event_type, created_at
            from interaction_events
            where tenant_id = %s and idempotency_key = %s
            limit 1
            """,
            (tenant_id, parsed["idempotency_key"]),
        )
        if existing:
            return {
                "ok": True,
                "created": False,
                "event_id": str(existing["event_id"]),
                "event_type": existing["event_type"],
                "recorded_at": existing["created_at"].isoformat() if existing.get("created_at") else None,
            }

        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into interaction_events (
                  event_id, tenant_id, session_id, event_type, idempotency_key, payload
                ) values (%s::uuid, %s, %s::uuid, %s, %s, %s::jsonb)
                """,
                (
                    event_id,
                    tenant_id,
                    parsed.get("visitor_session_id"),
                    parsed["event_type"],
                    parsed["idempotency_key"],
                    json.dumps(parsed["payload"], ensure_ascii=False),
                ),
            )

    return {
        "ok": True,
        "created": True,
        "event_id": event_id,
        "event_type": parsed["event_type"],
    }
