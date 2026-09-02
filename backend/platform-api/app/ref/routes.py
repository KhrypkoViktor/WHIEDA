from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from app.ref.service import (
    format_public_ref,
    load_public_ref,
    load_public_ref_by_subdomain,
)
from app.settings import get_settings
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["ref"])

RefLoader = Callable[[str, str], Awaitable[dict[str, Any] | None]]


@router.get("/v1/public/ref/{ref_code}")
async def public_ref_v1(ref_code: str, request: Request) -> dict:
    return await _public_ref(ref_code, request, load_public_ref)


@router.get("/api/v1/public/ref/{ref_code}")
async def public_ref_site_alias(ref_code: str, request: Request) -> dict:
    return await _public_ref(ref_code, request, load_public_ref)


@router.get("/v1/public/ref/by-subdomain/{subdomain}")
async def public_ref_by_subdomain_v1(subdomain: str, request: Request) -> dict:
    return await _public_ref(
        subdomain, request, load_public_ref_by_subdomain, not_found_error="subdomain_not_found"
    )


@router.get("/api/v1/public/ref/by-subdomain/{subdomain}")
async def public_ref_by_subdomain_site_alias(subdomain: str, request: Request) -> dict:
    return await _public_ref(
        subdomain, request, load_public_ref_by_subdomain, not_found_error="subdomain_not_found"
    )


async def _public_ref(
    ref_code: str,
    request: Request,
    loader: RefLoader,
    not_found_error: str = "ref_not_found",
) -> dict:
    settings = get_settings()
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")

    if settings.core_route_public_ref == "legacy":
        raise HTTPException(
            status_code=501,
            detail={"error": "legacy_route_not_implemented_in_core"},
        )

    row = await loader(tenant.tenant_id, ref_code)
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"ok": False, "error": not_found_error},
        )

    payload = format_public_ref(row)
    if settings.core_route_public_ref == "shadow":
        request.state.shadow_public_ref = payload

    return payload
