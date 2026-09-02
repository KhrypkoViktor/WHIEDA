from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.db import tenant_connection
from app.markets.constants import (
    DEFAULT_STRUCTURE_ID,
    FALLBACK_NO_REGISTRY,
    normalize_city,
    normalize_market_id,
    normalize_ref,
    public_center_row,
    structure_display_name,
)
from app.markets.repository import (
    fetch_centers_for_city,
    fetch_cities,
    fetch_coverage_center,
    fetch_market,
    fetch_markets,
    fetch_prices,
    fetch_referral_profile,
    fetch_scheduled_sync_tenants,
    fetch_structure_for_ref,
    get_sync_registry,
    mark_sync_status,
    publish_bundle,
)
from app.markets.sync.sources import FixtureSheetsSource, GoogleSheetsSource, SheetsSource
from app.markets.sync.validate import SheetBundle, validate_sheet_bundle
from app.settings import get_settings

logger = logging.getLogger(__name__)


async def resolve_structure_id(tenant_id: str, ref: str | None, first_ref: str | None) -> str:
    active = normalize_ref(ref)
    first = normalize_ref(first_ref)

    async def _structure_for_public_ref(conn, ref_code: str) -> str | None:
        if not ref_code:
            return None
        profile = await fetch_referral_profile(conn, tenant_id, ref_code)
        if not profile:
            return None
        row = await fetch_structure_for_ref(conn, tenant_id, ref_code)
        return row["structure_id"] if row else None

    async with tenant_connection(tenant_id) as conn:
        if active:
            structure_id = await _structure_for_public_ref(conn, active)
            if structure_id:
                return structure_id
        if first:
            structure_id = await _structure_for_public_ref(conn, first)
            if structure_id:
                return structure_id
    return DEFAULT_STRUCTURE_ID


async def build_site_context(
    tenant_id: str,
    *,
    ref: str | None,
    first_ref: str | None,
    market_id: str | None,
    country_hint: str | None,
    city: str | None,
    page: str | None,
    sku: str | None,
) -> dict[str, Any]:
    normalized_ref = normalize_ref(ref)
    normalized_first = normalize_ref(first_ref) or normalized_ref
    market_key = normalize_market_id(market_id)

    async with tenant_connection(tenant_id) as conn:
        market = await fetch_market(conn, tenant_id, market_key)
        if not market:
            market = await fetch_market(conn, tenant_id, "global")
        markets = await fetch_markets(conn, tenant_id)
        structure_id = await resolve_structure_id(tenant_id, normalized_ref, normalized_first)

        partner_row = None
        if normalized_ref:
            partner_row = await fetch_referral_profile(conn, tenant_id, normalized_ref)

    profile = (partner_row or {}).get("public_profile") or {}
    partner = {
        "id": (partner_row or {}).get("owner_id") or "",
        "name": profile.get("display_name") or "",
        "contact": profile.get("public_site_url") or profile.get("contact_url") or "",
    }

    city_match = None
    if city and market and market["country_iso"] != "*":
        async with tenant_connection(tenant_id) as conn:
            centers = await fetch_centers_for_city(
                conn, tenant_id, structure_id, market["country_iso"], city
            )
            if centers:
                city_match = {
                    "type": "exact",
                    "city": centers[0]["city"],
                    "center_id": centers[0]["center_id"],
                }

    return {
        "ok": True,
        "ref": normalized_ref,
        "first_ref": normalized_first,
        "personalization_active": bool(partner_row),
        "partner": partner,
        "structure": {"id": structure_id, "name": structure_display_name(structure_id)},
        "market": {
            "id": market["market_id"] if market else "global",
            "country_iso": market["country_iso"] if market else "*",
            "currency_code": market["currency_code"] if market else "RUB",
            "price_visibility": market["price_visibility"] if market else "full",
        },
        "available_markets": [row["market_id"] for row in markets] or ["ru", "by", "global"],
        "country_hint": country_hint,
        "city_match": city_match,
        "service_centers": [],
        "page": page,
        "sku": sku,
    }


