from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.db import fetch_one, get_pool
from app.settings import get_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TenantContext:
    tenant_id: str
    status: str
    display_name: str
    entitlements: dict[str, bool]


def normalize_host(host: str | None) -> str:
    if not host:
        return ""
    value = host.strip().lower()
    if ":" in value:
        value = value.split(":", 1)[0]
    return value


async def resolve_tenant_from_host(host: str) -> TenantContext:
    normalized = normalize_host(host)
    if not normalized:
        raise HTTPException(status_code=404, detail={"error": "tenant_not_found"})

    settings = get_settings()
    pool = get_pool()
    async with pool.connection(timeout=settings.database_timeout_sec) as conn:
        row = await fetch_one(
            conn,
            """
            select t.tenant_id, t.status, t.display_name
            from tenant_domains d
            join tenants t on t.tenant_id = d.tenant_id
            where d.domain = %s
              and d.is_active = true
            limit 1
            """,
            (normalized,),
        )

    if not row:
        if settings.default_host_tenant and settings.environment != "production":
            return await _load_tenant(settings.default_host_tenant)
        raise HTTPException(status_code=404, detail={"error": "tenant_not_found"})

    if row["status"] == "suspended":
        raise HTTPException(status_code=503, detail={"error": "tenant_suspended"})
    if row["status"] != "active":
        raise HTTPException(status_code=404, detail={"error": "tenant_not_found"})

    entitlements = await _load_entitlements(row["tenant_id"])
    return TenantContext(
        tenant_id=row["tenant_id"],
        status=row["status"],
        display_name=row["display_name"],
        entitlements=entitlements,
    )


async def resolve_tenant_from_bot_binding(binding_id: str) -> TenantContext:
    settings = get_settings()
    pool = get_pool()
    async with pool.connection(timeout=settings.database_timeout_sec) as conn:
        row = await fetch_one(
            conn,
            """
            select t.tenant_id, t.status, t.display_name, b.status as binding_status
            from tenant_bot_bindings b
            join tenants t on t.tenant_id = b.tenant_id
            where b.binding_id = %s
            limit 1
            """,
            (binding_id,),
        )

    if not row or row["binding_status"] != "active":
        raise HTTPException(status_code=404, detail={"error": "bot_binding_not_found"})
    if row["status"] == "suspended":
        raise HTTPException(status_code=503, detail={"error": "tenant_suspended"})
    if row["status"] != "active":
        raise HTTPException(status_code=404, detail={"error": "bot_binding_not_found"})

    entitlements = await _load_entitlements(row["tenant_id"])
    return TenantContext(
        tenant_id=row["tenant_id"],
        status=row["status"],
        display_name=row["display_name"],
        entitlements=entitlements,
    )


async def _load_tenant(tenant_id: str) -> TenantContext:
    settings = get_settings()
    pool = get_pool()
    async with pool.connection(timeout=settings.database_timeout_sec) as conn:
        row = await fetch_one(
            conn,
            "select tenant_id, status, display_name from tenants where tenant_id = %s",
            (tenant_id,),
        )
    if not row or row["status"] != "active":
        raise HTTPException(status_code=404, detail={"error": "tenant_not_found"})
    entitlements = await _load_entitlements(row["tenant_id"])
    return TenantContext(
        tenant_id=row["tenant_id"],
        status=row["status"],
        display_name=row["display_name"],
        entitlements=entitlements,
    )


async def _load_entitlements(tenant_id: str) -> dict[str, bool]:
    settings = get_settings()
    pool = get_pool()
    async with pool.connection(timeout=settings.database_timeout_sec) as conn:
        rows = await fetch_all_entitlements(conn, tenant_id)
    return {row["feature_key"]: bool(row["enabled"]) for row in rows}


async def fetch_all_entitlements(conn: Any, tenant_id: str) -> list[dict[str, Any]]:
    from app.db import fetch_all

    return await fetch_all(
        conn,
        """
        select feature_key, enabled
        from tenant_entitlements
        where tenant_id = %s
        """,
        (tenant_id,),
    )


def require_entitlement(tenant: TenantContext, feature_key: str) -> None:
    if not tenant.entitlements.get(feature_key, False):
        raise HTTPException(status_code=403, detail={"error": "feature_disabled"})


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        if path.startswith("/health"):
            return await call_next(request)

        if path.startswith("/v1/telegram/") or path.startswith("/v1/max/"):
            # Webhooks мессенджеров приходят с их хостов, тенант задаёт сам маршрут.
            return await call_next(request)

        if path == "/v1/admin/auth/telegram-confirm":
            return await call_next(request)

        if path.startswith("/v1/admin/"):
            host = request.headers.get("x-forwarded-host") or request.headers.get("host")
            tenant = await _resolve_optional_tenant_from_host(host or "")
            if tenant is not None:
                request.state.tenant = tenant
            return await call_next(request)

        host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        try:
            tenant = await resolve_tenant_from_host(host or "")
        except HTTPException as exc:
            from fastapi.responses import JSONResponse

            detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
            return JSONResponse(status_code=exc.status_code, content=detail)

        request.state.tenant = tenant
        return await call_next(request)


async def _resolve_optional_tenant_from_host(host: str) -> TenantContext | None:
    normalized = normalize_host(host)
    cabinet_hosts = {
        "admin-staging.wwc.best": "whieda",
        "cabinet.staging.wwc.best": "whieda",
        "cabinet.test.local": "whieda",
    }
    if normalized in cabinet_hosts:
        return await _load_tenant(cabinet_hosts[normalized])
    settings = get_settings()
    if not normalized:
        if settings.default_host_tenant and settings.environment != "production":
            return await _load_tenant(settings.default_host_tenant)
        return None
    try:
        return await resolve_tenant_from_host(host)
    except HTTPException:
        return None


def get_request_tenant(request: Request) -> TenantContext:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(status_code=500, detail={"error": "tenant_context_missing"})
    return tenant


def get_trace_id(request: Request) -> str:
    trace_id = getattr(request.state, "trace_id", None)
    if trace_id:
        return trace_id
    return str(uuid.uuid4())
