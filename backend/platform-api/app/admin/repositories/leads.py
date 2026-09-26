from __future__ import annotations

from typing import Any

from app.db import fetch_all, fetch_one, tenant_connection


async def count_leads(
    tenant_id: str,
    *,
    status: str | None = None,
    days: int | None = None,
) -> int:
    clauses = ["tenant_id = %s", "deleted_at is null"]
    params: list[Any] = [tenant_id]
    if status:
        clauses.append("status = %s")
        params.append(status)
    if days is not None:
        clauses.append("created_at >= now() - (%s || ' days')::interval")
        params.append(str(max(1, days)))
    where = " and ".join(clauses)
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(conn, f"select count(*) as total from website_leads where {where}", tuple(params))
    return int(row["total"]) if row else 0


async def list_leads(
    tenant_id: str,
    *,
    limit: int,
    offset: int,
    status: str | None = None,
    ref_code: str | None = None,
    country_code: str | None = None,
    assignee_id: str | None = None,
    product_sku: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    clauses = ["tenant_id = %s", "deleted_at is null"]
    params: list[Any] = [tenant_id]
    if status:
        clauses.append("status = %s")
        params.append(status)
    if ref_code:
        clauses.append("(first_ref_code = %s or active_ref_code = %s or initial_ref_code = %s)")
        params.extend([ref_code, ref_code, ref_code])
    if country_code:
        clauses.append("country_code = %s")
        params.append(country_code)
    if assignee_id:
        clauses.append("assigned_owner_id = %s")
        params.append(assignee_id)
    if product_sku:
        clauses.append("product_sku = %s")
        params.append(product_sku)
    where = " and ".join(clauses)

    async with tenant_connection(tenant_id) as conn:
        total_row = await fetch_one(
            conn,
            f"select count(*) as total from website_leads where {where}",
            tuple(params),
        )
        rows = await fetch_all(
            conn,
            f"""
            select lead_id, public_id, status, delivery_status, name, contact,
                   product_name, product_sku, initial_ref_code, first_ref_code, active_ref_code,
                   attributed_owner_id, assigned_owner_id, country_code, city,
                   metadata, marketing_consent, created_at, updated_at
            from website_leads
            where {where}
            order by created_at desc
            limit %s offset %s
            """,
            tuple(params + [limit, offset]),
        )
    return rows, int(total_row["total"]) if total_row else 0


async def get_lead(tenant_id: str, lead_id: str) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        lead = await fetch_one(
            conn,
            """
            select *
            from website_leads
            where tenant_id = %s and lead_id = %s::uuid and deleted_at is null
            limit 1
            """,
            (tenant_id, lead_id),
        )
        if not lead:
            return None
        status_history = await fetch_all(
            conn,
            """
            select old_status, new_status, changed_by_actor_id, reason, created_at
            from website_lead_status_history
            where tenant_id = %s and lead_id = %s::uuid
            order by created_at asc
            """,
            (tenant_id, lead_id),
        )
        owner_history = await fetch_all(
            conn,
            """
            select owner_id, action, changed_by, reason, created_at
            from website_lead_owner_history
            where tenant_id = %s and lead_id = %s::uuid
            order by created_at asc
            """,
            (tenant_id, lead_id),
        )
        watchers = await fetch_all(
            conn,
            """
            select watcher_actor_id, added_by_actor_id, scope, created_at
            from website_lead_watchers
            where tenant_id = %s and lead_id = %s::uuid and enabled = true
            order by created_at asc
            """,
            (tenant_id, lead_id),
        )
        delivery = await fetch_all(
            conn,
            """
            select delivery_id, channel, status, error_text, created_at, sent_at
            from lead_delivery_attempts
            where tenant_id = %s and lead_id = %s::uuid
            order by created_at desc
            limit 20
            """,
            (tenant_id, lead_id),
        )
    return {
        "lead": lead,
        "status_history": status_history,
        "owner_history": owner_history,
        "watchers": watchers,
        "delivery_history": delivery,
    }
