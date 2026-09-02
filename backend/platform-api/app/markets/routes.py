from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.markets.service import (
    build_catalog_prices,
    build_service_center_cities,
    build_service_centers,
    build_site_context,
)
from app.tenancy import get_request_tenant

router = APIRouter(tags=["wwc-markets"])


def _split_skus(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    return parts or None


async def _site_context_handler(
    request: Request,
    ref: str | None,
    first_ref: str | None,
    market_id: str | None,
    country_hint: str | None,
    city: str | None,
    page: str | None,
    sku: str | None,
) -> dict:
    tenant = get_request_tenant(request)
    return await build_site_context(
        tenant.tenant_id,
        ref=ref,
        first_ref=first_ref,
        market_id=market_id,
        country_hint=country_hint,
        city=city,
        page=page,
        sku=sku,
    )


@router.get("/v1/site-context")
async def site_context_v1(
    request: Request,
    ref: str | None = None,
    first_ref: str | None = None,
    market_id: str | None = None,
    country_hint: str | None = None,
    city: str | None = None,
    page: str | None = None,
    sku: str | None = None,
) -> dict:
    return await _site_context_handler(
        request, ref, first_ref, market_id, country_hint, city, page, sku
    )


@router.get("/api/site-context")
async def site_context_site_alias(
    request: Request,
    ref: str | None = None,
    first_ref: str | None = None,
    market_id: str | None = None,
    country_hint: str | None = None,
    city: str | None = None,
    page: str | None = None,
    sku: str | None = None,
) -> dict:
    return await _site_context_handler(
        request, ref, first_ref, market_id, country_hint, city, page, sku
    )


@router.get("/v1/catalog-prices")
async def catalog_prices_v1(
    request: Request,
    market_id: str = Query(...),
    sku: str | None = None,
) -> dict:
    tenant = get_request_tenant(request)
    return await build_catalog_prices(tenant.tenant_id, market_id=market_id, skus=_split_skus(sku))


@router.get("/api/catalog-prices")
async def catalog_prices_site_alias(
    request: Request,
    market_id: str = Query(...),
    sku: str | None = None,
) -> dict:
    tenant = get_request_tenant(request)
    return await build_catalog_prices(tenant.tenant_id, market_id=market_id, skus=_split_skus(sku))


@router.get("/v1/service-center-cities")
async def service_center_cities_v1(
    request: Request,
    country_iso: str = Query(...),
    ref: str | None = None,
    first_ref: str | None = None,
) -> dict:
    tenant = get_request_tenant(request)
    return await build_service_center_cities(
        tenant.tenant_id, ref=ref, first_ref=first_ref, country_iso=country_iso
    )


@router.get("/api/service-center-cities")
async def service_center_cities_site_alias(
    request: Request,
    country_iso: str = Query(...),
    ref: str | None = None,
    first_ref: str | None = None,
) -> dict:
    tenant = get_request_tenant(request)
    return await build_service_center_cities(
        tenant.tenant_id, ref=ref, first_ref=first_ref, country_iso=country_iso
    )


@router.get("/v1/service-centers")
async def service_centers_v1(
    request: Request,
    country_iso: str = Query(...),
    city: str | None = None,
    ref: str | None = None,
    first_ref: str | None = None,
) -> dict:
    tenant = get_request_tenant(request)
    return await build_service_centers(
        tenant.tenant_id,
        ref=ref,
        first_ref=first_ref,
        country_iso=country_iso,
        city=city,
    )


@router.get("/api/service-centers")
async def service_centers_site_alias(
    request: Request,
    country_iso: str = Query(...),
    city: str | None = None,
    ref: str | None = None,
    first_ref: str | None = None,
) -> dict:
    tenant = get_request_tenant(request)
    return await build_service_centers(
        tenant.tenant_id,
        ref=ref,
        first_ref=first_ref,
        country_iso=country_iso,
        city=city,
    )
