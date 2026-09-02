from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from app.admin.audit import write_audit_log
from app.admin.auth.cookies import read_session_cookie
from app.admin.auth.service import validate_session
from app.db import fetch_one, get_pool
from app.settings import get_settings
from app.tenancy import TenantContext


@dataclass(frozen=True)
class AdminSession:
    session_id: str
    principal_id: str
    telegram_user_id: int
    role: str
    display_name: str | None
    allowed_tenant_ids: list[str] | None
    active_tenant_id: str | None


async def require_admin_session(request: Request) -> AdminSession:
    raw = read_session_cookie(request)
    if not raw:
        raise HTTPException(status_code=401, detail={"error": "admin_session_required"})

    row = await validate_session(raw)
    if not row:
        raise HTTPException(status_code=401, detail={"error": "admin_session_invalid"})

    allowed = row.get("allowed_tenant_ids")
    return AdminSession(
        session_id=str(row["session_id"]),
        principal_id=str(row["principal_id"]),
        telegram_user_id=int(row["telegram_user_id"]),
        role=str(row["role"]),
        display_name=row.get("display_name"),
        allowed_tenant_ids=list(allowed) if allowed else None,
        active_tenant_id=row.get("active_tenant_id"),
    )


def require_admin_roles(session: AdminSession, *roles: str) -> None:
    if session.role not in roles:
        raise HTTPException(status_code=403, detail={"error": "admin_role_forbidden"})


async def resolve_effective_tenant(
    request: Request,
    session: AdminSession,
    *,
    requested_tenant_id: str | None = None,
    audit_action: str = "admin_sensitive_view",
) -> str:
    host_tenant: str | None = None
    tenant_ctx = getattr(request.state, "tenant", None)
    if isinstance(tenant_ctx, TenantContext):
        host_tenant = tenant_ctx.tenant_id

    target = (requested_tenant_id or host_tenant or "").strip() or None
    if not target:
        raise HTTPException(status_code=400, detail={"error": "tenant_required"})

    if session.role == "super_admin":
        await _assert_tenant_exists(target)
        if host_tenant and target != host_tenant:
            await write_audit_log(
                principal_id=session.principal_id,
                action=audit_action,
                target_tenant_id=target,
                details={"host_tenant_id": host_tenant},
            )
        elif not host_tenant:
            await write_audit_log(
                principal_id=session.principal_id,
                action=audit_action,
                target_tenant_id=target,
                details={"source": "explicit_tenant"},
            )
        return target

    allowed = set(session.allowed_tenant_ids or [])
    if target not in allowed:
        raise HTTPException(status_code=403, detail={"error": "tenant_forbidden"})
    if host_tenant and host_tenant != target:
        raise HTTPException(status_code=403, detail={"error": "tenant_host_mismatch"})
    return target


async def _assert_tenant_exists(tenant_id: str) -> None:
    settings = get_settings()
    pool = get_pool()
    async with pool.connection(timeout=settings.database_timeout_sec) as conn:
        row = await fetch_one(
            conn,
            "select tenant_id, status from tenants where tenant_id = %s limit 1",
            (tenant_id,),
        )
    if not row or row.get("status") != "active":
        raise HTTPException(status_code=404, detail={"error": "tenant_not_found"})


def optional_host_tenant(request: Request) -> str | None:
    tenant_ctx = getattr(request.state, "tenant", None)
    if isinstance(tenant_ctx, TenantContext):
        return tenant_ctx.tenant_id
    return None
