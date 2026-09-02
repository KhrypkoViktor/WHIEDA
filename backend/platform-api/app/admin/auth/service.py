from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from app.admin.audit import write_audit_log
from app.db import admin_connection, fetch_one
from app.settings import get_settings

CHALLENGE_PREFIX = "adm_"
ADMIN_START_PREFIX = "admin_login_"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_value(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_admin_deep_link(bot_username: str, raw_challenge: str) -> str:
    username = bot_username.lstrip("@")
    return f"https://t.me/{username}?start={ADMIN_START_PREFIX}{raw_challenge}"


def extract_challenge_token(start_param: str) -> str:
    value = (start_param or "").strip()
    if value.startswith(ADMIN_START_PREFIX):
        return value[len(ADMIN_START_PREFIX) :]
    if value.startswith(CHALLENGE_PREFIX):
        return value[len(CHALLENGE_PREFIX) :]
    return value


async def create_login_challenge(*, browser_nonce: str) -> dict[str, Any]:
    nonce = browser_nonce.strip()
    if len(nonce) < 16:
        raise HTTPException(status_code=400, detail={"error": "invalid_browser_nonce"})

    settings = get_settings()
    if not settings.telegram_bot_username:
        raise HTTPException(status_code=503, detail={"error": "telegram_bot_username_not_configured"})

    raw_challenge = secrets.token_urlsafe(24)
    challenge_hash = _hash_value(raw_challenge)
    browser_nonce_hash = _hash_value(nonce)
    challenge_id = str(uuid.uuid4())
    expires_at = _utcnow() + timedelta(minutes=settings.platform_admin_challenge_ttl_minutes)

    async with admin_connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into platform_admin_login_challenges (
                  challenge_id, challenge_hash, browser_nonce_hash, expires_at
                ) values (%s::uuid, %s, %s, %s)
                """,
                (challenge_id, challenge_hash, browser_nonce_hash, expires_at),
            )

    return {
        "ok": True,
        "challenge_id": challenge_id,
        "expires_at": expires_at.isoformat(),
        "deep_link": build_admin_deep_link(settings.telegram_bot_username, raw_challenge),
        "poll_interval_sec": 2,
    }


async def confirm_login_from_telegram(
    *,
    challenge_token: str,
    telegram_user_id: int,
) -> dict[str, Any]:
    token = extract_challenge_token(challenge_token)
    if not token:
        raise HTTPException(status_code=400, detail={"error": "invalid_challenge_token"})

    challenge_hash = _hash_value(token)
    principal = await _resolve_or_bootstrap_principal(telegram_user_id)
    if principal is None:
        async with admin_connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update platform_admin_login_challenges
                    set status = 'rejected'
                    where challenge_hash = %s and status = 'pending'
                    """,
                    (challenge_hash,),
                )
        raise HTTPException(status_code=403, detail={"error": "admin_not_allowed"})

    async with admin_connection() as conn:
        row = await fetch_one(
            conn,
            """
            select challenge_id, status, expires_at, used_at
            from platform_admin_login_challenges
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
                    update platform_admin_login_challenges
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
                update platform_admin_login_challenges
                set status = 'approved',
                    principal_id = %s::uuid,
                    approved_at = now()
                where challenge_id = %s
                """,
                (principal["principal_id"], row["challenge_id"]),
            )

    await write_audit_log(
        principal_id=str(principal["principal_id"]),
        action="admin_login_approved",
        details={"telegram_user_id": telegram_user_id},
    )
    return {"ok": True, "challenge_id": str(row["challenge_id"]), "status": "approved"}


async def poll_login_challenge(
    *,
    challenge_id: str,
    browser_nonce: str,
) -> tuple[dict[str, Any], str | None]:
    """Return poll payload and raw session token when exchange succeeds."""
    nonce_hash = _hash_value(browser_nonce.strip())
    async with admin_connection() as conn:
        row = await fetch_one(
            conn,
            """
            select c.challenge_id, c.status, c.expires_at, c.used_at, c.browser_nonce_hash,
                     c.principal_id, p.role, p.status as principal_status
            from platform_admin_login_challenges c
            left join platform_admin_principals p on p.principal_id = c.principal_id
            where c.challenge_id = %s::uuid
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
                    update platform_admin_login_challenges
                    set status = 'expired'
                    where challenge_id = %s::uuid
                    """,
                    (challenge_id,),
                )
            return {"ok": True, "status": "expired"}, None

        if row.get("status") == "pending":
            return {"ok": True, "status": "pending"}, None
        if row.get("status") == "rejected":
            return {"ok": True, "status": "rejected"}, None
        if row.get("status") == "expired":
            return {"ok": True, "status": "expired"}, None
        if row.get("used_at") or row.get("status") == "used":
            return {"ok": True, "status": "used"}, None
        if row.get("status") != "approved":
            return {"ok": True, "status": row.get("status")}, None
        if row.get("principal_status") != "active":
            raise HTTPException(status_code=403, detail={"error": "principal_suspended"})
        if expires_at and expires_at < _utcnow():
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update platform_admin_login_challenges
                    set status = 'expired'
                    where challenge_id = %s::uuid and status = 'approved'
                    """,
                    (challenge_id,),
                )
            return {"ok": True, "status": "expired"}, None

        async with conn.cursor() as cur:
            await cur.execute(
                """
                update platform_admin_login_challenges
                set status = 'used', used_at = now()
                where challenge_id = %s::uuid
                  and status = 'approved'
                  and used_at is null
                returning challenge_id, principal_id
                """,
                (challenge_id,),
            )
            claimed = await cur.fetchone()

        if not claimed:
            return {"ok": True, "status": "used"}, None

        raw_session = secrets.token_urlsafe(32)
        session_hash = _hash_value(raw_session)
        settings = get_settings()
        session_expires = _utcnow() + timedelta(minutes=settings.platform_admin_session_ttl_minutes)
        session_id = str(uuid.uuid4())

        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into platform_admin_sessions (
                  session_id, session_hash, principal_id, expires_at
                ) values (%s::uuid, %s, %s::uuid, %s)
                """,
                (session_id, session_hash, claimed["principal_id"], session_expires),
            )

    await write_audit_log(
        principal_id=str(claimed["principal_id"]),
        action="admin_session_created",
        details={"session_id": session_id},
    )
    return {
        "ok": True,
        "status": "authenticated",
        "expires_at": session_expires.isoformat(),
    }, raw_session


