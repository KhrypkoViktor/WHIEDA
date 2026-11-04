"""Кэши на пути нажатия кнопки: привязка бота (вебхук) и форум поддержки (04.11.2026). Без базы."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.telegram import bindings


# ---- Кэш привязки бота -------------------------------------------------------------------------


class _Pool:
    def __init__(self) -> None:
        self.checkouts = 0

    def connection(self, **_kwargs):
        pool = self

        class _Connection:
            async def __aenter__(self):
                pool.checkouts += 1
                return object()

            async def __aexit__(self, *_exc):
                return None

        return _Connection()


def _binding_row(**overrides):
    row = {
        "tenant_id": "whieda", "tenant_status": "active", "display_name": "WHIEDA",
        "binding_status": "active", "webhook_secret_ref": "env:TAP_WEBHOOK_SECRET",
        "bot_token_ref": "env:TAP_BOT_TOKEN", "bot_username": "WHIEDA_Advisor_bot",
        "processing_mode": "core", "showcase_id": None,
    }
    row.update(overrides)
    return row


@pytest.fixture
def binding_store(monkeypatch):
    monkeypatch.setenv("TAP_BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("TAP_WEBHOOK_SECRET", "tap-secret")
    pool = _Pool()
    state = {"row": _binding_row()}
    monkeypatch.setattr(bindings, "get_pool", lambda: pool)
    monkeypatch.setattr(bindings, "fetch_one", AsyncMock(side_effect=lambda *_a, **_k: state["row"]))
    monkeypatch.setattr(
        bindings, "fetch_all_entitlements",
        AsyncMock(return_value=[{"feature_key": "site_support", "enabled": True}]),
    )
    bindings.clear_binding_cache()
    yield pool, state
    bindings.clear_binding_cache()


async def test_binding_is_read_once_then_served_from_the_cache(binding_store):
    pool, _state = binding_store
    first = await bindings.resolve_bot_binding_context("whieda-advisor-bot")
    second = await bindings.resolve_bot_binding_context("whieda-advisor-bot")
    assert pool.checkouts == 1
    assert first is not None and second is not None
    assert second.bot_token == "test-bot-token" and second.tenant.entitlements == {"site_support": True}
    # Каждому вызову — своя копия прав: чужая правка словаря кэш не портит.
    second.tenant.entitlements["site_support"] = False
    third = await bindings.resolve_bot_binding_context("whieda-advisor-bot")
    assert third is not None and third.tenant.entitlements == {"site_support": True}


async def test_missing_or_inactive_binding_is_not_cached(binding_store):
    pool, state = binding_store
    state["row"] = _binding_row(binding_status="paused")
    assert await bindings.resolve_bot_binding_context("whieda-advisor-bot") is None
    state["row"] = _binding_row()
    assert await bindings.resolve_bot_binding_context("whieda-advisor-bot") is not None  # включили — видно сразу
    assert pool.checkouts == 2


async def test_suspended_tenant_error_is_not_cached(binding_store):
    from fastapi import HTTPException

    pool, state = binding_store
    state["row"] = _binding_row(tenant_status="suspended")
    with pytest.raises(HTTPException) as caught:
        await bindings.resolve_bot_binding_context("whieda-advisor-bot")
    assert caught.value.status_code == 503
    state["row"] = _binding_row()
    assert await bindings.resolve_bot_binding_context("whieda-advisor-bot") is not None
    assert pool.checkouts == 2


async def test_binding_cache_expires(binding_store, monkeypatch):
    pool, _state = binding_store
    import app.ttl_cache as ttl

    now = [1000.0]
    monkeypatch.setattr(ttl, "_now", lambda: now[0])
    await bindings.resolve_bot_binding_context("whieda-advisor-bot")
    now[0] += bindings.BINDING_CACHE_TTL_SEC - 1
    await bindings.resolve_bot_binding_context("whieda-advisor-bot")
    assert pool.checkouts == 1
    now[0] += 2
    await bindings.resolve_bot_binding_context("whieda-advisor-bot")
    assert pool.checkouts == 2


# ---- Кэш форума поддержки ----------------------------------------------------------------------


@pytest.fixture
def forum_store(monkeypatch):
    from app.support import service

    reads: list[tuple] = []
    state: dict = {"row": {"tenant_id": "whieda", "binding_id": "b", "kind": "site", "chat_id": -100, "title": "Support",
                           "bonuses_thread_id": None, "reports_thread_id": 5}}

    class _Tx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_exc):
            return None

    async def fetch_one(_conn, query, params=()):
        if "from support_forums" in query:
            reads.append(tuple(params))
            return dict(state["row"]) if state["row"] is not None else None
        return {"binding_id": "b", "tenant_id": "whieda", "kind": params[-1] if params else "site",
                "chat_id": -100, "title": None}

    monkeypatch.setattr(service, "tenant_connection", lambda _tenant: _Tx())
    monkeypatch.setattr(service, "fetch_one", fetch_one)
    service.clear_forum_cache()
    yield service, reads, state
    service.clear_forum_cache()


async def test_forum_is_read_once_and_the_copy_is_safe(forum_store):
    service, reads, _state = forum_store
    first = await service.get_forum("whieda", binding_id="b", kind="site")
    second = await service.get_forum("whieda", binding_id="b", kind="site")
    assert len(reads) == 1 and first == second
    second["reports_thread_id"] = 999
    assert (await service.get_forum("whieda", binding_id="b", kind="site"))["reports_thread_id"] == 5
    await service.get_forum("whieda", binding_id="b", kind="services")  # другой вид — свой ключ
    assert len(reads) == 2


async def test_no_forum_is_remembered_too(forum_store):
    service, reads, state = forum_store
    state["row"] = None
    assert await service.get_forum("whieda", binding_id="b", kind="site") is None
    assert await service.get_forum("whieda", binding_id="b", kind="site") is None
    assert len(reads) == 1


async def test_forum_writes_drop_the_cached_row(forum_store):
    service, reads, state = forum_store
    await service.get_forum("whieda", binding_id="b", kind="site")
    await service.set_site_orders_thread("whieda", binding_id="b", thread_id=None)
    state["row"] = {**state["row"], "reports_thread_id": None}
    assert (await service.get_forum("whieda", binding_id="b", kind="site"))["reports_thread_id"] is None
    assert len(reads) == 2

    await service.register_forum("whieda", binding_id="b", chat_id=-200, title="New", registered_by=1, kind="site")
    state["row"] = {**state["row"], "chat_id": -200}
    assert (await service.get_forum("whieda", binding_id="b", kind="site"))["chat_id"] == -200
    assert len(reads) == 3

    await service.set_forum_service_threads("whieda", binding_id="b", bonuses_thread_id=1, reports_thread_id=2)
    await service.get_forum("whieda", binding_id="b", kind="services")
    await service.set_forum_service_threads("whieda", binding_id="b", bonuses_thread_id=3, reports_thread_id=4)
    await service.get_forum("whieda", binding_id="b", kind="services")
    assert len(reads) == 5
