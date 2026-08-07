"""Confirmed long-term user facts (no raw transcript storage)."""

from __future__ import annotations

import json
import uuid
from typing import Any

from app.db import fetch_one, tenant_connection

ALLOWED_SUBJECT_TYPES = frozenset({"visitor_session", "telegram_user"})
MAX_FACT_KEY_LEN = 64


def _clean(value: Any, max_len: int = 240) -> str:
    return str(value or "").strip()[:max_len]


async def upsert_memory_fact(
    tenant_id: str,
    *,
    subject_type: str,
    subject_id: str,
    fact_key: str,
    fact_value: dict[str, Any],
    consent_scope: str | None = None,
    source: str = "confirmed",
) -> dict[str, Any]:
    if subject_type not in ALLOWED_SUBJECT_TYPES:
        raise ValueError("invalid_subject_type")
    key = _clean(fact_key, MAX_FACT_KEY_LEN)
    if not key or not subject_id:
        raise ValueError("subject_and_key_required")

    fact_id = str(uuid.uuid4())
    async with tenant_connection(tenant_id) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into user_memory_facts (
                  fact_id, tenant_id, subject_type, subject_id, fact_key,
                  fact_value, source, consent_scope
                ) values (
                  %s::uuid, %s, %s, %s, %s,
                  %s::jsonb, %s, %s
                )
                on conflict (tenant_id, subject_type, subject_id, fact_key) do update
                  set fact_value = excluded.fact_value,
                      source = excluded.source,
                      consent_scope = excluded.consent_scope,
                      confirmed_at = now()
                returning fact_id
                """,
                (
                    fact_id,
                    tenant_id,
                    subject_type,
                    subject_id,
                    key,
                    json.dumps(fact_value, ensure_ascii=False),
                    source,
                    consent_scope,
                ),
            )
            row = await cur.fetchone()
    return {"ok": True, "fact_id": str(row["fact_id"]) if row else fact_id, "fact_key": key}


async def list_memory_facts(
    tenant_id: str,
    *,
    subject_type: str,
    subject_id: str,
) -> list[dict[str, Any]]:
    async with tenant_connection(tenant_id) as conn:
        from app.db import fetch_all

        rows = await fetch_all(
            conn,
            """
            select fact_key, fact_value, source, confirmed_at
            from user_memory_facts
            where tenant_id = %s and subject_type = %s and subject_id = %s
            order by confirmed_at desc
            limit 50
            """,
            (tenant_id, subject_type, subject_id),
        )
    return [
        {
            "fact_key": row["fact_key"],
            "fact_value": row.get("fact_value") or {},
            "source": row.get("source"),
            "confirmed_at": row["confirmed_at"].isoformat() if row.get("confirmed_at") else None,
        }
        for row in rows
    ]
