from __future__ import annotations

from typing import Any

from app.db import fetch_all, fetch_one, tenant_connection


async def list_referrals(
    tenant_id: str,
    *,
    limit: int,
    offset: int,
    search: str | None = None,
    enabled: bool | None = None,
) -> tuple[list[dict[str, Any]], int]:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    clauses = ["rp.tenant_id = %s"]
    params: list[Any] = [tenant_id]
    if enabled is not None:
        clauses.append("rp.enabled = %s")
        params.append(enabled)
    if search:
        clauses.append("(rp.ref_code ilike %s or la.display_name ilike %s)")
        pattern = f"%{search.strip().lower()}%"
        params.extend([pattern, pattern])
    where = " and ".join(clauses)

    async with tenant_connection(tenant_id) as conn:
        total_row = await fetch_one(
            conn,
            f"""
            select count(*) as total
            from referral_profiles rp
            join lead_actors la on la.actor_id = rp.owner_id and la.tenant_id = rp.tenant_id
            where {where}
            """,
            tuple(params),
        )
        rows = await fetch_all(
            conn,
            f"""
            select rp.ref_code, rp.owner_id, rp.display_mode, rp.enabled, rp.country_code,
                   rp.region_code, rp.profile_version, rp.public_profile, rp.updated_at,
                   la.display_name as owner_display_name, la.telegram_username
            from referral_profiles rp
            join lead_actors la on la.actor_id = rp.owner_id and la.tenant_id = rp.tenant_id
            where {where}
            order by rp.ref_code asc
            limit %s offset %s
            """,
            tuple(params + [limit, offset]),
        )
    return rows, int(total_row["total"]) if total_row else 0


async def count_enabled_referrals(tenant_id: str) -> dict[str, int]:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select
              count(*) filter (where enabled) as enabled,
              count(*) filter (where not enabled) as disabled
            from referral_profiles
            where tenant_id = %s
            """,
            (tenant_id,),
        )
    return {
        "enabled": int(row["enabled"]) if row else 0,
        "disabled": int(row["disabled"]) if row else 0,
    }
