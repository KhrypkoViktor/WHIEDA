from __future__ import annotations

from typing import Any

from app.db import fetch_one, tenant_connection
from app.settings import get_settings
from app.theme_access.service import ALLOWED_THEME_IDS, ISSUED_SUBDOMAIN_TO_REF, REF_TO_ISSUED_SUBDOMAIN

# Personal-page root: mirrors the site runtime (src/data/referrals.js ROOT_SITE).
PUBLIC_SITE_DOMAIN = "wwc.best"

# public_profile JSONB key -> additive socials contract key (camelCase, per WWC TZ V1 slice 2).
_SOCIAL_SOURCES: tuple[tuple[str, str], ...] = (
    ("telegramUrl", "telegram_url"),
    ("instagramUrl", "instagram_url"),
    ("vkUrl", "vk_url"),
    ("vkCommunityUrl", "vk_community_url"),
    ("youtubeUrl", "youtube_url"),
    ("tiktokUrl", "tiktok_url"),
    ("telegramChannelUrl", "telegram_channel_url"),
)


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


async def load_public_ref_by_subdomain(tenant_id: str, subdomain: str) -> dict[str, Any] | None:
    """Resolve an enabled referral profile by its personal-host subdomain.

    Sources, in priority order:
      1. ``public_profile->>'subdomain'`` (JSONB key, populated by the Partners_Ref sync);
      2. the issued-subdomain map from theme_access (e.g. ``samtsova`` -> ``olga-samtsova``).
    No dedicated SQL column exists yet; no migration is added here on purpose.
    """
    normalized = subdomain.strip().lower().rstrip(".")
    if not normalized:
        return None

    mapped_ref = ISSUED_SUBDOMAIN_TO_REF.get(normalized, normalized)
    subdomain_hit = "lower(coalesce(public_profile->>'subdomain', '')) = %s"
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            f"""
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
              and enabled = true
              and ({subdomain_hit} or ref_code = %s)
            order by ({subdomain_hit}) desc
            limit 1
            """,
            (tenant_id, normalized, mapped_ref, normalized),
        )


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def resolve_photo_url(photo_url: Any, media_base_url: str | None) -> str | None:
    """Return the public photo URL; absolutize relative paths only when a media base is set."""
    photo = _clean(photo_url)
    if not photo:
        return None
    base = _clean(media_base_url)
    if not base:
        return photo
    if photo.startswith(("http://", "https://", "//")):
        return photo
    if photo.startswith("/"):
        return f"{base.rstrip('/')}{photo}"
    return photo


def _resolve_subdomain(row: dict[str, Any], profile: dict[str, Any]) -> str | None:
    subdomain = _clean(profile.get("subdomain"))
    if subdomain:
        return subdomain.lower()
    ref_code = str(row.get("ref_code") or "").strip().lower()
    return _clean(REF_TO_ISSUED_SUBDOMAIN.get(ref_code))


def _theme(profile: dict[str, Any]) -> str | None:
    theme = _clean(profile.get("selected_theme_id"))
    return theme if theme in ALLOWED_THEME_IDS else None


def _personal_page_url(profile: dict[str, Any], subdomain: str | None) -> str | None:
    explicit = _clean(profile.get("personal_page_url"))
    if explicit:
        return explicit
    if subdomain:
        return f"https://{subdomain}.{PUBLIC_SITE_DOMAIN}/partner/"
    return None


def _socials(profile: dict[str, Any]) -> dict[str, str | None] | None:
    nested = profile.get("socials")
    nested = nested if isinstance(nested, dict) else {}
    socials: dict[str, str | None] = {}
    for contract_key, source_key in _SOCIAL_SOURCES:
        socials[contract_key] = _clean(nested.get(source_key) or profile.get(source_key))
    if not any(socials.values()):
        return None
    return socials


def format_public_ref(row: dict[str, Any]) -> dict[str, Any]:
    profile = row.get("public_profile") or {}
    subdomain = _resolve_subdomain(row, profile)
    return {
        "ok": True,
        "ref_code": row["ref_code"],
        "display_mode": row["display_mode"],
        "enabled": row["enabled"],
        "profile_version": row["profile_version"],
        "theme": _theme(profile),
        "personalPageUrl": _personal_page_url(profile, subdomain),
        "consultant": {
            "display_name": profile.get("display_name"),
            "page_mode": profile.get("page_mode"),
            "public_site_url": profile.get("public_site_url"),
            "site_type": profile.get("site_type"),
            "focus_group": profile.get("focus_group") is True,
            "access_tier": profile.get("access_tier"),
            "photoUrl": resolve_photo_url(
                profile.get("photo_url"), get_settings().tenant_media_base_url
            ),
            "subdomain": subdomain,
            "socials": _socials(profile),
        },
    }
