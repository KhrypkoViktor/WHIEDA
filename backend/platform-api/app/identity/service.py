"""Tenant-scoped visitor sessions and Telegram link tokens."""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from app.db import fetch_one, tenant_connection
from app.ref.service import load_public_ref

TOKEN_TTL_DAYS = 7
JOURNEY_TYPES = frozenset({"product", "business", "organic", "direct"})
FORBIDDEN_BODY_FIELDS = frozenset(
    {
        "owner_id",
        "assigned_owner_id",
        "attributed_owner_id",
        "telegram_user_id",
    }
)

SAFE_CONTEXT_KEYS = frozenset(
    {
        "last_product_sku",
        "last_product_name",
        "topic",
        "journey_type",
        "unresolved_clarification",
    }
)


@dataclass
class LinkTokenCreateResult:
    token_id: str
    visitor_session_id: str
    link_token: str
    deep_link: str
    expires_at: str
    first_ref: str | None
    attributed_owner_id: str | None
    mentor_display_name: str | None


@dataclass
class LinkTokenExchangeResult:
    link_id: str
    visitor_session_id: str
    first_ref: str | None
    attributed_owner_id: str | None
    mentor_display_name: str | None
    journey_type: str | None
    context: dict[str, Any]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _clean(value: Any, max_len: int = 240) -> str:
    return str(value or "").strip()[:max_len]


