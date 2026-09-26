"""Staging SQL apply order (file presence + apply script)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SQL_DIR = ROOT / "postgres" / "sql"
APPLY_SCRIPT = ROOT / "postgres" / "scripts" / "apply_staging_platform_all.ps1"
CABINET_APPLY_SCRIPT = (
    ROOT / "n8n" / "current" / "apply_staging_cabinet_sql_2026-08-10.py"
)
RLS_CORE = SQL_DIR / "platform_tenant_rls_v1.sql"
sys.path.insert(0, str(ROOT / "postgres" / "scripts"))

from staging_proof_lib import APPLY_ORDER, apply_script_files

EXPECTED_ORDER = list(APPLY_ORDER)


def test_all_sql_files_exist():
    for name in EXPECTED_ORDER:
        assert (SQL_DIR / name).is_file(), name


def test_apply_script_lists_full_order():
    assert apply_script_files() == EXPECTED_ORDER


def test_partner_subscription_migration_follows_required_schema():
    text = APPLY_SCRIPT.read_text(encoding="utf-8")
    required = [
        "platform_tenant_registry_v1.sql",
        "platform_tenant_rls_v1.sql",
        "platform_partner_subscriptions_v1.sql",
        "platform_partner_subscription_currency_v2.sql",
        "platform_referral_bonuses_v1.sql",
        "platform_referral_bonus_redemptions_v2.sql",
        "platform_referral_admin_intents_v3.sql",
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


def test_bot_binding_context_sql_is_in_apply_order_once():
    name = "platform_bot_binding_context_v1.sql"
    inbox = "platform_telegram_durable_inbox_v1.sql"
    assert (SQL_DIR / name).is_file()
    assert (SQL_DIR / inbox).is_file()
    listed = apply_script_files()
    assert listed.count(name) == 1
    assert listed.count(inbox) == 1
    assert EXPECTED_ORDER.count(name) == 1
    assert listed.index("platform_whieda_telegram_binding_v1.sql") < listed.index(name)
    assert listed.index(name) < listed.index(inbox)
    data_plane = "platform_tenant_advisor_data_plane_v1.sql"
    assert listed.count(data_plane) == 1
    assert listed.index(inbox) < listed.index(data_plane)
    release_pkg = "platform_tenant_release_package_v1.sql"
    price_plane = "platform_tenant_release_price_plane_v1.sql"
    assert listed.count(release_pkg) == 1
    assert listed.index(data_plane) < listed.index(release_pkg)
    assert listed.count(price_plane) == 1
    assert listed.index(release_pkg) < listed.index(price_plane)
    outbox = "platform_telegram_durable_outbox_v1.sql"
    assert listed.count(outbox) == 1
    assert listed.index(price_plane) < listed.index(outbox)


def test_apply_script_lists_each_expected_file_once():
    listed = apply_script_files()
    assert listed == EXPECTED_ORDER
    assert len(listed) == len(set(listed))
    assert len(listed) == 41  # +telegram_consent_v1, +site_request_plans_v10 (19.09.2026), +lead_actor_channels_v11 (20.09.2026), +site_request_contacts_v12, +renewal_services_v13 (24.09.2026), +crm_v14, +academy_v1, +academy_shelf_v15 (25.09.2026), +support_site_forum_v16 (26.09.2026)  # +marketing_consent_v17 (26.09.2026)


def test_core_apply_sql_does_not_seed_nsp_maxim():
    for name in EXPECTED_ORDER:
        text = (SQL_DIR / name).read_text(encoding="utf-8").lower()
        assert "nsp-maxim" not in text
        assert "nsp_maxim" not in text
