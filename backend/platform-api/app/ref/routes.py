from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request, Response

from app.public_cache import PUBLIC_CACHE_TTL_SEC, mark_public, no_store_error, public_ref_cache
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
async def public_ref_v1(ref_code: str, request: Request, response: Response) -> dict:
    return await _public_ref(ref_code, request, response, load_public_ref)


@router.get("/api/v1/public/ref/{ref_code}")
async def public_ref_site_alias(ref_code: str, request: Request, response: Response) -> dict:
    return await _public_ref(ref_code, request, response, load_public_ref)


@router.get("/v1/public/ref/by-subdomain/{subdomain}")
async def public_ref_by_subdomain_v1(subdomain: str, request: Request, response: Response) -> dict:
    return await _public_ref(subdomain, request, response, load_public_ref_by_subdomain, kind="subdomain")


@router.get("/api/v1/public/ref/by-subdomain/{subdomain}")
async def public_ref_by_subdomain_site_alias(subdomain: str, request: Request, response: Response) -> dict:
    return await _public_ref(subdomain, request, response, load_public_ref_by_subdomain, kind="subdomain")


def _cache_code(kind: str, code: str) -> str:
    # Как нормализуют загрузчики: без этого «Olga» и «olga» занимали бы две записи.
    value = code.strip().lower()
    return value.rstrip(".") if kind == "subdomain" else value


async def _public_ref(
    ref_code: str,
    request: Request,
    response: Response,
    loader: RefLoader,
    *,
    kind: str = "ref",
) -> Any:
    """Ответ один для всех посетителей хоста: ни cookie, ни другие заголовки на него не влияют
    (арендатор берётся из Host / X-Forwarded-Host в middleware). Строка профиля — в памяти минуту,
    браузеру — ``public, max-age=120``; 404, 403 и 501 — ``no-store`` и в кэш не попадают."""
    settings = get_settings()
    try:
        tenant = get_request_tenant(request)
        require_entitlement(tenant, "structure_basic")

        if settings.core_route_public_ref == "legacy":
            raise HTTPException(
                status_code=501,
                detail={"error": "legacy_route_not_implemented_in_core"},
            )

        key = (tenant.tenant_id, kind, _cache_code(kind, ref_code))
        row = public_ref_cache.get(key)
        if row is None:
            row = await loader(tenant.tenant_id, ref_code)
            if not row:
                raise HTTPException(
                    status_code=404,
                    detail={"ok": False, "error": "referral_not_available"},
                )
            public_ref_cache.set(key, row, ttl=PUBLIC_CACHE_TTL_SEC)

        payload = format_public_ref(row)
    except HTTPException as exc:
        return await no_store_error(request, exc)
    if settings.core_route_public_ref == "shadow":
        request.state.shadow_public_ref = payload

    mark_public(response)
    return payload
