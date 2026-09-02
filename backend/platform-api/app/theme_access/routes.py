from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.content_access.cookies import read_session_cookie
from app.content_access.service import validate_content_session
from app.settings import get_settings
from app.tenancy import get_request_tenant, normalize_host, require_entitlement
from app.theme_access.service import (
    entitlement_payload,
    load_theme_site,
    normalize_site_id,
    public_theme_payload,
    save_selected_theme,
    site_identity_aliases,
)

router = APIRouter(tags=["theme-access"])


def _temporary_free_flag() -> bool:
    return get_settings().temporary_free_for_verified_telegram_users


class ThemeSaveBody(BaseModel):
    site_id: str
    selected_theme_id: str


def site_id_from_request_host(request: Request) -> str:
    host = normalize_host(
        request.headers.get("x-wwc-personal-host")
        or request.headers.get("x-forwarded-host")
        or request.headers.get("host")
    )
    suffix = ".wwc.best"
    if not host.endswith(suffix):
        raise HTTPException(status_code=403, detail={"error": "personal_site_host_required"})
    site_id = host[: -len(suffix)]
    if "." in site_id:
        raise HTTPException(status_code=403, detail={"error": "personal_site_host_required"})
    return normalize_site_id(site_id)


async def _theme_site(request: Request, requested_site_id: str) -> tuple[str, dict]:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    host_site_id = site_id_from_request_host(request)
    site_id = normalize_site_id(requested_site_id)
    site = await load_theme_site(tenant.tenant_id, site_id)
    if not site:
        site = await load_theme_site(tenant.tenant_id, host_site_id)
    if not site:
        raise HTTPException(status_code=404, detail={"error": "site_not_found"})
    aliases = site_identity_aliases(site)
    if host_site_id not in aliases or site_id not in aliases:
        raise HTTPException(status_code=403, detail={"error": "site_host_mismatch"})
    return tenant.tenant_id, site


async def _current_telegram_user_id(request: Request) -> str:
    tenant = get_request_tenant(request)
    raw_session = read_session_cookie(request)
    if not raw_session:
        raise HTTPException(status_code=401, detail={"error": "content_session_required"})
    session = await validate_content_session(tenant.tenant_id, raw_session)
    if not session:
        raise HTTPException(status_code=401, detail={"error": "content_session_invalid"})
    return str(session.get("telegram_user_id") or "")


@router.get("/api/v1/theme-access/public")
async def get_public_theme(
    request: Request,
    site_id: str = Query(..., min_length=1, max_length=64),
) -> dict:
    _, site = await _theme_site(request, site_id)
    return public_theme_payload(site)


@router.get("/api/v1/theme-access/entitlement")
async def get_theme_entitlement(request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    site_id = site_id_from_request_host(request)
    site = await load_theme_site(tenant.tenant_id, site_id)
    if not site:
        raise HTTPException(status_code=404, detail={"error": "site_not_found"})
    telegram_user_id = await _current_telegram_user_id(request)
    return entitlement_payload(site, telegram_user_id, temporary_free=_temporary_free_flag())


@router.put("/api/v1/theme-access/entitlement")
async def put_theme_entitlement(body: ThemeSaveBody, request: Request) -> dict:
    tenant_id, _ = await _theme_site(request, body.site_id)
    telegram_user_id = await _current_telegram_user_id(request)
    return await save_selected_theme(
        tenant_id,
        site_id=body.site_id,
        telegram_user_id=telegram_user_id,
        theme_id=body.selected_theme_id,
        temporary_free=_temporary_free_flag(),
    )
