from __future__ import annotations

from datetime import datetime
from typing import Any

from app.admin.dto import gap_meta
from app.admin.ref_urls import canonical_public_ref_url
from app.admin.repositories import referrals as referrals_repo

PUBLIC_PROFILE_KEYS = frozenset(
    {
        "display_name",
        "page_mode",
        "public_site_url",
        "site_type",
        "focus_group",
        "access_tier",
        "photo_url",
    }
)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _public_profile(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {k: raw[k] for k in PUBLIC_PROFILE_KEYS if k in raw}


def _personalization_status(public_profile: dict[str, Any]) -> str:
    if public_profile.get("display_name") and public_profile.get("page_mode"):
        return "personalized"
    if public_profile.get("display_name") or public_profile.get("public_site_url"):
        return "partial"
    return "basic"


def _public_contact(public_profile: dict[str, Any]) -> str | None:
    site = str(public_profile.get("public_site_url") or "").strip()
    if site:
        return site
    return None


def _referral_item(row: dict[str, Any]) -> dict[str, Any]:
    ref_code = row.get("ref_code")
    public_profile = _public_profile(row.get("public_profile"))
    return {
        "ref_code": ref_code,
        "owner_id": row.get("owner_id"),
        "owner_display_name": row.get("owner_display_name"),
        "display_mode": row.get("display_mode"),
        "enabled": row.get("enabled"),
        "country_code": row.get("country_code"),
        "region_code": row.get("region_code"),
        "profile_version": row.get("profile_version"),
        "public_profile": public_profile,
        "public_url": canonical_public_ref_url(ref_code),
        "partner_tier": None,
        "personalization_status": _personalization_status(public_profile),
        "public_contact": _public_contact(public_profile),
        "last_activity_at": None,
        "updated_at": _iso(row.get("updated_at")),
        "meta": gap_meta("partner_tier", "last_activity_at", "subscription_cost"),
    }


async def build_referrals_list(
    tenant_id: str,
    *,
    limit: int,
    offset: int,
    search: str | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    rows, total = await referrals_repo.list_referrals(
        tenant_id,
        limit=limit,
        offset=offset,
        search=search,
        enabled=enabled,
    )
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "items": [_referral_item(row) for row in rows],
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }
