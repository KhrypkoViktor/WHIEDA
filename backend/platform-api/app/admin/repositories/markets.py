from __future__ import annotations

from typing import Any

from app.db import fetch_all, tenant_connection
from app.markets.repository import fetch_markets, get_sync_registry


async def list_markets(tenant_id: str) -> list[dict[str, Any]]:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_markets(conn, tenant_id)


async def list_prices(
    tenant_id: str,
    *,
    market_id: str | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    clauses = ["tenant_id = %s"]
    params: list[Any] = [tenant_id]
    if market_id:
        clauses.append("market_id = %s")
        params.append(market_id)
    where = " and ".join(clauses)

    async with tenant_connection(tenant_id) as conn:
        from app.db import fetch_one

        total_row = await fetch_one(
            conn,
            f"select count(*) as total from wwc_product_prices where {where}",
            tuple(params),
        )
        rows = await fetch_all(
            conn,
            f"""
            select sku, market_id, currency_code, amount, formatted, price_state, is_active, updated_at
            from wwc_product_prices
            where {where}
            order by market_id, sku
            limit %s offset %s
            """,
            tuple(params + [limit, offset]),
        )
    return rows, int(total_row["total"]) if total_row else 0


async def list_service_centers(tenant_id: str) -> list[dict[str, Any]]:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            """
            select center_id, structure_id, country_iso, city, region, title,
                   manager_name, photo_url, telegram, phone, address, working_hours,
                   map_url_yandex, map_url_google, notes, is_active, priority
            from wwc_service_centers
            where tenant_id = %s
            order by country_iso, city, center_id
            """,
            (tenant_id,),
        )


async def count_markets(tenant_id: str) -> int:
    async with tenant_connection(tenant_id) as conn:
        from app.db import fetch_one

        row = await fetch_one(
            conn,
            "select count(*) as total from wwc_markets where tenant_id = %s",
            (tenant_id,),
        )
        return int(row["total"]) if row else 0


async def count_active_service_centers(tenant_id: str) -> int:
    async with tenant_connection(tenant_id) as conn:
        from app.db import fetch_one

        row = await fetch_one(
            conn,
            "select count(*) as total from wwc_service_centers where tenant_id = %s and is_active = true",
            (tenant_id,),
        )
        return int(row["total"]) if row else 0


async def fetch_markets_sync_registry(tenant_id: str) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await get_sync_registry(conn, tenant_id)
