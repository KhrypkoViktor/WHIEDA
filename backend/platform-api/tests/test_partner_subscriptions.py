from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.subscriptions.service import (
    PartnerHostCollisionError,
    ReservedPartnerHostError,
    SubscriptionError,
    add_calendar_months,
    build_host_snapshot,
    build_seed_manifest,
    normalize_partner_subdomain,
    paid_access_allowed,
    resolve_partner_hostname,
    subscription_state,
)


UTC = timezone.utc


def dt(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


def test_subscription_state_boundaries_are_half_open():
    paid_until = dt(2026, 9, 22)
    assert subscription_state(None, at=paid_until) == "no_subscription"
    assert subscription_state(paid_until, at=paid_until - timedelta(microseconds=1)) == "active"
    assert subscription_state(paid_until, at=paid_until) == "grace"
    assert subscription_state(paid_until, at=paid_until + timedelta(days=3) - timedelta(microseconds=1)) == "grace"
    assert subscription_state(paid_until, at=paid_until + timedelta(days=3)) == "suspended"


def test_paid_access_allows_active_and_grace_only():
    paid_until = dt(2026, 9, 22)
    assert paid_access_allowed(paid_until, at=paid_until - timedelta(days=1))
    assert paid_access_allowed(paid_until, at=paid_until + timedelta(days=2))
    assert not paid_access_allowed(paid_until, at=paid_until + timedelta(days=3))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (dt(2026, 1, 31), dt(2026, 4, 30)),
        (dt(2024, 11, 30), dt(2025, 2, 28)),
        (dt(2026, 9, 22, 15), dt(2026, 12, 22, 15)),
    ],
)
def test_add_three_calendar_months_clips_month_end(source: datetime, expected: datetime):
    assert add_calendar_months(source) == expected


def test_naive_datetime_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        subscription_state(datetime(2026, 9, 22), at=dt(2026, 9, 1))


def test_hostname_uses_profile_then_mapping_then_ref_fallback():
    assert resolve_partner_hostname("onlineelena") == "elena.wwc.best"
    assert resolve_partner_hostname("fedorov") == "fedorov.wwc.best"
    assert (
        resolve_partner_hostname("onlineelena", {"subdomain": "Elena-New.WWC.BEST."})
        == "elena-new.wwc.best"
    )


@pytest.mark.parametrize(
    "value",
    [
        "dev",
        "staging",
        "admin",
        "admin-staging",
        "fedorov-staging",
        "api",
        "media",
        "www",
        "wwc.best",
    ],
)
def test_reserved_or_invalid_partner_subdomain_is_rejected(value: str):
    error = ReservedPartnerHostError if value != "wwc.best" else SubscriptionError
    with pytest.raises(error):
        normalize_partner_subdomain(value)


def test_snapshot_rejects_duplicate_hostname():
    rows = [
        {"tenant_id": "whieda", "ref_code": "one", "public_profile": {"subdomain": "same"}},
        {"tenant_id": "whieda", "ref_code": "two", "public_profile": {"subdomain": "same"}},
    ]
    with pytest.raises(PartnerHostCollisionError):
        build_host_snapshot(rows, generated_at=dt(2026, 9, 9))


def test_snapshot_excludes_reserved_technical_profile():
    rows = [
        {"tenant_id": "whieda", "ref_code": "dev", "public_profile": {}},
        {"tenant_id": "whieda", "ref_code": "fedorov", "public_profile": {}},
    ]
    snapshot = build_host_snapshot(rows, generated_at=dt(2026, 9, 9))
    assert snapshot["allowed_hosts"] == ["fedorov.wwc.best"]


def test_seed_manifest_is_stable_and_never_shortens_existing_access():
    cutoff = dt(2026, 9, 21, 21)  # 2026-09-22 00:00 Europe/Moscow
    rows = [
        {
            "tenant_id": "whieda",
            "ref_code": "fedorov",
            "public_profile": {},
            "paid_until": dt(2026, 12, 1),
        },
        {
            "tenant_id": "whieda",
            "ref_code": "dev",
            "public_profile": {},
            "paid_until": None,
        },
    ]
    first = build_seed_manifest("whieda", rows, paid_until=cutoff)
    second = build_seed_manifest("whieda", list(reversed(rows)), paid_until=cutoff)
    assert first == second
    assert len(first["partners"]) == 1
    assert first["partners"][0]["new_paid_until"] == dt(2026, 12, 1).isoformat()


def test_subscription_migration_has_strict_period_rls_and_tenant_idempotency():
    root = Path(__file__).resolve().parents[3]
    sql = (root / "postgres" / "sql" / "platform_partner_subscriptions_v1.sql").read_text(
        encoding="utf-8"
    ).lower()
    assert "check (access_months = 3)" in sql
    assert "alter table partner_subscriptions enable row level security" in sql
    assert "alter table partner_payment_ledger enable row level security" in sql
    assert "alter table partner_payment_intents enable row level security" in sql
    assert "unique (tenant_id, source, telegram_chat_id, telegram_message_id)" in sql
    assert "idx_lead_actors_tenant_telegram_user" in sql


def test_local_staging_proof_covers_subscription_rls_and_concurrency():
    root = Path(__file__).resolve().parents[3]
    proof = (root / "postgres" / "scripts" / "run_local_staging_proof.py").read_text(
        encoding="utf-8"
    )
    assert "partner_subscriptions: cross-tenant INSERT rejected" in proof
    assert "concurrent distinct payments serialize without lost months" in proof
