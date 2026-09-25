"""Shared constants/helpers for local staging SQL proof (no prod)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "postgres" / "sql"
COMPOSE_FILE = ROOT / "postgres" / "docker-compose.local-staging.yml"
SEED = ROOT / "postgres" / "scripts" / "staging_seed_whieda_journey_v1.sql"
APPLY_PS1 = ROOT / "postgres" / "scripts" / "apply_staging_platform_all.ps1"
BACKFILL_PLAN = ROOT / "postgres" / "scripts" / "platform_bot_binding_context_backfill_plan_v1.sql"

LOCAL_STAGING_HOST = "127.0.0.1"
LOCAL_STAGING_PORT = 55432
LOCAL_STAGING_SUPERUSER = "postgres"
LOCAL_STAGING_SUPERPASSWORD = "local_staging_proof"

DOCKER_CONTAINER = "whieda-local-staging-postgres"

API_PROOF_ROLE = "whieda_platform_api_proof"
API_PROOF_PASSWORD = "local_api_proof_only"

ALLOWED_VERIFY_DB = re.compile(r"^whieda_platform_staging_verify_[a-z0-9][a-z0-9_]*$", re.I)

FORBIDDEN_HOST_FRAGMENTS = (
    "supabase",
    "amazonaws.com",
    "azure",
    "neon.tech",
    "render.com",
    "185.252.232.93",
    "wwc.best",
)

APPLY_ORDER = [
    "platform_tenant_registry_v1.sql",
    "platform_tenant_rls_v1.sql",
    "whieda_website_leads_p0_v1.sql",
    "wwc_leads_p01_runtime_migration.sql",
    "platform_tenant_rls_legacy_leads_v1.sql",
    "platform_partner_subscriptions_v1.sql",
    "platform_partner_subscription_currency_v2.sql",
    "platform_referral_bonuses_v1.sql",
    "platform_referral_bonus_redemptions_v2.sql",
    "platform_referral_admin_intents_v3.sql",
    "platform_partner_site_requests_v4.sql",
    "platform_partner_renewal_requests_v5.sql",
    "platform_partner_subscription_reminders_v6.sql",
    "platform_partner_products_v7.sql",
    "platform_support_tickets_v8.sql",
    "platform_support_forum_v9.sql",
    "platform_telegram_consent_v1.sql",
    "platform_partner_site_request_plans_v10.sql",
    "platform_lead_actor_channels_v11.sql",
    "platform_partner_site_request_contacts_v12.sql",
    "platform_renewal_services_v13.sql",
    "platform_crm_v14.sql",
    "platform_partner_library_v1.sql",
    "platform_api_session_context_v1.sql",
    "platform_advisor_structured_base_v1.sql",
    "platform_identity_journey_v1.sql",
    "platform_onboarding_v1.sql",
    "platform_user_memory_v1.sql",
    "platform_pilot_telemetry_v1.sql",
    "platform_retention_export_v1.sql",
    "platform_whieda_telegram_binding_v1.sql",
    "platform_bot_binding_context_v1.sql",
    "platform_telegram_durable_inbox_v1.sql",
    "platform_tenant_advisor_data_plane_v1.sql",
    "platform_tenant_release_package_v1.sql",
    "platform_tenant_release_price_plane_v1.sql",
    "platform_telegram_durable_outbox_v1.sql",
]

RLS_PROOF_TABLES = (
    "website_leads",
    "referral_profiles",
    "website_events",
    "website_lead_watchers",
    "referral_agreements",
    "partner_subscriptions",
    "partner_payment_ledger",
    "partner_payment_intents",
    "partner_library_items",
)

INBOX_RLS_PROOF_TABLES = (
    "telegram_update_inbox",
    "telegram_delivery_outbox",
)

ADVISOR_PROFILE_RLS_TABLES = (
    "tenant_advisor_profile",
)

RELEASE_PACKAGE_RLS_TABLES = (
    "tenant_release_run",
    "tenant_release_staging_product",
    "tenant_release_candidate",
    "tenant_release_candidate_product",
    "tenant_release_candidate_price",
)

INBOX_SQL = "platform_telegram_durable_inbox_v1.sql"
OUTBOX_SQL = "platform_telegram_durable_outbox_v1.sql"

LEADS_SCHEMA_FILES = (
    SQL_DIR / "whieda_website_leads_p0_v1.sql",
    SQL_DIR / "wwc_leads_p01_runtime_migration.sql",
)

RLS_LEGACY_FILE = SQL_DIR / "platform_tenant_rls_legacy_leads_v1.sql"


def apply_script_files(text: str | None = None) -> list[str]:
    source = APPLY_PS1.read_text(encoding="utf-8") if text is None else text
    start = source.index("$Files = @(")
    end = source.index(")", start)
    return re.findall(r'"([^"\n]+\.sql)"', source[start:end])


def validate_proof_db_name(db: str) -> None:
    if not ALLOWED_VERIFY_DB.match(db):
        raise ValueError(
            f"Refusing database name {db!r}. "
            "Required pattern: whieda_platform_staging_verify_<suffix>"
        )


def validate_proof_host(host: str) -> None:
    normalized = host.strip().lower()
    if normalized not in {LOCAL_STAGING_HOST, "localhost"}:
        raise ValueError(f"Refusing host {host!r}. Local proof allows 127.0.0.1 / localhost only.")
    for fragment in FORBIDDEN_HOST_FRAGMENTS:
        if fragment in normalized:
            raise ValueError(f"Refusing host {host!r}: matches forbidden fragment {fragment!r}")


def validate_proof_port(port: int) -> None:
    if port != LOCAL_STAGING_PORT:
        raise ValueError(f"Refusing port {port}. Local proof uses Docker Postgres on {LOCAL_STAGING_PORT} only.")


def tenant_scoped_lead_tables() -> set[str]:
    tables: set[str] = set()
    for schema in LEADS_SCHEMA_FILES:
        source = schema.read_text(encoding="utf-8").lower()
        for match in re.finditer(
            r"create table if not exists ([a-z_]+) \((.*?)(?=\ncreate table|\ndo \$\$|\ncommit;)",
            source,
            flags=re.DOTALL,
        ):
            if re.search(r"\btenant_id\s+text\b", match.group(2)):
                tables.add(match.group(1))
    return tables


def legacy_rls_covers_lead_tables() -> set[str]:
    text = RLS_LEGACY_FILE.read_text(encoding="utf-8").lower()
    covered: set[str] = set()
    for table in tenant_scoped_lead_tables():
        if f"create policy {table}_tenant_isolation on {table}" in text:
            covered.add(table)
    return covered