async def build_catalog_prices(
    tenant_id: str, *, market_id: str, skus: list[str] | None
) -> dict[str, Any]:
    market_key = normalize_market_id(market_id)
    price_market = market_key
    fallback_market = "ru" if market_key == "global" else market_key

    async with tenant_connection(tenant_id) as conn:
        market = await fetch_market(conn, tenant_id, market_key)
        if not market:
            market = await fetch_market(conn, tenant_id, "global")
        rows = await fetch_prices(conn, tenant_id, price_market, skus)
        if market_key == "global" and skus:
            missing = [sku for sku in skus if sku not in {r["sku"] for r in rows}]
            if missing:
                rows.extend(await fetch_prices(conn, tenant_id, fallback_market, missing))
        elif market_key == "global" and not skus:
            rows = await fetch_prices(conn, tenant_id, fallback_market, None)

    by_sku = {row["sku"]: row for row in rows}
    requested = skus or sorted(by_sku.keys())
    prices: list[dict[str, Any]] = []
    for sku in requested:
        row = by_sku.get(sku)
        if not row:
            prices.append(
                {
                    "sku": sku,
                    "amount": None,
                    "currency_code": market["currency_code"] if market else "RUB",
                    "formatted": None,
                    "price_state": "unavailable",
                    "is_active": False,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            continue
        prices.append(
            {
                "sku": row["sku"],
                "amount": float(row["amount"]) if row.get("amount") is not None else None,
                "currency_code": row["currency_code"],
                "formatted": row.get("formatted"),
                "price_state": row.get("price_state") or "active",
                "is_active": row.get("is_active", True),
                "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            }
        )

    return {
        "ok": True,
        "market_id": market_key,
        "currency_code": market["currency_code"] if market else "RUB",
        "price_visibility": market["price_visibility"] if market else "full",
        "prices": prices,
    }


async def build_service_center_cities(
    tenant_id: str, *, ref: str | None, first_ref: str | None, country_iso: str
) -> dict[str, Any]:
    structure_id = await resolve_structure_id(tenant_id, ref, first_ref)
    iso = (country_iso or "").strip().upper() or "*"
    async with tenant_connection(tenant_id) as conn:
        cities = await fetch_cities(conn, tenant_id, structure_id, iso)
    return {
        "ok": True,
        "structure_id": structure_id,
        "country_iso": iso,
        "cities": [{"city": row["city"], "region": row.get("region") or ""} for row in cities],
    }


async def build_service_centers(
    tenant_id: str,
    *,
    ref: str | None,
    first_ref: str | None,
    country_iso: str,
    city: str | None,
) -> dict[str, Any]:
    structure_id = await resolve_structure_id(tenant_id, ref, first_ref)
    iso = (country_iso or "").strip().upper() or "*"
    city_query = (city or "").strip()
    city_norm = normalize_city(city_query)

    if iso == "*":
        return {
            "ok": True,
            "structure_id": structure_id,
            "country_iso": iso,
            "city_query": city_query or None,
            "city_match": None,
            "centers": [],
            "fallback": {
                "type": "consultation",
                "message": "Для вашей страны доступна консультация. Локальный сервисный центр не указан.",
            },
        }

    async with tenant_connection(tenant_id) as conn:
        centers = []
        city_match = None
        if city_query:
            exact = await fetch_centers_for_city(conn, tenant_id, structure_id, iso, city_query)
            if exact:
                centers = [public_center_row(exact[0])]
                city_match = {
                    "type": "exact",
                    "city": exact[0]["city"],
                    "center_id": exact[0]["center_id"],
                }
            else:
                covered = await fetch_coverage_center(
                    conn, tenant_id, structure_id, iso, city_norm
                )
                if covered and covered["structure_id"] == structure_id:
                    centers = [public_center_row(covered)]
                    city_match = {
                        "type": "coverage",
                        "city": city_query,
                        "center_id": covered["center_id"],
                    }

    fallback = None
    if city_query and not centers:
        fallback = {"type": "no_registry_match", "message": FALLBACK_NO_REGISTRY}

    return {
        "ok": True,
        "structure_id": structure_id,
        "country_iso": iso,
        "city_query": city_query or None,
        "city_match": city_match,
        "centers": centers,
        "fallback": fallback,
    }


def build_sheets_source() -> SheetsSource:
    settings = get_settings()
    if settings.wwc_markets_sync_mode == "google":
        if not settings.wwc_markets_sheet_id or not settings.wwc_markets_google_credentials_path:
            raise RuntimeError("WWC_MARKETS_SHEET_ID and WWC_MARKETS_GOOGLE_CREDENTIALS_PATH required")
        return GoogleSheetsSource(
            spreadsheet_id=settings.wwc_markets_sheet_id,
            credentials_path=settings.wwc_markets_google_credentials_path,
        )
    return FixtureSheetsSource()


async def run_markets_sync(tenant_id: str, *, manual: bool = False) -> dict[str, Any]:
    source = build_sheets_source()
    async with tenant_connection(tenant_id) as conn:
        await mark_sync_status(conn, tenant_id, status="syncing", last_error=None, manual=manual)

    try:
        bundle = source.fetch()
        errors = validate_sheet_bundle(bundle)
        if errors:
            message = "; ".join(errors[:5])
            async with tenant_connection(tenant_id) as conn:
                await mark_sync_status(conn, tenant_id, status="error", last_error=message, manual=manual)
            return {"ok": False, "error": "validation_failed", "message": message, "errors": errors}

        async with tenant_connection(tenant_id) as conn:
            await publish_bundle(conn, tenant_id, bundle)
            await mark_sync_status(conn, tenant_id, status="ok", last_error=None, manual=manual)
        return {"ok": True, "tenant_id": tenant_id}
    except Exception as exc:
        logger.exception("wwc_markets_sync_failed")
        async with tenant_connection(tenant_id) as conn:
            await mark_sync_status(conn, tenant_id, status="error", last_error=str(exc)[:500], manual=manual)
        return {"ok": False, "error": "sync_failed", "message": str(exc)}


async def run_scheduled_markets_sync() -> dict[str, int]:
    from app.db import get_pool

    settings = get_settings()
    pool = get_pool()
    async with pool.connection(timeout=settings.database_timeout_sec) as conn:
        tenant_ids = await fetch_scheduled_sync_tenants(conn)

    synced = 0
    errors = 0
    for tenant_id in tenant_ids:
        result = await run_markets_sync(tenant_id, manual=False)
        if result.get("ok"):
            synced += 1
        else:
            errors += 1
    return {"tenants": len(tenant_ids), "synced": synced, "errors": errors}
