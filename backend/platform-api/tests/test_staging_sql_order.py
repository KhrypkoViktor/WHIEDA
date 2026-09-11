"""Staging SQL apply order (file presence + apply script)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SQL_DIR = ROOT / "postgres" / "sql"
APPLY_SCRIPT = ROOT / "postgres" / "scripts" / "apply_staging_platform_all.ps1"
CABINET_APPLY_SCRIPT = (
    ROOT / "n8n" / "current" / "apply_staging_cabinet_sql_2026-08-10.py"
)
RLS_CORE = SQL_DIR / "platform_tenant_rls_v1.sql"

EXPECTED_ORDER = [
    "platform_tenant_registry_v1.sql",
    "platform_bot_binding_context_v1.sql",
    "platform_tenant_rls_v1.sql",
    "whieda_website_leads_p0_v1.sql",
    "wwc_leads_p01_runtime_migration.sql",
    "platform_tenant_rls_legacy_leads_v1.sql",
    "platform_partner_subscriptions_v1.sql",
    "platform_partner_subscription_currency_v2.sql",
    "platform_referral_bonuses_v1.sql",
    "platform_referral_bonus_redemptions_v2.sql",
    "platform_partner_library_v1.sql",
    "platform_api_session_context_v1.sql",
    "platform_identity_journey_v1.sql",
    "platform_onboarding_v1.sql",
    "platform_user_memory_v1.sql",
    "platform_pilot_telemetry_v1.sql",
    "platform_retention_export_v1.sql",
    "platform_whieda_telegram_binding_v1.sql",
    "platform_cart_sessions_v1.sql",
    "platform_wwc_markets_v1.sql",
]


def test_all_sql_files_exist():
    for name in EXPECTED_ORDER:
        assert (SQL_DIR / name).is_file(), name


def test_apply_script_lists_full_order():
    text = APPLY_SCRIPT.read_text(encoding="utf-8")
    positions = [text.index(name) for name in EXPECTED_ORDER]
    assert positions == sorted(positions), "apply_staging_platform_all.ps1 order wrong"


def test_partner_subscription_migration_follows_required_schema():
    text = APPLY_SCRIPT.read_text(encoding="utf-8")
    required = [
        "platform_tenant_registry_v1.sql",
        "platform_tenant_rls_v1.sql",
        "platform_partner_subscriptions_v1.sql",
        "platform_partner_subscription_currency_v2.sql",
        "platform_referral_bonuses_v1.sql",
        "platform_referral_bonus_redemptions_v2.sql",
    ]
    positions = [text.index(name) for name in required]
    assert positions == sorted(positions)
    assert "lead_actors and referral_profiles" in text


def test_cabinet_apply_runs_binding_context_before_binding_seeds():
    text = CABINET_APPLY_SCRIPT.read_text(encoding="utf-8")
    context_run = text.index("run_sql_file(BOT_BINDING_CONTEXT_MIGRATION")
    whieda_run = text.index("run_sql_file(WHIEDA_BINDING_MIGRATION")
    cabinet_loop = text.index("for path in SQL_FILES:")
    assert context_run < whieda_run < cabinet_loop


def test_core_rls_does_not_touch_legacy_leads_tables():
    text = RLS_CORE.read_text(encoding="utf-8").lower()
    assert "website_leads" not in text
    assert "referral_profiles" not in text
