"""Признак «своей сети» (app/funnel/network.py, 11.11.2026): профиль с own_network, запасной список из env, гость без партнёра."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.funnel import network, texts
from app.settings import get_settings

TENANT = "whieda"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.delenv("PLATFORM_OWN_NETWORK_REFS", raising=False)
    monkeypatch.delenv("PLATFORM_ORGANIC_OWNER_ID", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _profiles(monkeypatch, row):
    """Подменяет запрос профилей: row — то, что вернёт select (None — нет строк вообще)."""
    fetch = AsyncMock(return_value=row)
    monkeypatch.setattr(network, "fetch_one", fetch)
    return fetch


async def test_no_partner_is_own_network(monkeypatch):
    fetch = _profiles(monkeypatch, {"own_network": False, "profiles": 1})
    assert await network.is_own_network(object(), TENANT) is True
    assert await network.is_own_network(object(), TENANT, ref_code="  ", owner_id=None) is True
    fetch.assert_not_awaited()


async def test_profile_flag_true_is_own_and_flag_false_or_missing_is_foreign(monkeypatch):
    _profiles(monkeypatch, {"own_network": True, "profiles": 2})
    assert await network.is_own_network(object(), TENANT, ref_code="olga-samtsova") is True
    _profiles(monkeypatch, {"own_network": False, "profiles": 1})  # профиль есть, признака нет или он ложный
    assert await network.is_own_network(object(), TENANT, ref_code="stranger") is False
    assert await network.is_own_network(object(), TENANT, owner_id="telegram:whieda:42") is False
    _profiles(monkeypatch, {"own_network": None, "profiles": 1})
    assert await network.is_own_network(object(), TENANT, owner_id="x") is False


async def test_unknown_or_disabled_partner_is_not_a_partner_so_own_network(monkeypatch):
    _profiles(monkeypatch, {"own_network": None, "profiles": 0})
    assert await network.is_own_network(object(), TENANT, ref_code="nobody") is True
    _profiles(monkeypatch, None)
    assert await network.is_own_network(object(), TENANT, owner_id="telegram:whieda:7") is True


async def test_query_looks_at_enabled_profiles_by_owner_or_ref_and_reads_the_json_flag(monkeypatch):
    fetch = _profiles(monkeypatch, {"own_network": True, "profiles": 1})
    await network.is_own_network(object(), TENANT, ref_code="Ladnaya")
    sql, params = fetch.await_args.args[1], fetch.await_args.args[2]
    assert "public_profile->>'own_network'" in sql and "enabled" in sql
    assert "owner_id = %(owner)s" in sql and "lower(ref_code) = lower(%(ref)s)" in sql
    assert params == {"tenant": TENANT, "owner": None, "ref": "Ladnaya"}


async def test_env_list_is_a_fallback_by_ref_or_owner_and_skips_the_database(monkeypatch):
    monkeypatch.setenv("PLATFORM_OWN_NETWORK_REFS", " Viktor , onlineelena,olga-samtsova ,, ladnaya")
    get_settings.cache_clear()
    assert get_settings().parsed_own_network_refs() == ("viktor", "onlineelena", "olga-samtsova", "ladnaya")
    fetch = _profiles(monkeypatch, {"own_network": False, "profiles": 1})
    assert await network.is_own_network(object(), TENANT, ref_code="Ladnaya") is True
    assert await network.is_own_network(object(), TENANT, owner_id="viktor") is True
    fetch.assert_not_awaited()
    assert await network.is_own_network(object(), TENANT, ref_code="stranger") is False


async def test_env_list_is_empty_by_default():
    assert get_settings().parsed_own_network_refs() == ()


async def test_organic_owner_is_own_network(monkeypatch):
    fetch = _profiles(monkeypatch, {"own_network": False, "profiles": 3})
    assert await network.is_own_network(object(), TENANT, owner_id="viktor") is True  # PLATFORM_ORGANIC_OWNER_ID по умолчанию
    fetch.assert_not_awaited()


# ---- guest_offers_blocked ---------------------------------------------------------------------------


def _patch_guest(monkeypatch, *, guest, owner_flag=None, owner_error=None):
    @asynccontextmanager
    async def fake_connection(tenant_id):
        yield object()

    monkeypatch.setattr(network, "tenant_connection", fake_connection)
    monkeypatch.setattr(network, "resolve_guest", AsyncMock(return_value=guest))
    monkeypatch.setattr(network, "inviter_actor_id", AsyncMock(return_value="partner-x"))
    own = AsyncMock(side_effect=owner_error) if owner_error else AsyncMock(return_value=owner_flag)
    monkeypatch.setattr(network, "is_own_network", own)
    return own


async def test_blocked_only_for_a_guest_of_a_foreign_partner(monkeypatch):
    guest = SimpleNamespace(owner_actor_id="owner-1")
    own = _patch_guest(monkeypatch, guest=guest, owner_flag=False)
    assert await network.guest_offers_blocked(TENANT, "telegram", 5) is True
    assert own.await_args.kwargs == {"owner_id": "owner-1"}
    _patch_guest(monkeypatch, guest=guest, owner_flag=True)
    assert await network.guest_offers_blocked(TENANT, "telegram", 5) is False


async def test_not_a_guest_is_never_blocked(monkeypatch):
    own = _patch_guest(monkeypatch, guest=None, owner_flag=False)
    assert await network.guest_offers_blocked(TENANT, "max", 5) is False
    own.assert_not_awaited()


async def test_guest_without_lead_owner_takes_the_inviter(monkeypatch):
    own = _patch_guest(monkeypatch, guest=SimpleNamespace(owner_actor_id=None), owner_flag=False)
    assert await network.guest_offers_blocked(TENANT, "telegram", 5) is True
    assert own.await_args.kwargs == {"owner_id": "partner-x"}


async def test_database_failure_closes_the_offers(monkeypatch):
    _patch_guest(monkeypatch, guest=SimpleNamespace(owner_actor_id="o"), owner_error=RuntimeError("boom"))
    assert await network.guest_offers_blocked(TENANT, "telegram", 5) is True


def test_partner_contact_reply_has_only_the_ask_button(monkeypatch):
    monkeypatch.setattr(network, "_stages", lambda: False)
    reply = network.partner_contact_reply()
    assert reply.text == texts.GUEST_PARTNER_WILL_CONTACT
    assert reply.rows == [[(texts.GUEST_ASK_BUTTON, texts.FUNNEL_ASK_CALLBACK)]]
    monkeypatch.setattr(network, "_stages", lambda: True)
    assert network.partner_contact_reply().rows == [[(texts.STAGE_ASK_BUTTON, texts.FUNNEL_ASK_CALLBACK)]]


# ---- offers_blocked_for: гости и партнёры WHIEDA одной функцией --------------------------------------------


def _patch_stage(monkeypatch, *, stage, owner="inviter-1", own=False, guest=None):
    @asynccontextmanager
    async def fake_connection(tenant_id):
        yield object()

    monkeypatch.setenv("PLATFORM_BOT_STAGES", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(network, "tenant_connection", fake_connection)
    monkeypatch.setattr(network, "resolve_stage", AsyncMock(return_value=stage))
    monkeypatch.setattr(network, "inviter_actor_id", AsyncMock(return_value=owner))
    monkeypatch.setattr(network, "resolve_guest", AsyncMock(return_value=guest))
    flag = AsyncMock(return_value=own)
    monkeypatch.setattr(network, "is_own_network", flag)
    return flag


async def test_partner_whieda_of_a_foreign_inviter_is_blocked(monkeypatch):
    flag = _patch_stage(monkeypatch, stage="partner_whieda", own=False)
    assert await network.offers_blocked_for(TENANT, "telegram", 5) is True
    assert flag.await_args.kwargs == {"owner_id": "inviter-1"}
    network.resolve_guest.assert_not_awaited()


async def test_partner_whieda_of_the_own_network_or_without_inviter_is_not_blocked(monkeypatch):
    _patch_stage(monkeypatch, stage="partner_whieda", own=True)
    assert await network.offers_blocked_for(TENANT, "max", 5) is False
    flag = _patch_stage(monkeypatch, stage="partner_whieda", owner=None, own=True)  # is_own_network(None) — своя сеть
    assert await network.offers_blocked_for(TENANT, "max", 5) is False
    assert flag.await_args.kwargs == {"owner_id": None}


async def test_partner_wwc_is_never_blocked(monkeypatch):
    flag = _patch_stage(monkeypatch, stage="partner_wwc", own=False)
    assert await network.offers_blocked_for(TENANT, "telegram", 5) is False
    flag.assert_not_awaited()


async def test_a_guest_goes_the_guest_way_under_the_stages(monkeypatch):
    flag = _patch_stage(monkeypatch, stage="guest", own=False, guest=SimpleNamespace(owner_actor_id="lead-owner"))
    assert await network.offers_blocked_for(TENANT, "telegram", 5) is True
    assert flag.await_args.kwargs == {"owner_id": "lead-owner"}
    assert network.resolve_guest.await_args.kwargs["stage"] == "guest"
    assert network.guest_offers_blocked is network.offers_blocked_for
