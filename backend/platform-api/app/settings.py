from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RouteMode = Literal["legacy", "shadow", "core"]
DeepRouteMode = Literal["off", "shadow", "core"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "whieda-platform-api"
    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = Field(
        default="postgresql://postgres:postgres@localhost:5432/postgres",
        validation_alias="PLATFORM_DATABASE_URL",
    )
    database_pool_min: int = 1
    database_pool_max: int = 10
    database_timeout_sec: float = 5.0

    redis_url: str | None = Field(default=None, validation_alias="PLATFORM_REDIS_URL")

    default_host_tenant: str | None = Field(
        default=None,
        validation_alias="PLATFORM_DEFAULT_HOST_TENANT",
        description="Dev-only fallback host; production must leave unset.",
    )

    legacy_n8n_base_url: str = Field(
        default="https://sysarchn8n.duckdns.org",
        validation_alias="PLATFORM_LEGACY_N8N_BASE_URL",
    )
    legacy_request_timeout_sec: float = Field(
        default=8.0,
        validation_alias="PLATFORM_LEGACY_REQUEST_TIMEOUT_SEC",
    )
    telegram_legacy_timeout_sec: float = Field(
        default=180.0,
        validation_alias="PLATFORM_TELEGRAM_LEGACY_TIMEOUT_SEC",
    )
    lead_delivery_webhook_path: str = Field(
        default="/webhook/whieda-lead-delivery-v1",
        validation_alias="PLATFORM_LEAD_DELIVERY_WEBHOOK_PATH",
    )
    telegram_bot_token: str | None = Field(
        default=None,
        validation_alias="PLATFORM_TELEGRAM_BOT_TOKEN",
    )
    telegram_webhook_secret: str | None = Field(
        default=None,
        validation_alias="PLATFORM_TELEGRAM_WEBHOOK_SECRET",
    )
    telegram_bot_username: str | None = Field(
        default=None,
        validation_alias="PLATFORM_TELEGRAM_BOT_USERNAME",
        description="Public @username for deep links (no @ prefix). Staging/dev only until cutover.",
    )
    tenant_media_base_url: str | None = Field(
        default=None,
        validation_alias="PLATFORM_TENANT_MEDIA_BASE_URL",
        description=(
            "Public HTTPS base of the tenant media host (e.g. https://media.sysarch.pro). "
            "Used to absolutize relative partner photo paths in public profiles; "
            "unset means relative paths are returned as-is."
        ),
    )

    core_route_public_ref: RouteMode = Field(default="core", validation_alias="CORE_ROUTE_PUBLIC_REF")
    core_route_leads: RouteMode = Field(default="core", validation_alias="CORE_ROUTE_LEADS")
    # Production has completed the Core cutover. Defaults must preserve that
    # route if an operator rebuilds an environment with a missing .env value.
    core_route_advisor: RouteMode = Field(default="core", validation_alias="CORE_ROUTE_ADVISOR")
    core_route_telegram: RouteMode = Field(default="core", validation_alias="CORE_ROUTE_TELEGRAM")
    core_route_deep: DeepRouteMode = Field(default="off", validation_alias="CORE_ROUTE_DEEP")

    wwc_markets_sync_mode: Literal["fixture", "google"] = Field(
        default="fixture",
        validation_alias="WWC_MARKETS_SYNC_MODE",
    )
    wwc_markets_sheet_id: str | None = Field(
        default=None,
        validation_alias="WWC_MARKETS_SHEET_ID",
    )
    wwc_markets_google_credentials_path: str | None = Field(
        default=None,
        validation_alias="WWC_MARKETS_GOOGLE_CREDENTIALS_PATH",
    )

    lead_consent_version: str = "wwc-lead-consent-2026-08-02"
    rate_limit_leads_per_minute: int = 30

    platform_admin_super_telegram_ids: str = Field(
        default="",
        validation_alias="PLATFORM_ADMIN_SUPER_TELEGRAM_IDS",
        description="Comma-separated Telegram user IDs allowed as super_admin bootstrap.",
    )
    platform_billing_owner_telegram_id: int | None = Field(
        default=None,
        validation_alias="PLATFORM_BILLING_OWNER_TELEGRAM_ID",
        description="Single Telegram user ID allowed to confirm manual partner payments.",
    )
    platform_admin_confirm_secret: str | None = Field(
        default=None,
        validation_alias="PLATFORM_ADMIN_CONFIRM_SECRET",
        description="Shared secret for internal POST /v1/admin/auth/telegram-confirm.",
    )
    platform_admin_session_ttl_minutes: int = Field(
        default=720,
        validation_alias="PLATFORM_ADMIN_SESSION_TTL_MINUTES",
    )
    platform_admin_challenge_ttl_minutes: int = Field(
        default=10,
        validation_alias="PLATFORM_ADMIN_CHALLENGE_TTL_MINUTES",
    )
    platform_admin_cookie_name: str = Field(
        default="wwc_admin_session",
        validation_alias="PLATFORM_ADMIN_COOKIE_NAME",
    )
    platform_admin_cookie_secure: bool = Field(
        default=True,
        validation_alias="PLATFORM_ADMIN_COOKIE_SECURE",
    )
    platform_admin_cookie_samesite: str = Field(
        default="lax",
        validation_alias="PLATFORM_ADMIN_COOKIE_SAMESITE",
    )

    platform_content_session_ttl_days: int = Field(
        default=30,
        validation_alias="PLATFORM_CONTENT_SESSION_TTL_DAYS",
    )
    platform_content_challenge_ttl_minutes: int = Field(
        default=10,
        validation_alias="PLATFORM_CONTENT_CHALLENGE_TTL_MINUTES",
    )
    platform_content_cookie_name: str = Field(
        default="wwc_content_session",
        validation_alias="PLATFORM_CONTENT_COOKIE_NAME",
    )
    platform_content_cookie_secure: bool = Field(
        default=True,
        validation_alias="PLATFORM_CONTENT_COOKIE_SECURE",
    )
    platform_content_cookie_samesite: str = Field(
        default="lax",
        validation_alias="PLATFORM_CONTENT_COOKIE_SAMESITE",
    )

    platform_partner_library_storage_backend: Literal["local", "s3"] = Field(
        default="local",
        validation_alias="PLATFORM_PARTNER_LIBRARY_STORAGE_BACKEND",
    )
    platform_partner_library_local_root: str | None = Field(
        default=None,
        validation_alias="PLATFORM_PARTNER_LIBRARY_LOCAL_ROOT",
    )
    platform_partner_library_local_signing_secret: str = Field(
        default="development-only-change-me",
        validation_alias="PLATFORM_PARTNER_LIBRARY_LOCAL_SIGNING_SECRET",
    )
    platform_partner_library_local_base_url: str = Field(
        default="/api/v1/partner-library/local-files",
        validation_alias="PLATFORM_PARTNER_LIBRARY_LOCAL_BASE_URL",
    )
    platform_partner_library_s3_bucket: str | None = Field(
        default=None,
        validation_alias="PLATFORM_PARTNER_LIBRARY_S3_BUCKET",
    )
    platform_partner_library_s3_endpoint_url: str | None = Field(
        default=None,
        validation_alias="PLATFORM_PARTNER_LIBRARY_S3_ENDPOINT_URL",
    )
    platform_partner_library_s3_region: str | None = Field(
        default=None,
        validation_alias="PLATFORM_PARTNER_LIBRARY_S3_REGION",
    )
    platform_partner_library_signed_url_ttl_seconds: int = Field(
        default=300,
        ge=60,
        le=900,
        validation_alias="PLATFORM_PARTNER_LIBRARY_SIGNED_URL_TTL_SECONDS",
    )

    # Theme access: when enabled, every verified Telegram user (valid content
    # session) may customize the theme of an enabled personal profile without
    # being its owner. Rights are always decided server-side, never in the
    # browser.
    temporary_free_for_verified_telegram_users: bool = Field(
        default=False,
        validation_alias="THEME_TEMPORARY_FREE_FOR_VERIFIED_TELEGRAM_USERS",
    )

    platform_admin_source_url_markets: str | None = Field(
        default=None,
        validation_alias="PLATFORM_ADMIN_SOURCE_URL_MARKETS",
    )
    platform_admin_source_title_markets: str | None = Field(
        default=None,
        validation_alias="PLATFORM_ADMIN_SOURCE_TITLE_MARKETS",
    )
    platform_admin_source_url_partners: str | None = Field(
        default=None,
        validation_alias="PLATFORM_ADMIN_SOURCE_URL_PARTNERS",
    )
    platform_admin_source_title_partners: str | None = Field(
        default=None,
        validation_alias="PLATFORM_ADMIN_SOURCE_TITLE_PARTNERS",
    )
    platform_admin_source_url_structured: str | None = Field(
        default=None,
        validation_alias="PLATFORM_ADMIN_SOURCE_URL_STRUCTURED",
    )
    platform_admin_source_title_structured: str | None = Field(
        default=None,
        validation_alias="PLATFORM_ADMIN_SOURCE_TITLE_STRUCTURED",
    )
    platform_admin_source_url_service_centers: str | None = Field(
        default=None,
        validation_alias="PLATFORM_ADMIN_SOURCE_URL_SERVICE_CENTERS",
    )
    platform_admin_source_title_service_centers: str | None = Field(
        default=None,
        validation_alias="PLATFORM_ADMIN_SOURCE_TITLE_SERVICE_CENTERS",
    )

    def parsed_super_admin_telegram_ids(self) -> frozenset[int]:
        ids: set[int] = set()
        for part in self.platform_admin_super_telegram_ids.split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        return frozenset(ids)


@lru_cache
def get_settings() -> Settings:
    return Settings()
