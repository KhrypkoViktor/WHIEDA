from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.ref.service import format_public_ref, load_public_ref
from app.settings import get_settings
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["ref"])


@router.get("/v1/public/ref/{ref_code}")
async def public_ref_v1(ref_code: str, request: Request) -> dict:
    return await _public_ref(ref_code, request)


@router.get("/api/v1/public/ref/{ref_code}")
async def public_ref_site_alias(ref_code: str, request: Request) -> dict:
    return await _public_ref(ref_code, request)


async def _public_ref(ref_code: str, request: Request) -> dict:
    settings = get_settings()
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")

    if settings.core_route_public_ref == "legacy":
        raise HTTPException(
            status_code=501,
            detail={"error": "legacy_route_not_implemented_in_core"},
        )

    row = await load_public_ref(tenant.tenant_id, ref_code)
    if not row:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "ref_not_found"})

    payload = format_public_ref(row)
    if settings.core_route_public_ref == "shadow":
        request.state.shadow_public_ref = payload

    return payload
