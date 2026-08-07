from __future__ import annotations

from typing import Any

from app.db import fetch_one, tenant_connection


async def load_public_ref(tenant_id: str, ref_code: str) -> dict[str, Any] | None:
    normalized = ref_code.strip().lower()
    if not normalized:
        return None

    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            """
            select ref_code,
                   tenant_id,
                   owner_id,
                   display_mode,
                   enabled,
                   country_code,
                   region_code,
                   profile_version,
                   public_profile
            from referral_profiles
            where tenant_id = %s
              and ref_code = %s
              and enabled = true
            limit 1
            """,
            (tenant_id, normalized),
        )


def format_public_ref(row: dict[str, Any]) -> dict[str, Any]:
    profile = row.get("public_profile") or {}
    return {
        "ok": True,
        "ref_code": row["ref_code"],
        "display_mode": row["display_mode"],
        "enabled": row["enabled"],
        "profile_version": row["profile_version"],
        "consultant": {
            "display_name": profile.get("display_name"),
            "page_mode": profile.get("page_mode"),
            "public_site_url": profile.get("public_site_url"),
            "site_type": profile.get("site_type"),
            "focus_group": profile.get("focus_group") is True,
            "access_tier": profile.get("access_tier"),
        },
    }