async def validate_session(raw_session: str) -> dict[str, Any] | None:
    session_hash = _hash_value(raw_session.strip())
    async with admin_connection() as conn:
        row = await fetch_one(
            conn,
            """
            select s.session_id, s.principal_id, s.active_tenant_id, s.expires_at, s.revoked_at,
                   p.telegram_user_id, p.role, p.status, p.display_name, p.allowed_tenant_ids
            from platform_admin_sessions s
            join platform_admin_principals p on p.principal_id = s.principal_id
            where s.session_hash = %s
            limit 1
            """,
            (session_hash,),
        )
        if not row or row.get("revoked_at"):
            return None
        if row.get("expires_at") and row["expires_at"] < _utcnow():
            return None
        if row.get("status") != "active":
            return None

        async with conn.cursor() as cur:
            await cur.execute(
                """
                update platform_admin_sessions
                set last_seen_at = now()
                where session_id = %s::uuid
                """,
                (row["session_id"],),
            )
    return row


async def revoke_session(raw_session: str) -> bool:
    session_hash = _hash_value(raw_session.strip())
    async with admin_connection() as conn:
        row = await fetch_one(
            conn,
            """
            update platform_admin_sessions
            set revoked_at = now()
            where session_hash = %s and revoked_at is null
            returning session_id, principal_id
            """,
            (session_hash,),
        )
    if not row:
        return False
    await write_audit_log(
        principal_id=str(row["principal_id"]),
        action="admin_session_revoked",
        details={"session_id": str(row["session_id"])},
    )
    return True


async def load_principal(principal_id: str) -> dict[str, Any] | None:
    async with admin_connection() as conn:
        return await fetch_one(
            conn,
            """
            select principal_id, telegram_user_id, role, status, display_name,
                   allowed_tenant_ids, metadata, created_at, updated_at
            from platform_admin_principals
            where principal_id = %s::uuid
            limit 1
            """,
            (principal_id,),
        )


async def _resolve_or_bootstrap_principal(telegram_user_id: int) -> dict[str, Any] | None:
    settings = get_settings()
    super_ids = settings.parsed_super_admin_telegram_ids()

    async with admin_connection() as conn:
        existing = await fetch_one(
            conn,
            """
            select principal_id, telegram_user_id, role, status, display_name, allowed_tenant_ids
            from platform_admin_principals
            where telegram_user_id = %s
            limit 1
            """,
            (telegram_user_id,),
        )
        if existing:
            if existing.get("status") != "active":
                return None
            if telegram_user_id in super_ids and existing.get("role") != "super_admin":
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        update platform_admin_principals
                        set role = 'super_admin', updated_at = now()
                        where principal_id = %s::uuid
                        """,
                        (existing["principal_id"],),
                    )
                existing["role"] = "super_admin"
            return existing

        if telegram_user_id not in super_ids:
            return None

        principal_id = str(uuid.uuid4())
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into platform_admin_principals (
                  principal_id, telegram_user_id, role, status, display_name, metadata
                ) values (%s::uuid, %s, 'super_admin', 'active', %s, %s::jsonb)
                """,
                (
                    principal_id,
                    telegram_user_id,
                    "Platform Super Admin",
                    json.dumps({"bootstrap": "env_super_telegram_ids"}, ensure_ascii=False),
                ),
            )
        return await fetch_one(
            conn,
            """
            select principal_id, telegram_user_id, role, status, display_name, allowed_tenant_ids
            from platform_admin_principals
            where principal_id = %s::uuid
            """,
            (principal_id,),
        )


def format_me_payload(principal: dict[str, Any], *, effective_tenant_id: str | None) -> dict[str, Any]:
    allowed = principal.get("allowed_tenant_ids")
    scope = "all_tenants" if principal.get("role") == "super_admin" else "restricted"
    return {
        "ok": True,
        "principal_id": str(principal["principal_id"]),
        "role": principal["role"],
        "status": principal["status"],
        "display_name": principal.get("display_name"),
        "scope": scope,
        "allowed_tenant_ids": list(allowed) if allowed else None,
        "effective_tenant_id": effective_tenant_id,
        "telegram_user_id": int(principal["telegram_user_id"]),
    }
