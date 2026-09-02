from __future__ import annotations

from datetime import datetime
from typing import Any

from app.admin.repositories import markets as markets_repo
from app.admin.repositories import products as products_repo
from app.admin.sync_registry_meta import (
    build_empty_state_meta,
    build_registry_readiness_item,
    enrich_registry_block,
)
from app.settings import get_settings

REQUIRED_CENTER_FIELDS = (
    "title",
    "manager_name",
    "address",
    "phone",
    "working_hours",
    "map_url_yandex",
)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _center_completeness(row: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in REQUIRED_CENTER_FIELDS if not str(row.get(field) or "").strip()]
    total = len(REQUIRED_CENTER_FIELDS)
    filled = total - len(missing)
    return {
        "score": round(filled / total, 2) if total else 0,
        "missing_fields": missing,
    }


async def build_markets_payload(tenant_id: str) -> dict[str, Any]:
    settings = get_settings()
    markets = await markets_repo.list_markets(tenant_id)
    registry = await markets_repo.fetch_markets_sync_registry(tenant_id)
    if not markets:
        return {
            "ok": True,
            "tenant_id": tenant_id,
            "items": [],
            "snapshot": None,
            "meta": {
                "field_status": "gap",
                "empty_state": build_empty_state_meta("markets", settings=settings),
            },
        }
    snapshot = None
    if registry:
        snapshot = {
            "status": registry.get("status"),
            "synced_at": _iso(registry.get("synced_at")),
            "last_error": registry.get("last_error"),
        }
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "items": [
            {
                "market_id": row.get("market_id"),
                "country_iso": row.get("country_iso"),
                "country_name": row.get("country_name"),
                "currency_code": row.get("currency_code"),
                "price_visibility": row.get("price_visibility"),
                "is_active": row.get("is_active"),
                "is_default": row.get("is_default"),
            }
            for row in markets
        ],
        "snapshot": snapshot,
    }


async def build_prices_payload(
    tenant_id: str,
    *,
    market_id: str | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    rows, total = await markets_repo.list_prices(
        tenant_id,
        market_id=market_id,
        limit=limit,
        offset=offset,
    )
    if not rows:
        return {
            "ok": True,
            "tenant_id": tenant_id,
            "items": [],
            "pagination": {"limit": limit, "offset": offset, "total": 0},
            "meta": {
                "field_status": "gap",
                "empty_state": build_empty_state_meta("markets", settings=get_settings()),
            },
        }

    skus = [str(row.get("sku")) for row in rows if row.get("sku")]
    product_names = await products_repo.fetch_product_names_by_skus(tenant_id, skus)

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "items": [
            {
                "sku": row.get("sku"),
                "product_name": product_names.get(str(row.get("sku"))) if row.get("sku") else None,
                "market_id": row.get("market_id"),
                "currency_code": row.get("currency_code"),
                "amount": float(row["amount"]) if row.get("amount") is not None else None,
                "formatted": row.get("formatted"),
                "price_state": row.get("price_state"),
                "is_active": row.get("is_active"),
                "updated_at": _iso(row.get("updated_at")),
            }
            for row in rows
        ],
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }


async def build_service_centers_payload(tenant_id: str) -> dict[str, Any]:
    settings = get_settings()
    rows = await markets_repo.list_service_centers(tenant_id)
    if not rows:
        return {
            "ok": True,
            "tenant_id": tenant_id,
            "source": "wwc_service_centers",
            "items": [],
            "meta": {
                "field_status": "gap",
                "empty_state": build_empty_state_meta("service_centers", settings=settings),
            },
        }
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "source": "wwc_service_centers",
        "items": [
            {
                "center_id": row.get("center_id"),
                "structure_id": row.get("structure_id"),
                "country_iso": row.get("country_iso"),
                "city": row.get("city"),
                "region": row.get("region"),
                "title": row.get("title"),
                "manager_name": row.get("manager_name"),
                "phone": row.get("phone"),
                "telegram": str(row.get("telegram") or "").lstrip("@") or None,
                "address": row.get("address"),
                "working_hours": row.get("working_hours"),
                "is_active": row.get("is_active"),
                "completeness": _center_completeness(row),
                "updated_at": _iso(row.get("updated_at")),
            }
            for row in rows
        ],
    }


async def build_sync_status_payload(tenant_id: str) -> dict[str, Any]:
    settings = get_settings()
    registry = await markets_repo.fetch_markets_sync_registry(tenant_id)
    markets_count = await markets_repo.count_markets(tenant_id)
    centers_active = await markets_repo.count_active_service_centers(tenant_id)
    centers_total = len(await markets_repo.list_service_centers(tenant_id))

    markets_block: dict[str, Any]
    if registry:
        markets_block = {
            "status": registry.get("status"),
            "synced_at": _iso(registry.get("synced_at")),
            "last_error": registry.get("last_error"),
            "scheduler_enabled": registry.get("scheduler_enabled"),
            "field_status": "implemented",
        }
    else:
        markets_block = {"field_status": "gap", "reason": "no_markets_sync_registry"}

    partners_block: dict[str, Any] = {
        "field_status": "gap",
        "reason": "n8n_cron_not_exposed_to_core",
    }
    structured_block: dict[str, Any] = {
        "field_status": "gap",
        "reason": "n8n_cron_not_exposed_to_core",
    }

    if centers_total:
        service_centers_block: dict[str, Any] = {
            "field_status": "implemented",
            "status": "ok",
            "runtime_row_count": centers_active,
            "runtime_row_total": centers_total,
        }
    else:
        service_centers_block = {"field_status": "gap", "reason": "no_service_centers_data"}

    return {
        "ok": True,
        "tenant_id": tenant_id,
        "markets_sync": enrich_registry_block(
            "markets",
            markets_block,
            settings=settings,
            runtime_row_count=markets_count if markets_count else None,
        ),
        "partners_sync": enrich_registry_block("partners", partners_block, settings=settings),
        "structured_sync": enrich_registry_block("structured", structured_block, settings=settings),
        "service_centers_sync": enrich_registry_block(
            "service_centers",
            service_centers_block,
            settings=settings,
            runtime_row_count=centers_active if centers_total else None,
        ),
    }


async def build_registry_readiness_payload(tenant_id: str, *, referrals: dict[str, int]) -> list[dict[str, Any]]:
    settings = get_settings()
    sync = await build_sync_status_payload(tenant_id)
    markets_count = await markets_repo.count_markets(tenant_id)
    centers_active = await markets_repo.count_active_service_centers(tenant_id)
    centers_total = len(await markets_repo.list_service_centers(tenant_id))

    referrals_enabled = int(referrals.get("enabled") or 0)
    structured_connected = sync["structured_sync"].get("operational_status") == "working"

    return [
        build_registry_readiness_item(
            "partners",
            connected=referrals_enabled > 0,
            summary=f"{referrals_enabled} активных ref" if referrals_enabled else "Ожидает источник",
            settings=settings,
        ),
        build_registry_readiness_item(
            "markets",
            connected=markets_count > 0,
            summary=f"{markets_count} рынков в runtime" if markets_count else "Ожидает первую синхронизацию",
            settings=settings,
        ),
        build_registry_readiness_item(
            "service_centers",
            connected=centers_total > 0,
            summary=(
                f"{centers_active} активных из {centers_total}"
                if centers_total
                else "Реестр пуст"
            ),
            settings=settings,
        ),
        build_registry_readiness_item(
            "structured",
            connected=structured_connected,
            summary="История запусков доступна" if structured_connected else "Журнал пока не подключён",
            settings=settings,
        ),
    ]
