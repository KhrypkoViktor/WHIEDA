from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from app.content_access.cookies import read_session_cookie
from app.content_access.service import validate_content_session
from app.partner_library.service import create_download, list_published_items
from app.partner_library.storage import LocalStorageBackend, get_storage_backend
from app.settings import get_settings
from app.subscriptions.service import resolve_partner_subscription_by_telegram_user_id
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["partner-library"])


async def _require_paid_session(request: Request) -> tuple[str, dict[str, Any]]:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    raw_session = read_session_cookie(request)
    if not raw_session:
        raise HTTPException(status_code=401, detail={"error": "content_session_required"})
    session = await validate_content_session(tenant.tenant_id, raw_session)
    if not session:
        raise HTTPException(status_code=401, detail={"error": "content_session_invalid"})
    telegram_user_id = session.get("telegram_user_id")
    subscription = None
    if telegram_user_id is not None:
        subscription = await resolve_partner_subscription_by_telegram_user_id(
            tenant.tenant_id,
            int(telegram_user_id),
        )
    if not subscription or not subscription.get("partner_paid"):
        raise HTTPException(status_code=403, detail={"error": "partner_paid_required"})
    return tenant.tenant_id, session


async def _items(request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    tenant_id, _ = await _require_paid_session(request)
    items = await list_published_items(tenant_id)
    return {
        "ok": True,
        "items": items,
        "links": [
            {
                "slug": "repeat-prices",
                "category": "repeat-prices",
                "title": "Повторные цены",
                "description": "Партнёрский прайс и калькулятор корзины.",
                "kind": "internal_link",
                "href": "/price/repeat/",
            }
        ],
    }


@router.get("/v1/partner-library/items")
async def items_v1(request: Request, response: Response) -> dict[str, Any]:
    return await _items(request, response)


@router.get("/api/v1/partner-library/items")
async def items_site(request: Request, response: Response) -> dict[str, Any]:
    return await _items(request, response)


async def _download(item_id: UUID, request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "private, no-store"
    tenant_id, _ = await _require_paid_session(request)
    ttl = get_settings().platform_partner_library_signed_url_ttl_seconds
    payload = await create_download(
        tenant_id,
        item_id,
        storage=get_storage_backend(),
        ttl=ttl,
    )
    if not payload:
        raise HTTPException(status_code=404, detail={"error": "library_item_not_found"})
    return payload


@router.get("/v1/partner-library/items/{item_id}/download")
async def download_v1(
    item_id: UUID,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    return await _download(item_id, request, response)


@router.get("/api/v1/partner-library/items/{item_id}/download")
async def download_site(
    item_id: UUID,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    return await _download(item_id, request, response)


@router.get("/api/v1/partner-library/local-files/{token}", include_in_schema=False)
async def local_file(token: str) -> FileResponse:
    storage = get_storage_backend()
    if not isinstance(storage, LocalStorageBackend):
        raise HTTPException(status_code=404, detail={"error": "not_found"})
    try:
        path = storage.open_signed_path(token)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail={"error": "signed_url_invalid"}) from exc
    return FileResponse(path, headers={"Cache-Control": "private, no-store"})
