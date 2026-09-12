"""Static contract for the additive referral-bonus migration."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SQL = ROOT / "postgres" / "sql" / "platform_referral_bonuses_v1.sql"
REDEMPTIONS_SQL = ROOT / "postgres" / "sql" / "platform_referral_bonus_redemptions_v2.sql"
ADMIN_INTENTS_SQL = ROOT / "postgres" / "sql" / "platform_referral_admin_intents_v3.sql"
SITE_REQUESTS_SQL = ROOT / "postgres" / "sql" / "platform_partner_site_requests_v4.sql"
RENEWAL_REQUESTS_SQL = ROOT / "postgres" / "sql" / "platform_partner_renewal_requests_v5.sql"
REMINDERS_SQL = ROOT / "postgres" / "sql" / "platform_partner_subscription_reminders_v6.sql"
APPLY_SCRIPT = ROOT / "postgres" / "scripts" / "apply_staging_platform_all.ps1"


def _sql() -> str:
    return SQL.read_text(encoding="utf-8").lower()


def test_referral_bonus_migration_is_additive_and_registered_for_staging():
    text = _sql()
    assert "create table if not exists referral_invite_codes" in text
    assert "create table if not exists partner_referral_attributions" in text
    assert "create table if not exists partner_bonus_ledger" in text
    assert "drop table" not in text
    assert "platform_referral_bonuses_v1.sql" in APPLY_SCRIPT.read_text(encoding="utf-8")


def test_referral_bonus_schema_keeps_tenant_isolation_and_first_touch_rules():
    text = _sql()
    assert "primary key (tenant_id, invitee_actor_id)" in text
    assert "check (invitee_actor_id <> inviter_actor_id)" in text
    assert "partner_referral_bonus_tenant_guard" in text
    for table in (
        "partner_subscription_plans",
        "referral_invite_codes",
        "partner_referral_attributions",
        "partner_referral_attribution_audit",
        "referral_reward_rules",
        "partner_bonus_ledger",
    ):
        assert f"alter table {table} enable row level security" in text
        assert f"{table}_tenant_isolation" in text


def test_referral_bonus_schema_has_canonical_plans_and_reward_rules():
    text = _sql()
    assert "check (access_months in (3, 6, 12))" in text
    assert "('whieda', 'platform_3m', 'platform_subscription', 3, 3000, 300000)" in text
    assert "('whieda', 'platform_6m', 'platform_subscription', 6, 5400, 540000)" in text
    assert "('whieda', 'platform_12m', 'platform_subscription', 12, 9600, 960000)" in text
    assert "2000, 1000, 'wusd'" in text
    assert "unique (tenant_id, source_payment_id)" not in text
    assert "uq_partner_bonus_ledger_payment_credit" in text


def test_bonus_redemption_has_user_bound_single_use_intent_and_rls():
    text = REDEMPTIONS_SQL.read_text(encoding="utf-8").lower()
    assert "create table if not exists partner_bonus_redemption_intents" in text
    assert "consumed_entry_id" in text
    assert "cancelled_at" in text
    assert "telegram_chat_id" in text
    assert "telegram_user_id" in text
    assert "alter table partner_bonus_redemption_intents enable row level security" in text
    assert "partner_bonus_redemption_intents_tenant_isolation" in text
    assert "platform_referral_bonus_redemptions_v2.sql" in APPLY_SCRIPT.read_text(encoding="utf-8")


def test_referral_admin_operations_require_an_expiring_confirm_intent():
    text = ADMIN_INTENTS_SQL.read_text(encoding="utf-8").lower()
    assert "create table if not exists partner_referral_admin_intents" in text
    assert "assign_referrer" in text
    assert "adjust_bonus" in text
    assert "consumed_at" in text
    assert "cancelled_at" in text
    assert "telegram_user_id" in text
    assert "alter table partner_referral_admin_intents enable row level security" in text
    assert "platform_referral_admin_intents_v3.sql" in APPLY_SCRIPT.read_text(encoding="utf-8")


def test_site_request_queue_is_tenant_scoped_and_registered_for_staging():
    text = SITE_REQUESTS_SQL.read_text(encoding="utf-8").lower()
    assert "create table if not exists partner_site_requests" in text
    assert "pending_confirmation" in text
    assert "pending_provisioning" in text
    assert "alter table partner_site_requests enable row level security" in text
    assert "partner_site_requests_tenant_isolation" in text
    assert "platform_partner_site_requests_v4.sql" in APPLY_SCRIPT.read_text(encoding="utf-8")


def test_renewal_request_queue_is_tenant_scoped_and_registered_for_staging():
    text = RENEWAL_REQUESTS_SQL.read_text(encoding="utf-8").lower()
    assert "create table if not exists partner_renewal_requests" in text
    assert "alter table partner_renewal_requests enable row level security" in text
    assert "partner_renewal_requests_tenant_isolation" in text
    assert "platform_partner_renewal_requests_v5.sql" in APPLY_SCRIPT.read_text(encoding="utf-8")


def test_subscription_reminders_are_idempotent_and_tenant_scoped():
    text = REMINDERS_SQL.read_text(encoding="utf-8").lower()
    assert "unique (tenant_id, ref_code, event_type, paid_until)" in text
    assert "alter table partner_subscription_reminder_log enable row level security" in text
    assert "partner_subscription_reminder_log_tenant_isolation" in text
    assert "platform_partner_subscription_reminders_v6.sql" in APPLY_SCRIPT.read_text(encoding="utf-8")
