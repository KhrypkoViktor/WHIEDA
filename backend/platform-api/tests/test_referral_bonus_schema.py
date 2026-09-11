"""Static contract for the additive referral-bonus migration."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SQL = ROOT / "postgres" / "sql" / "platform_referral_bonuses_v1.sql"
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
