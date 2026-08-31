"""Browser challenge + public content session. Separate from admin auth."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException

from app.db import fetch_one, tenant_connection
from app.settings import get_settings

CONTENT_START_PREFIX = "content_access_"
ALLOWED_SCOPES = frozenset({"telegram_verified"})
SCOPE_ALIASES = {"theme_customization": "telegram_verified"}
ALLOWED_RETURN_PREFIXES = (
    "/articles/",
    "/reviews",
    "/partner/",
    "/catalog/",
    "/settings/",
    "/en/",
)
ALLOWED_RETURN_EXACT = frozenset({"/", "/en", "/en/"})
CONTENT_KEY_RE = re.compile(r"^[a-z0-9]+(?:/[a-z0-9._-]+){1,6}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_value(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_content_deep_link(bot_username: str, raw_challenge: str) -> str:
    username = bot_username.lstrip("@")
    return f"https://t.me/{username}?start={CONTENT_START_PREFIX}{raw_challenge}"


def extract_challenge_token(start_param: str) -> str:
    value = (start_param or "").strip()
    if value.startswith(CONTENT_START_PREFIX):
        return value[len(CONTENT_START_PREFIX) :]
    return value


def sanitize_return_to(raw: str) -> str:
    value = str(raw or "").strip()
    if not value.startswith("/") or value.startswith("//"):
        raise HTTPException(status_code=400, detail={"error": "invalid_return_to"})
    if any(ch in value for ch in ("\\", "\x00")):
        raise HTTPException(status_code=400, detail={"error": "invalid_return_to"})
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        raise HTTPException(status_code=400, detail={"error": "invalid_return_to"})
    path = parsed.path or ""
    lowered = path.lower()
    if lowered.startswith(("/cabinet", "/admin", "/api")):
        raise HTTPException(status_code=400, detail={"error": "invalid_return_to"})
    if path not in ALLOWED_RETURN_EXACT and not any(
        path.startswith(prefix) for prefix in ALLOWED_RETURN_PREFIXES
    ):
        raise HTTPException(status_code=400, detail={"error": "invalid_return_to"})
    if len(value) > 500:
        raise HTTPException(status_code=400, detail={"error": "invalid_return_to"})
    return value


def sanitize_content_key(raw: str) -> str:
    value = str(raw or "").strip().lower()
    if ".." in value or value.startswith("/") or not CONTENT_KEY_RE.match(value):
        raise HTTPException(status_code=400, detail={"error": "invalid_content_key"})
    return value


def normalize_content_scope(raw: str | None) -> str:
    value = str(raw or "telegram_verified").strip() or "telegram_verified"
    value = SCOPE_ALIASES.get(value, value)
    if value not in ALLOWED_SCOPES:
        raise HTTPException(status_code=400, detail={"error": "invalid_scope"})
    return value


def _scope(raw: str | None) -> str:
    return normalize_content_scope(raw)


async def create_content_challenge(
    tenant_id: str,
    *,
    browser_nonce: str,
    return_to: str,
    scope: str | None = None,
    visitor_session_id: str | None = None,
    ref: str | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    nonce = browser_nonce.strip()
    if len(nonce) < 16:
        raise HTTPException(status_code=400, detail={"error": "invalid_browser_nonce"})

    settings = get_settings()
    if not settings.telegram_bot_username:
        raise HTTPException(status_code=503, detail={"error": "telegram_bot_username_not_configured"})

    safe_return = sanitize_return_to(return_to)
    requested_scope = _scope(scope)
    session_raw = str(visitor_session_id or "").strip() or str(uuid.uuid4())
    try:
        uuid.UUID(session_raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_session_id"}) from exc

    raw_challenge = secrets.token_urlsafe(24)
    challenge_hash = _hash_value(raw_challenge)
    browser_nonce_hash = _hash_value(nonce)
    challenge_id = str(uuid.uuid4())
    expires_at = _utcnow() + timedelta(minutes=settings.platform_content_challenge_ttl_minutes)
    ref_code = str(ref or "").strip()[:64].lower() or None
    safe_context = json.dumps(context or {}, ensure_ascii=False) if isinstance(context, dict) else "{}"

    async with tenant_connection(tenant_id) as conn:
        existing = await fetch_one(
            conn,
            """
            select session_id from visitor_sessions
            where tenant_id = %s and session_id = %s::uuid
            limit 1
            """,
            (tenant_id, session_raw),
        )
        async with conn.cursor() as cur:
            if not existing:
                await cur.execute(
                    """
                    insert into visitor_sessions (
                      tenant_id, session_id, first_ref, current_ref, journey_type, context
                    ) values (%s, %s::uuid, %s, %s, 'organic', %s::jsonb)
                    """,
                    (tenant_id, session_raw, ref_code, ref_code, safe_context),
                )
            await cur.execute(
                """
                insert into content_access_challenges (
                  challenge_id, tenant_id, visitor_session_id, challenge_hash,
                  browser_nonce_hash, return_to, requested_scope, expires_at
                ) values (
                  %s::uuid, %s, %s::uuid, %s,
                  %s, %s, %s, %s
                )
                """,
                (
                    challenge_id,
                    tenant_id,
                    session_raw,
                    challenge_hash,
                    browser_nonce_hash,
                    safe_return,
                    requested_scope,
                    expires_at,
                ),
            )

    return {
        "ok": True,
        "challenge_id": challenge_id,
        "expires_at": expires_at.isoformat(),
        "deep_link": build_content_deep_link(settings.telegram_bot_username, raw_challenge),
        "poll_interval_sec": 2,
    }


async def confirm_content_from_telegram(
    *,
    tenant_id: str,
    challenge_token: str,
    telegram_user_id: int,
    telegram_chat_id: int | None = None,
) -> dict[str, Any]:
    token = extract_challenge_token(challenge_token)
    if not token:
        raise HTTPException(status_code=400, detail={"error": "invalid_challenge_token"})

    challenge_hash = _hash_value(token)
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select challenge_id, status, expires_at, used_at, visitor_session_id, requested_scope
            from content_access_challenges
            where challenge_hash = %s
            limit 1
            """,
            (challenge_hash,),
        )
        if not row:
            raise HTTPException(status_code=404, detail={"error": "challenge_not_found"})
        if row.get("used_at") or row.get("status") == "used":
            raise HTTPException(status_code=409, detail={"error": "challenge_already_used"})
        expires_at = row.get("expires_at")
        if expires_at and expires_at < _utcnow():
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update content_access_challenges
                    set status = 'expired'
                    where challenge_id = %s
                    """,
                    (row["challenge_id"],),
                )
            raise HTTPException(status_code=410, detail={"error": "challenge_expired"})
        if row.get("status") not in {"pending", "approved"}:
            raise HTTPException(status_code=409, detail={"error": "challenge_not_pending"})

        async with conn.cursor() as cur:
            await cur.execute(
                """
                update content_access_challenges
                set status = 'approved',
                    telegram_user_id = %s,
                    approved_at = now()
                where challenge_id = %s
                """,
                (telegram_user_id, row["challenge_id"]),
            )
            await cur.execute(
                """
                insert into telegram_identity_links (
                  link_id, tenant_id, session_id, telegram_user_id, telegram_chat_id,
                  journey_type, context_snapshot
                ) values (
                  %s::uuid, %s, %s::uuid, %s, %s,
                  'organic', '{}'::jsonb
                )
                on conflict (tenant_id, telegram_user_id) do update
                  set session_id = excluded.session_id,
                      telegram_chat_id = excluded.telegram_chat_id
                """,
                (
                    str(uuid.uuid4()),
                    tenant_id,
                    str(row["visitor_session_id"]),
                    telegram_user_id,
                    telegram_chat_id,
                ),
            )

    return {"ok": True, "challenge_id": str(row["challenge_id"]), "status": "approved"}


async def poll_content_challenge(
    tenant_id: str,
    *,
    challenge_id: str,
    browser_nonce: str,
) -> tuple[dict[str, Any], str | None]:
    try:
        uuid.UUID(challenge_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_challenge_id"}) from exc

    nonce_hash = _hash_value(browser_nonce.strip())
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select challenge_id, status, expires_at, used_at, browser_nonce_hash,
                   visitor_session_id, telegram_user_id, requested_scope
            from content_access_challenges
            where challenge_id = %s::uuid
            limit 1
            """,
            (challenge_id,),
        )
        if not row:
            raise HTTPException(status_code=404, detail={"error": "challenge_not_found"})
        if row.get("browser_nonce_hash") != nonce_hash:
            raise HTTPException(status_code=403, detail={"error": "invalid_browser_nonce"})

        expires_at = row.get("expires_at")
        if expires_at and expires_at < _utcnow() and row.get("status") == "pending":
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update content_access_challenges
                    set status = 'expired'
                    where challenge_id = %s::uuid
                    """,
                    (challenge_id,),
                )
            return {"ok": True, "status": "expired"}, None

        if row.get("status") == "pending":
            return {"ok": True, "status": "pending"}, None
        if row.get("status") in {"rejected", "expired", "used"}:
            return {"ok": True, "status": row["status"]}, None
        if row.get("used_at"):
            return {"ok": True, "status": "used"}, None
        if row.get("status") != "approved":
            return {"ok": True, "status": str(row.get("status"))}, None
        if expires_at and expires_at < _utcnow():
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update content_access_challenges
                    set status = 'expired'
                    where challenge_id = %s::uuid and status = 'approved'
                    """,
                    (challenge_id,),
                )
            return {"ok": True, "status": "expired"}, None

        async with conn.cursor() as cur:
            await cur.execute(
                """
                update content_access_challenges
                set status = 'used', used_at = now()
                where challenge_id = %s::uuid
                  and status = 'approved'
                  and used_at is null
                returning challenge_id, visitor_session_id, telegram_user_id, requested_scope
                """,
                (challenge_id,),
            )
            claimed = await cur.fetchone()

        if not claimed:
            return {"ok": True, "status": "used"}, None

        raw_session = secrets.token_urlsafe(32)
        session_hash = _hash_value(raw_session)
        settings = get_settings()
        session_expires = _utcnow() + timedelta(days=settings.platform_content_session_ttl_days)
        session_id = str(uuid.uuid4())

        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into content_access_sessions (
                  session_id, tenant_id, visitor_session_id, session_hash,
                  telegram_user_id, scope, expires_at
                ) values (
                  %s::uuid, %s, %s::uuid, %s,
                  %s, %s, %s
                )
                """,
                (
                    session_id,
                    tenant_id,
                    str(claimed["visitor_session_id"]),
                    session_hash,
                    claimed["telegram_user_id"],
                    claimed.get("requested_scope") or "telegram_verified",
                    session_expires,
                ),
            )

    return {
        "ok": True,
        "status": "authenticated",
        "expires_at": session_expires.isoformat(),
    }, raw_session


async def validate_content_session(tenant_id: str, raw_session: str) -> dict[str, Any] | None:
    session_hash = _hash_value(raw_session.strip())
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select session_id, tenant_id, visitor_session_id, telegram_user_id,
                   scope, expires_at, revoked_at
            from content_access_sessions
            where session_hash = %s
            limit 1
            """,
            (session_hash,),
        )
        if not row or row.get("revoked_at"):
            return None
        if str(row.get("tenant_id")) != tenant_id:
            return None
        if row.get("expires_at") and row["expires_at"] < _utcnow():
            return None
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update content_access_sessions
                set last_seen_at = now()
                where session_id = %s::uuid
                """,
                (row["session_id"],),
            )
    return row


def format_me_payload(session: dict[str, Any]) -> dict[str, Any]:
    expires_at = session.get("expires_at")
    return {
        "ok": True,
        "scope": session.get("scope") or "telegram_verified",
        "expires_at": expires_at.isoformat() if hasattr(expires_at, "isoformat") else expires_at,
    }


async def revoke_content_session(tenant_id: str, raw_session: str) -> bool:
    session_hash = _hash_value(raw_session.strip())
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update content_access_sessions
            set revoked_at = now()
            where session_hash = %s and tenant_id = %s and revoked_at is null
            returning session_id
            """,
            (session_hash, tenant_id),
        )
    return bool(row)


async def load_material(tenant_id: str, content_key: str, *, scope: str) -> dict[str, Any]:
    key = sanitize_content_key(content_key)
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select content_key, scope, status, kind, title, body_html
            from content_access_materials
            where tenant_id = %s and content_key = %s
            limit 1
            """,
            (tenant_id, key),
        )
    if not row or row.get("status") != "published":
        raise HTTPException(status_code=404, detail={"error": "material_not_found"})
    required = str(row.get("scope") or "telegram_verified")
    if required != scope:
        raise HTTPException(status_code=403, detail={"error": "scope_denied"})
    return {
        "ok": True,
        "content_key": row["content_key"],
        "kind": row["kind"],
        "scope": required,
        "title": row.get("title"),
        "body_html": row["body_html"],
    }
