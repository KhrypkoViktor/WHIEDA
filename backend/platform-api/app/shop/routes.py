"""Site API for «Мастерская WWC» (03.10.2026).

Public catalog: ``GET /api/v1/public/shop`` — ``published`` for everyone; with a
content-access session of a preview admin (owner, super admins) also ``pilot``.
A guest's answer is cached (``public, max-age=60``), an answer that depends on
the session is not (``private, no-store``).

The buyer (content-access session, Telegram sign-in on the site):
``GET /api/v1/content-access/me/purchases`` and
``GET /api/v1/content-access/shop/files/{item_code}`` (a signed link, 1 hour).

The owner's catalog screen (preview admins): ``GET/POST
/api/v1/content-access/shop/admin/items`` and ``PATCH …/items/{code}``.

Every route answers on ``/api/v1/…`` and ``/v1/…``: production proxies the
first, the staging nginx strips ``/api`` (content_access/routes.py).
"""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import APIRouter, HTTPException, Request, Response

from app.content_access.cookies import read_session_cookie
from app.content_access.routes import _current_session
from app.content_access.service import validate_content_session
from app.shop.service import (
    ShopError,
    admin_catalog,
    admin_item_out,
    create_item,
    file_link,
    is_shop_admin,
    list_purchases,
    public_catalog,
    update_item,
)
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["shop"])

PUBLIC_CACHE = "public, max-age=60"
NO_STORE = "private, no-store"


def _raise(exc: ShopError) -> NoReturn:
    raise HTTPException(status_code=exc.status, detail={"error": exc.code, **exc.extra}) from exc


async def _optional_viewer(request: Request) -> int | None:
    """Telegram id from the content-access session, if any; a guest is None."""
    raw = read_session_cookie(request)
    if not raw:
        return None
    tenant = get_request_tenant(request)
    session = await validate_content_session(tenant.tenant_id, raw)
    value = (session or {}).get("telegram_user_id")
    return int(value) if value is not None else None


async def _viewer(request: Request, response: Response) -> tuple[str, int]:
    response.headers["Cache-Control"] = NO_STORE
    session = await _current_session(request)
    telegram_user_id = session.get("telegram_user_id")
    if telegram_user_id is None:
        raise HTTPException(status_code=401, detail={"error": "telegram_session_required"})
    return get_request_tenant(request).tenant_id, int(telegram_user_id)


async def _admin(request: Request, response: Response) -> tuple[str, int]:
    tenant_id, viewer = await _viewer(request, response)
    if not is_shop_admin(viewer):
        raise HTTPException(status_code=403, detail={"error": "shop_admin_required"})
    return tenant_id, viewer


def _both(path: str) -> list[str]:
    return [f"/api{path}", path]


# ---- public catalog --------------------------------------------------------------------


async def public_shop(request: Request, response: Response) -> dict[str, Any]:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    viewer = await _optional_viewer(request)
    payload = await public_catalog(tenant.tenant_id, viewer_id=viewer)
    # Ответ зависит от сессии (pilot у админа, подпись обложек) — общий кэш только гостю.
    response.headers["Cache-Control"] = PUBLIC_CACHE if viewer is None else NO_STORE
    response.headers["Vary"] = "Cookie"
    return payload


for _path in _both("/v1/public/shop"):
    router.add_api_route(_path, public_shop, methods=["GET"])


# ---- the buyer -------------------------------------------------------------------------


async def my_purchases(request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    return await list_purchases(tenant_id, viewer)


async def shop_file(item_code: str, request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _viewer(request, response)
    try:
        return await file_link(tenant_id, item_code, viewer)
    except ShopError as exc:
        _raise(exc)


for _path in _both("/v1/content-access/me/purchases"):
    router.add_api_route(_path, my_purchases, methods=["GET"])
for _path in _both("/v1/content-access/shop/files/{item_code}"):
    router.add_api_route(_path, shop_file, methods=["GET"])


# ---- the owner's catalog screen --------------------------------------------------------


async def admin_items(request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _admin(request, response)
    return await admin_catalog(tenant_id, viewer_id=viewer)


async def admin_create_item(body: dict[str, Any], request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _admin(request, response)
    try:
        item = await create_item(tenant_id, body, updated_by=viewer)
    except ShopError as exc:
        _raise(exc)
    return {"ok": True, "item": await admin_item_out(tenant_id, item, viewer_id=viewer)}


async def admin_update_item(code: str, body: dict[str, Any], request: Request, response: Response) -> dict[str, Any]:
    tenant_id, viewer = await _admin(request, response)
    try:
        item = await update_item(tenant_id, code, body, updated_by=viewer)
    except ShopError as exc:
        _raise(exc)
    return {"ok": True, "item": await admin_item_out(tenant_id, item, viewer_id=viewer)}


for _path in _both("/v1/content-access/shop/admin/items"):
    router.add_api_route(_path, admin_items, methods=["GET"])
    router.add_api_route(_path, admin_create_item, methods=["POST"])
for _path in _both("/v1/content-access/shop/admin/items/{code}"):
    router.add_api_route(_path, admin_update_item, methods=["PATCH"])
