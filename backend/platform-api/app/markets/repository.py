from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection

from app.db import fetch_all, fetch_one
from app.markets.constants import format_price_amount
from app.markets.sync.validate import SheetBundle


STAGING_TABLES = (
    "wwc_markets_staging",
    "wwc_ref_structures_staging",
    "wwc_service_centers_staging",
    "wwc_service_center_coverage_staging",
    "wwc_product_prices_staging",
)

ACTIVE_TABLES = (
    "wwc_markets",
    "wwc_ref_structures",
    "wwc_service_centers",
    "wwc_service_center_coverage",
    "wwc_product_prices",
)


async def fetch_markets(conn: AsyncConnection, tenant_id: str) -> list[dict[str, Any]]:
    return await fetch_all(
        conn,
        """
        select market_id, country_iso, country_name, currency_code, price_visibility, is_active, is_default
        from wwc_markets
        where tenant_id = %s and is_active = true
        order by market_id
        """,
        (tenant_id,),
    )


async def fetch_market(conn: AsyncConnection, tenant_id: str, market_id: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select market_id, country_iso, country_name, currency_code, price_visibility, is_active, is_default
        from wwc_markets
        where tenant_id = %s and market_id = %s and is_active = true
        limit 1
        """,
        (tenant_id, market_id),
    )


async def fetch_structure_for_ref(
    conn: AsyncConnection, tenant_id: str, ref_code: str
) -> dict[str, Any] | None:
    if not ref_code:
        return None
    return await fetch_one(
        conn,
        """
        select ref_code, structure_id, is_active
        from wwc_ref_structures
        where tenant_id = %s and ref_code = %s and is_active = true
        limit 1
        """,
        (tenant_id, ref_code),
    )


async def fetch_prices(
    conn: AsyncConnection, tenant_id: str, market_id: str, skus: list[str] | None = None
) -> list[dict[str, Any]]:
    if skus:
        return await fetch_all(
            conn,
            """
            select sku, market_id, currency_code, amount, formatted, price_state, is_active, updated_at
            from wwc_product_prices
            where tenant_id = %s and market_id = %s and is_active = true and sku = any(%s)
            """,
            (tenant_id, market_id, skus),
        )
    return await fetch_all(
        conn,
        """
        select sku, market_id, currency_code, amount, formatted, price_state, is_active, updated_at
        from wwc_product_prices
        where tenant_id = %s and market_id = %s and is_active = true
        order by sku
        """,
        (tenant_id, market_id),
    )


async def fetch_cities(
    conn: AsyncConnection, tenant_id: str, structure_id: str, country_iso: str
) -> list[dict[str, Any]]:
    if country_iso == "*":
        return []
    return await fetch_all(
        conn,
        """
        select distinct city, region
        from wwc_service_centers
        where tenant_id = %s and structure_id = %s and country_iso = %s and is_active = true
        order by city
        """,
        (tenant_id, structure_id, country_iso),
    )


async def fetch_centers_for_city(
    conn: AsyncConnection, tenant_id: str, structure_id: str, country_iso: str, city: str
) -> list[dict[str, Any]]:
    return await fetch_all(
        conn,
        """
        select *
        from wwc_service_centers
        where tenant_id = %s and structure_id = %s and country_iso = %s
          and lower(trim(city)) = lower(trim(%s)) and is_active = true
        order by priority desc, center_id
        """,
        (tenant_id, structure_id, country_iso, city),
    )


async def fetch_coverage_center(
    conn: AsyncConnection,
    tenant_id: str,
    structure_id: str,
    country_iso: str,
    city_alias: str,
) -> dict[str, Any] | None:
    row = await fetch_one(
        conn,
        """
        select center_id, priority
        from wwc_service_center_coverage
        where tenant_id = %s and structure_id = %s and country_iso = %s
          and lower(trim(city_alias)) = lower(trim(%s)) and is_active = true
        order by priority desc
        limit 1
        """,
        (tenant_id, structure_id, country_iso, city_alias),
    )
    if not row:
        return None
    return await fetch_one(
        conn,
        """
        select *
        from wwc_service_centers
        where tenant_id = %s and center_id = %s and structure_id = %s and is_active = true
        limit 1
        """,
        (tenant_id, row["center_id"], structure_id),
    )


async def fetch_center_by_id(
    conn: AsyncConnection, tenant_id: str, center_id: str, structure_id: str
) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select *
        from wwc_service_centers
        where tenant_id = %s and center_id = %s and structure_id = %s and is_active = true
        limit 1
        """,
        (tenant_id, center_id, structure_id),
    )


