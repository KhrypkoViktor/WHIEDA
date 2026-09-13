"""/me tells the site what the signed-in person owns: balance, PRO, CLUB."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.content_access.account import build_site_account, load_site_account
from app.content_access.service import format_me_payload


NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def test_build_site_account_counts_days_and_balance():
    account = build_site_account(
        actor_id="olga-samtsova",
        bonus_minor=1250,
        pro_paid_until=NOW + timedelta(days=84, hours=3),
        pro_ref_code="olga-samtsova",
        club_paid_until=None,
        at=NOW,
    )
    assert account["balance"] == {"currency": "WWC$", "amount_minor": 1250}
    assert account["pro"]["status"] == "active"
    assert account["pro"]["days_left"] == 84
    assert account["pro"]["ref_code"] == "olga-samtsova"
    assert account["club"] == {"status": "none", "paid_until": None, "days_left": None}


def test_build_site_account_without_subscription_is_none_status():
    account = build_site_account(
        actor_id="telegram:whieda:1", bonus_minor=0, pro_paid_until=None,
        pro_ref_code=None, club_paid_until=None, at=NOW,
    )
    assert account["pro"] == {"status": "none", "paid_until": None, "days_left": None, "ref_code": None}


def test_expired_pro_reports_grace_then_suspended():
    grace = build_site_account(
        actor_id="a", bonus_minor=0, pro_paid_until=NOW - timedelta(days=1),
        pro_ref_code="a", club_paid_until=None, at=NOW,
    )
    assert grace["pro"]["status"] == "grace"
    assert grace["pro"]["days_left"] == 0
    suspended = build_site_account(
        actor_id="a", bonus_minor=0, pro_paid_until=NOW - timedelta(days=40),
        pro_ref_code="a", club_paid_until=None, at=NOW,
    )
    assert suspended["pro"]["status"] == "suspended"


def test_me_payload_carries_account_and_keeps_legacy_fields():
    account = build_site_account(
        actor_id="a", bonus_minor=600, pro_paid_until=NOW + timedelta(days=10),
        pro_ref_code="a", club_paid_until=None, at=NOW,
    )
    payload = format_me_payload(
        {"expires_at": NOW},
        partner_subscription={"partner_paid": True, "subscription_status": "active", "paid_until": NOW},
        account=account,
    )
    assert payload["partner_paid"] is True
    assert payload["account"]["balance"]["amount_minor"] == 600
    assert payload["account"]["pro"]["paid_until"] == (NOW + timedelta(days=10)).isoformat()
    assert format_me_payload({"expires_at": NOW})["account"] is None


@pytest.mark.anyio
async def test_load_site_account_reads_actor_balance_and_pro(monkeypatch: pytest.MonkeyPatch):
    async def fake_fetch_one(conn, sql, params=None):
        if "to_regclass" in sql:
            return {"name": None}
        assert params == ("whieda", 525317405)
        assert "null::timestamptz as club_paid_until" in sql
        return {
            "actor_id": "olga-samtsova",
            "bonus_minor": 1250,
            "pro_paid_until": NOW + timedelta(days=5),
            "pro_ref_code": "olga-samtsova",
            "club_paid_until": None,
        }

    @asynccontextmanager
    async def fake_conn(tenant_id: str):
        yield object()

    monkeypatch.setattr("app.content_access.account.tenant_connection", fake_conn)
    monkeypatch.setattr("app.content_access.account.fetch_one", fake_fetch_one)
    monkeypatch.setattr("app.content_access.account._club_table_present", None)
    account = await load_site_account("whieda", 525317405, at=NOW)
    assert account["actor_id"] == "olga-samtsova"
    assert account["pro"]["days_left"] == 5


@pytest.mark.anyio
async def test_load_site_account_unknown_person_is_none(monkeypatch: pytest.MonkeyPatch):
    async def fake_fetch_one(conn, sql, params=None):
        return {"name": None} if "to_regclass" in sql else None

    @asynccontextmanager
    async def fake_conn(tenant_id: str):
        yield object()

    monkeypatch.setattr("app.content_access.account.tenant_connection", fake_conn)
    monkeypatch.setattr("app.content_access.account.fetch_one", fake_fetch_one)
    monkeypatch.setattr("app.content_access.account._club_table_present", None)
    assert await load_site_account("whieda", 1) is None


@pytest.mark.anyio
async def test_load_site_account_reads_club_when_table_exists(monkeypatch: pytest.MonkeyPatch):
    async def fake_fetch_one(conn, sql, params=None):
        if "to_regclass" in sql:
            return {"name": "partner_product_access"}
        assert "from partner_product_access pa" in sql
        return {"actor_id": "a", "bonus_minor": 0, "pro_paid_until": None, "pro_ref_code": "a",
                "club_paid_until": NOW + timedelta(days=30)}

    @asynccontextmanager
    async def fake_conn(tenant_id: str):
        yield object()

    monkeypatch.setattr("app.content_access.account.tenant_connection", fake_conn)
    monkeypatch.setattr("app.content_access.account.fetch_one", fake_fetch_one)
    monkeypatch.setattr("app.content_access.account._club_table_present", None)
    account = await load_site_account("whieda", 7, at=NOW)
    assert account["club"]["status"] == "active" and account["club"]["days_left"] == 30
