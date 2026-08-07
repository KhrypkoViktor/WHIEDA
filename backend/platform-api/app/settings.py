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

    core_route_public_ref: RouteMode = Field(default="core", validation_alias="CORE_ROUTE_PUBLIC_REF")
    core_route_leads: RouteMode = Field(default="core", validation_alias="CORE_ROUTE_LEADS")
    core_route_advisor: RouteMode = Field(default="legacy", validation_alias="CORE_ROUTE_ADVISOR")
    core_route_telegram: RouteMode = Field(default="legacy", validation_alias="CORE_ROUTE_TELEGRAM")
    core_route_deep: DeepRouteMode = Field(default="off", validation_alias="CORE_ROUTE_DEEP")

    lead_consent_version: str = "wwc-lead-consent-2026-08-02"
    rate_limit_leads_per_minute: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