async def fetch_referral_profile(
    conn: AsyncConnection, tenant_id: str, ref_code: str
) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select ref_code, owner_id, public_profile, enabled
        from referral_profiles
        where tenant_id = %s and ref_code = %s and enabled = true
        limit 1
        """,
        (tenant_id, ref_code),
    )


async def fetch_scheduled_sync_tenants(conn) -> list[str]:
    rows = await fetch_all(
        conn,
        """
        select r.tenant_id
        from wwc_markets_sync_registry r
        join tenants t on t.tenant_id = r.tenant_id
        where t.status = 'active'
          and r.first_manual_sync_at is not null
          and r.scheduler_enabled = true
        order by r.tenant_id
        """,
    )
    return [str(row["tenant_id"]) for row in rows]


async def get_sync_registry(conn: AsyncConnection, tenant_id: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        "select * from wwc_markets_sync_registry where tenant_id = %s",
        (tenant_id,),
    )


def _normalize_phone(value: Any) -> str | None:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit() or ch == "+")
    return digits or None


def _price_row(tenant_id: str, row: dict[str, Any]) -> tuple[Any, ...]:
    amount_raw = row.get("amount")
    amount = Decimal(str(amount_raw).replace(",", ".").replace(" ", "")) if amount_raw not in (None, "") else None
    currency = str(row.get("currency_code") or "RUB").strip().upper()
    formatted = format_price_amount(amount, currency) if amount is not None else None
    return (
        tenant_id,
        str(row.get("sku") or "").strip(),
        str(row.get("market_id") or "").strip().lower(),
        currency,
        amount,
        formatted,
        str(row.get("price_state") or "active").strip().lower(),
        bool(str(row.get("is_active", "true")).strip().lower() not in {"0", "false", "no"}),
        datetime.now(timezone.utc),
    )


async def publish_bundle(conn: AsyncConnection, tenant_id: str, bundle: SheetBundle) -> None:
    async with conn.cursor() as cur:
        for table in STAGING_TABLES:
            await cur.execute(f"delete from {table} where tenant_id = %s", (tenant_id,))

        for row in bundle.markets:
            await cur.execute(
                """
                insert into wwc_markets_staging (
                  tenant_id, market_id, country_iso, country_name, currency_code,
                  price_visibility, is_active, is_default
                ) values (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    tenant_id,
                    str(row.get("market_id")).strip().lower(),
                    str(row.get("country_iso")).strip().upper(),
                    str(row.get("country_name")).strip(),
                    str(row.get("currency_code")).strip().upper(),
                    str(row.get("price_visibility") or "full").strip().lower(),
                    str(row.get("is_active", "true")).strip().lower() not in {"0", "false", "no"},
                    str(row.get("is_default", "false")).strip().lower() in {"1", "true", "yes"},
                ),
            )

        for row in bundle.ref_structures:
            await cur.execute(
                """
                insert into wwc_ref_structures_staging (tenant_id, ref_code, structure_id, is_active)
                values (%s,%s,%s,%s)
                """,
                (
                    tenant_id,
                    str(row.get("ref_code")).strip().lower(),
                    str(row.get("structure_id")).strip(),
                    str(row.get("is_active", "true")).strip().lower() not in {"0", "false", "no"},
                ),
            )

        for row in bundle.service_centers:
            await cur.execute(
                """
                insert into wwc_service_centers_staging (
                  tenant_id, center_id, structure_id, country_iso, city, region, title, manager_name,
                  photo_url, telegram, phone, address, working_hours, map_url_yandex, map_url_google,
                  notes, is_active, priority
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    tenant_id,
                    str(row.get("center_id")).strip(),
                    str(row.get("structure_id")).strip(),
                    str(row.get("country_iso")).strip().upper(),
                    str(row.get("city")).strip(),
                    str(row.get("region") or "").strip(),
                    str(row.get("title")).strip(),
                    str(row.get("manager_name")).strip(),
                    (str(row.get("photo_url")).strip() or None),
                    (str(row.get("telegram")).strip().lstrip("@") or None),
                    _normalize_phone(row.get("phone")),
                    str(row.get("address")).strip(),
                    (str(row.get("working_hours")).strip() or None),
                    (str(row.get("map_url_yandex")).strip() or None),
                    (str(row.get("map_url_google")).strip() or None),
                    (str(row.get("notes")).strip() or None),
                    str(row.get("is_active", "true")).strip().lower() not in {"0", "false", "no"},
                    int(str(row.get("priority") or "100")),
                ),
            )

        for row in bundle.coverage:
            await cur.execute(
                """
                insert into wwc_service_center_coverage_staging (
                  tenant_id, structure_id, country_iso, city_alias, center_id, is_active, priority
                ) values (%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    tenant_id,
                    str(row.get("structure_id")).strip(),
                    str(row.get("country_iso")).strip().upper(),
                    str(row.get("city_alias")).strip().casefold(),
                    str(row.get("center_id")).strip(),
                    str(row.get("is_active", "true")).strip().lower() not in {"0", "false", "no"},
                    int(str(row.get("priority") or "100")),
                ),
            )

        for row in bundle.product_prices:
            await cur.execute(
                """
                insert into wwc_product_prices_staging (
                  tenant_id, sku, market_id, currency_code, amount, formatted, price_state, is_active, updated_at
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                _price_row(tenant_id, row),
            )

        for active, staging in zip(ACTIVE_TABLES, STAGING_TABLES, strict=True):
            await cur.execute(f"delete from {active} where tenant_id = %s", (tenant_id,))
            await cur.execute(
                f"insert into {active} select * from {staging} where tenant_id = %s",
                (tenant_id,),
            )


async def mark_sync_status(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    status: str,
    last_error: str | None = None,
    manual: bool = False,
) -> None:
    now = datetime.now(timezone.utc)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into wwc_markets_sync_registry (tenant_id, status, last_error, synced_at, first_manual_sync_at, updated_at)
            values (%s, %s, %s, %s, case when %s then %s else null end, %s)
            on conflict (tenant_id) do update set
              status = excluded.status,
              last_error = excluded.last_error,
              synced_at = case when excluded.status = 'ok' then excluded.synced_at else wwc_markets_sync_registry.synced_at end,
              first_manual_sync_at = coalesce(wwc_markets_sync_registry.first_manual_sync_at, excluded.first_manual_sync_at),
              scheduler_enabled = case when excluded.first_manual_sync_at is not null then true else wwc_markets_sync_registry.scheduler_enabled end,
              updated_at = excluded.updated_at
            """,
            (tenant_id, status, last_error, now, manual, now, now),
        )