def _sanitize_context(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {key: raw[key] for key in SAFE_CONTEXT_KEYS if key in raw and raw[key] is not None}


def _validate_create_body(body: dict[str, Any]) -> None:
    for field in FORBIDDEN_BODY_FIELDS:
        if body.get(field):
            raise HTTPException(status_code=400, detail={"ok": False, "error": "forbidden_field"})


async def _resolve_owner_from_ref(tenant_id: str, ref_code: str | None) -> tuple[str | None, str | None]:
    if not ref_code:
        return None, None
    row = await load_public_ref(tenant_id, ref_code)
    if not row:
        return ref_code, None
    profile = row.get("public_profile") or {}
    return ref_code, str(row.get("owner_id") or "") or None


async def _persist_exchange_memory(
    tenant_id: str,
    *,
    session_id: str,
    telegram_user_id: int,
    context: dict[str, Any],
    first_ref: str | None,
    journey_type: str | None,
) -> None:
    """Seed confirmed facts from site context — no raw transcript."""
    from app.memory.service import upsert_memory_fact

    base = {
        "telegram_user_id": telegram_user_id,
        "linked_at": _utcnow().isoformat(),
    }
    if first_ref:
        base["first_ref"] = first_ref
    if journey_type:
        base["journey_type"] = journey_type
    await upsert_memory_fact(
        tenant_id,
        subject_type="visitor_session",
        subject_id=session_id,
        fact_key="telegram_link",
        fact_value=base,
        source="link_exchange",
    )
    if context.get("last_product_sku"):
        await upsert_memory_fact(
            tenant_id,
            subject_type="visitor_session",
            subject_id=session_id,
            fact_key="last_product",
            fact_value={
                "sku": context.get("last_product_sku"),
                "name": context.get("last_product_name"),
            },
            source="site_context",
        )


def build_deep_link(bot_username: str, raw_token: str) -> str:
    username = bot_username.lstrip("@")
    return f"https://t.me/{username}?start={raw_token}"


async def create_telegram_link_token(
    tenant_id: str,
    body: dict[str, Any],
    *,
    bot_username: str | None,
) -> LinkTokenCreateResult:
    _validate_create_body(body)
    if not bot_username:
        raise HTTPException(
            status_code=503,
            detail={"ok": False, "error": "telegram_bot_username_not_configured"},
        )

    ref_code = _clean(body.get("ref") or body.get("first_ref"), 64).lower() or None
    journey_type = _clean(body.get("journey_type") or "organic", 32).lower()
    if journey_type not in JOURNEY_TYPES:
        journey_type = "organic"

    session_raw = _clean(body.get("visitor_session_id"), 64)
    session_id = session_raw or str(uuid.uuid4())
    try:
        uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid_session_id"}) from exc

    context = _sanitize_context(body.get("context"))
    campaign = _clean(body.get("campaign"), 120) or None
    source = _clean(body.get("source"), 120) or None
    content = _clean(body.get("content"), 120) or None
    consent_scope = _clean(body.get("consent_scope"), 120) or None

    ref_code, owner_id = await _resolve_owner_from_ref(tenant_id, ref_code)
    mentor_name = None
    if ref_code:
        public = await load_public_ref(tenant_id, ref_code)
        if public:
            mentor_name = (public.get("public_profile") or {}).get("display_name")

    raw_token = secrets.token_urlsafe(24)
    token_hash = _hash_token(raw_token)
    expires_at = _utcnow() + timedelta(days=TOKEN_TTL_DAYS)
    token_id = str(uuid.uuid4())
    first_ref: str | None = ref_code

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

        if existing:
            first_ref = existing.get("first_ref") or ref_code
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
                        json.dumps(context, ensure_ascii=False),
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
                        json.dumps(context, ensure_ascii=False),
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

        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into identity_link_tokens (
                  token_id, tenant_id, session_id, token_hash, expires_at
                ) values (%s::uuid, %s, %s::uuid, %s, %s)
                """,
                (token_id, tenant_id, session_id, token_hash, expires_at),
            )

            idempotency = _clean(body.get("idempotency_key"), 160) or f"telegram_link_created:{token_id}"
            await cur.execute(
                """
                insert into interaction_events (
                  tenant_id, session_id, event_type, idempotency_key, payload
                ) values (%s, %s::uuid, 'telegram_link_created', %s, %s::jsonb)
                on conflict (tenant_id, idempotency_key) do nothing
                """,
                (
                    tenant_id,
                    session_id,
                    idempotency,
                    json.dumps({"token_id": token_id, "journey_type": journey_type}, ensure_ascii=False),
                ),
            )

    return LinkTokenCreateResult(
        token_id=token_id,
        visitor_session_id=session_id,
        link_token=raw_token,
        deep_link=build_deep_link(bot_username, raw_token),
        expires_at=expires_at.isoformat(),
        first_ref=first_ref,
        attributed_owner_id=owner_id,
        mentor_display_name=mentor_name,
    )


async def exchange_telegram_link_token(
    tenant_id: str,
    raw_token: str,
    *,
    telegram_user_id: int,
    telegram_chat_id: int | None = None,
) -> LinkTokenExchangeResult:
    token_hash = _hash_token(raw_token.strip())
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select t.token_id, t.session_id, t.expires_at, t.used_at,
                     v.first_ref, v.attributed_owner_id, v.assigned_owner_id,
                     v.journey_type, v.context
            from identity_link_tokens t
            join visitor_sessions v
              on v.tenant_id = t.tenant_id and v.session_id = t.session_id
            where t.tenant_id = %s and t.token_hash = %s
            limit 1
            """,
            (tenant_id, token_hash),
        )
        if not row:
            raise HTTPException(status_code=404, detail={"ok": False, "error": "token_not_found"})

        if row.get("used_at"):
            raise HTTPException(status_code=409, detail={"ok": False, "error": "token_already_used"})

        expires_at = row.get("expires_at")
        if expires_at and expires_at < _utcnow():
            raise HTTPException(status_code=410, detail={"ok": False, "error": "token_expired"})

        session_id = str(row["session_id"])
        context = row.get("context") or {}
        if isinstance(context, str):
            try:
                context = json.loads(context)
            except json.JSONDecodeError:
                context = {}

        async with conn.cursor() as cur:
            await cur.execute(
                """
                update identity_link_tokens
                set used_at = now(), used_by_telegram_user_id = %s
                where tenant_id = %s and token_id = %s
                """,
                (telegram_user_id, tenant_id, row["token_id"]),
            )

            link_id = str(uuid.uuid4())
            await cur.execute(
                """
                insert into telegram_identity_links (
                  link_id, tenant_id, session_id, telegram_user_id, telegram_chat_id,
                  first_ref, attributed_owner_id, assigned_owner_id, journey_type,
                  context_snapshot
                ) values (
                  %s::uuid, %s, %s::uuid, %s, %s,
                  %s, %s, %s, %s,
                  %s::jsonb
                )
                on conflict (tenant_id, telegram_user_id) do update
                  set session_id = excluded.session_id,
                      telegram_chat_id = excluded.telegram_chat_id,
                      context_snapshot = excluded.context_snapshot
                """,
                (
                    link_id,
                    tenant_id,
                    session_id,
                    telegram_user_id,
                    telegram_chat_id,
                    row.get("first_ref"),
                    row.get("attributed_owner_id"),
                    row.get("assigned_owner_id"),
                    row.get("journey_type"),
                    json.dumps(context, ensure_ascii=False),
                ),
            )

            await cur.execute(
                """
                insert into interaction_events (
                  tenant_id, session_id, event_type, idempotency_key, payload
                ) values (%s, %s::uuid, 'telegram_link_exchanged', %s, %s::jsonb)
                on conflict (tenant_id, idempotency_key) do nothing
                """,
                (
                    tenant_id,
                    session_id,
                    f"telegram_link_exchanged:{token_hash}",
                    json.dumps({"telegram_user_id": telegram_user_id}, ensure_ascii=False),
                ),
            )

    mentor_name = None
    first_ref = row.get("first_ref")
    if first_ref:
        public = await load_public_ref(tenant_id, str(first_ref))
        if public:
            mentor_name = (public.get("public_profile") or {}).get("display_name")

    await _persist_exchange_memory(
        tenant_id,
        session_id=session_id,
        telegram_user_id=telegram_user_id,
        context=context if isinstance(context, dict) else {},
        first_ref=str(first_ref) if first_ref else None,
        journey_type=row.get("journey_type"),
    )

    return LinkTokenExchangeResult(
        link_id=link_id,
        visitor_session_id=session_id,
        first_ref=str(first_ref) if first_ref else None,
        attributed_owner_id=row.get("attributed_owner_id"),
        mentor_display_name=mentor_name,
        journey_type=row.get("journey_type"),
        context=context if isinstance(context, dict) else {},
    )
