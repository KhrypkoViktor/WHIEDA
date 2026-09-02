from __future__ import annotations

from app.db import fetch_all, tenant_connection


async def fetch_product_names_by_skus(tenant_id: str, skus: list[str]) -> dict[str, str]:
    """Resolve canonical product names from advisor_structured_products (read-only)."""
    unique = [sku for sku in dict.fromkeys(s.strip() for s in skus if s and str(s).strip())]
    if not unique:
        return {}

    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select sku, canonical_name
            from advisor_structured_products
            where client_id = %s
              and sku = any(%s)
            """,
            (tenant_id, unique),
        )

    names: dict[str, str] = {}
    for row in rows:
        sku = row.get("sku")
        canonical = row.get("canonical_name")
        if sku and canonical:
            names[str(sku)] = str(canonical).strip()
    return names
