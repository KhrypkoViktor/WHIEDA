from __future__ import annotations

from fastapi import APIRouter, Request
from starlette import status

from app.cart import service
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["cart"])


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


@router.post("/v1/cart-sessions")
@router.post("/api/v1/cart-sessions")
async def create_cart_session(request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    return await service.create_session(tenant.tenant_id, await _json_body(request))


@router.get("/v1/cart-sessions/{cart_session_id}")
@router.get("/api/v1/cart-sessions/{cart_session_id}")
async def get_cart_session(cart_session_id: str, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    return await service.get_session(tenant.tenant_id, cart_session_id)


@router.patch("/v1/cart-sessions/{cart_session_id}")
@router.patch("/api/v1/cart-sessions/{cart_session_id}")
async def patch_cart_session(cart_session_id: str, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    return await service.patch_session(tenant.tenant_id, cart_session_id, await _json_body(request))


@router.post("/v1/cart-sessions/{cart_session_id}/items")
@router.post("/api/v1/cart-sessions/{cart_session_id}/items")
async def upsert_cart_item(cart_session_id: str, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    return await service.upsert_item(tenant.tenant_id, cart_session_id, await _json_body(request))


@router.post("/v1/cart-sessions/{cart_session_id}/clear")
@router.post("/api/v1/cart-sessions/{cart_session_id}/clear")
async def clear_cart_session(cart_session_id: str, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    return await service.clear_session(tenant.tenant_id, cart_session_id)


@router.post("/v1/cart-sessions/{cart_session_id}/snapshot")
@router.post("/api/v1/cart-sessions/{cart_session_id}/snapshot")
async def snapshot_cart_session(cart_session_id: str, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    return await service.create_snapshot(tenant.tenant_id, cart_session_id)


@router.get("/v1/cart-snapshots/{token}")
@router.get("/api/v1/cart-snapshots/{token}")
async def get_cart_snapshot(token: str, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    return await service.get_snapshot(tenant.tenant_id, token)


@router.post("/v1/cart-sessions/{cart_session_id}/checkout", status_code=status.HTTP_201_CREATED)
@router.post("/api/v1/cart-sessions/{cart_session_id}/checkout", status_code=status.HTTP_201_CREATED)
async def checkout_cart_session(cart_session_id: str, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "partner_leads")
    return await service.checkout(tenant.tenant_id, cart_session_id, await _json_body(request))
