from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

from app.db import fetch_one, tenant_connection
from app.observability import log_event

ALLOWED_THEME_IDS = ("whieda-bright", "sankofa")
_SITE_ID_RE = re.compile(r"^[a-z0-9-]{1,64}$")
ISSUED_SUBDOMAIN_TO_REF = {
    "ladnaya": "ladnaya",
    "mariam": "mariam",
    "harold": "harold",
    "elena": "onlineelena",
    "samtsova": "olga-samtsova",
    "igoref": "igoref",
    "petrovna": "petrovna",
    "profit": "profit",
    "zubkovaludmila": "zubkovaludmila",
    "makarova": "makarova",
    "sofiya": "sofiya",
}
REF_TO_ISSUED_SUBDOMAIN = {
    ref: subdomain
    for subdomain, ref in ISSUED_SUBDOMAIN_TO_REF.items()
    if subdomain != ref
}


def normalize_site_id(value: str) -> str:
    site_id = str(value or "").strip().lower()
    if not _SITE_ID_RE.fullmatch(site_id):
        raise HTTPException(status_code=400, detail={"error": "invalid_site_id"})
    return site_id


def site_identity_aliases(site: dict[str, Any]) -> set[str]:
    ref_code = str(site.get("ref_code") or "").strip().lower()
    names = {ref_code}
    profile = site.get("public_profile") or {}
    subdomain = str(profile.get("subdomain") or "").strip().lower()
    if subdomain:
        names.add(subdomain)
    mapped_subdomain = REF_TO_ISSUED_SUBDOMAIN.get(ref_code, "")
    if mapped_subdomain:
        names.add(mapped_subdomain)
    names.discard("")
    return names


async def load_theme_site(tenant_id: str, site_id: str) -> dict[str, Any] | None:
    normalized_site_id = normalize_site_id(site_id)
    lookup_id = ISSUED_SUBDOMAIN_TO_REF.get(normalized_site_id, normalized_site_id)
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            """
            select rp.ref_code,
                   rp.public_profile,
                   la.telegram_chat_id
            from referral_profiles rp
            join lead_actors la
              on la.tenant_id = rp.tenant_id
             and la.actor_id = rp.owner_id
             and la.active = true
            where rp.tenant_id = %s
              and rp.enabled = true
              and (
                rp.ref_code = %s
                or rp.ref_code = %s
                or lower(coalesce(rp.public_profile->>'subdomain', '')) = %s
              )
            limit 1
            """,
            (tenant_id, lookup_id, normalized_site_id, normalized_site_id),
        )


def selected_theme(profile: dict[str, Any] | None) -> str:
    value = str((profile or {}).get("selected_theme_id") or "")
    return value if value in ALLOWED_THEME_IDS else ""


def public_theme_payload(site: dict[str, Any]) -> dict[str, Any]:
    profile = site.get("public_profile") or {}
    return {
        "ok": True,
        "site_id": site["ref_code"],
        "selected_theme_id": selected_theme(profile),
    }


def entitlement_payload(
    site: dict[str, Any],
    telegram_user_id: str,
    *,
    temporary_free: bool = False,
) -> dict[str, Any]:
    profile = site.get("public_profile") or {}
    owner_telegram_id = str(site.get("telegram_chat_id") or "")
    is_owner = bool(telegram_user_id and owner_telegram_id and telegram_user_id == owner_telegram_id)
    # Temporary free mode: any verified Telegram user (a valid session was
    # checked by the caller) may customize an enabled personal profile.
    temporary_grant = bool(temporary_free and telegram_user_id and not is_owner)
    allowed = is_owner or temporary_grant
    selected = selected_theme(profile)
    return {
        "ok": True,
        "telegram_user_id": telegram_user_id,
        "site_id": site["ref_code"],
        "theme_customization_allowed": allowed,
        "allowed_theme_ids": list(ALLOWED_THEME_IDS) if allowed else [],
        "selected_theme_id": selected if allowed else "",
        "access_expires_at": None,
        "source_status": (
            "site_owner" if is_owner else "temporary_free" if temporary_grant else "not_site_owner"
        ),
    }


async def save_selected_theme(
    tenant_id: str,
    *,
    site_id: str,
    telegram_user_id: str,
    theme_id: str,
    temporary_free: bool = False,
) -> dict[str, Any]:
    if theme_id not in ALLOWED_THEME_IDS:
        raise HTTPException(status_code=400, detail={"error": "theme_not_allowed"})

    site = await load_theme_site(tenant_id, site_id)
    if not site:
        raise HTTPException(status_code=404, detail={"error": "site_not_found"})
    entitlement = entitlement_payload(site, telegram_user_id, temporary_free=temporary_free)
    if not entitlement["theme_customization_allowed"]:
        raise HTTPException(status_code=403, detail={"error": "theme_owner_required"})

    previous_theme_id = selected_theme(site.get("public_profile"))
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update referral_profiles
            set public_profile = jsonb_set(
                    coalesce(public_profile, '{}'::jsonb),
                    '{selected_theme_id}',
                    to_jsonb(%s::text),
                    true
                ),
                profile_version = profile_version + 1,
                updated_at = now()
            where tenant_id = %s
              and ref_code = %s
              and enabled = true
            returning ref_code, public_profile
            """,
            (theme_id, tenant_id, site["ref_code"]),
        )
    if not row:
        raise HTTPException(status_code=404, detail={"error": "site_not_found"})

    # Audit the server-side theme decision: who saved, which site, which
    # theme, when (the log record carries the timestamp).
    log_event(
        f"theme_access.theme_saved tenant_id={tenant_id} site_id={site['ref_code']} "
        f"telegram_user_id={telegram_user_id} theme_id={theme_id} "
        f"previous_theme_id={previous_theme_id or ''} granted_via={entitlement['source_status']}",
        tenant_id=tenant_id,
        site_id=site["ref_code"],
        telegram_user_id=telegram_user_id,
        theme_id=theme_id,
        previous_theme_id=previous_theme_id,
        granted_via=entitlement["source_status"],
    )

    return entitlement_payload(
        {**site, "public_profile": row["public_profile"]},
        telegram_user_id,
        temporary_free=temporary_free,
    )
