"""Арендатор по хосту: один заход в базу на хост за минуту, а не шесть обращений на каждый запрос (24.10.2026).

Подменён пул: считаем занятые подключения и выполненные запросы. База настоящая здесь не нужна —
проверяется, сколько раз код в неё ходит и что отдаёт после кэша."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient

import app.tenancy as tenancy
from app.tenancy import (
    TENANT_CACHE_MAX_HOSTS,
    TENANT_CACHE_NEGATIVE_TTL_SEC,
    TENANT_CACHE_TTL_SEC,
    TenantContext,
    TenantMiddleware,
    clear_tenant_cache,
    resolve_tenant_from_host,
)
from app.ttl_cache import TtlCache
from tests.fake_db import FakeDb


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr("app.ttl_cache._now", fake)
    return fake


class _Pool(FakeDb):
    """Две таблицы: домены → арендатор и права арендатора."""

    def __init__(self) -> None:
        super().__init__(self._answer)
        self.domains: dict[str, dict] = {
            "wwc.best": {"tenant_id": "whieda", "status": "active", "display_name": "WHIEDA"},
            "frozen.example": {"tenant_id": "frozen", "status": "suspended", "display_name": "Frozen"},
            "draft.example": {"tenant_id": "draft", "status": "pending", "display_name": "Draft"},
        }
        self.entitlements = {"whieda": [("structure_basic", True), ("crm", False)]}

    @property
    def checkouts(self) -> int:
        return self.opened

    @property
    def statement_count(self) -> int:
        return len(self.statements)

    def _answer(self, query: str, params) -> list[dict]:
        if "from tenant_domains" in query:
            row = self.domains.get(params[0])
            return [row] if row else []
        if "from tenant_entitlements" in query:
            return [{"feature_key": key, "enabled": enabled} for key, enabled in self.entitlements.get(params[0], [])]
        raise AssertionError(f"unexpected statement: {query}")


@pytest.fixture
def pool(monkeypatch):
    fake = _Pool()
    monkeypatch.setattr("app.tenancy.get_pool", lambda: fake)
    clear_tenant_cache()
    yield fake
    clear_tenant_cache()


@pytest.mark.asyncio
async def test_first_lookup_is_one_checkout_two_statements(pool):
    tenant = await resolve_tenant_from_host("wwc.best")

    assert tenant == TenantContext(
        tenant_id="whieda",
        status="active",
        display_name="WHIEDA",
        entitlements={"structure_basic": True, "crm": False},
    )
    assert pool.checkouts == 1
    assert pool.statement_count == 2


@pytest.mark.asyncio
async def test_second_lookup_within_ttl_touches_no_database(pool, clock):
    first = await resolve_tenant_from_host("wwc.best")
    checkouts, statements = pool.checkouts, pool.statement_count

    clock.now += TENANT_CACHE_TTL_SEC - 1
    second = await resolve_tenant_from_host("WWC.Best:443")  # тот же хост после нормализации

    assert second == first
    assert (pool.checkouts, pool.statement_count) == (checkouts, statements)


@pytest.mark.asyncio
async def test_lookup_after_ttl_refreshes_and_sees_new_entitlements(pool, clock):
    await resolve_tenant_from_host("wwc.best")
    pool.entitlements["whieda"] = [("structure_basic", True), ("crm", True)]

    clock.now += TENANT_CACHE_TTL_SEC + 1
    refreshed = await resolve_tenant_from_host("wwc.best")

    assert refreshed.entitlements["crm"] is True
    assert pool.checkouts == 2
    assert pool.statement_count == 4


@pytest.mark.asyncio
async def test_cached_entitlements_are_a_copy_per_request(pool):
    first = await resolve_tenant_from_host("wwc.best")
    first.entitlements["crm"] = True  # чужая правка в одном запросе

    second = await resolve_tenant_from_host("wwc.best")

    assert second.entitlements["crm"] is False


@pytest.mark.asyncio
async def test_unknown_host_is_cached_briefly_then_seen_again(pool, clock):
    for _ in range(2):
        with pytest.raises(HTTPException) as caught:
            await resolve_tenant_from_host("new.example")
        assert caught.value.status_code == 404
        assert caught.value.detail == {"error": "tenant_not_found"}
    assert (pool.checkouts, pool.statement_count) == (1, 1)

    # Домен появился в базе: после короткого срока хост находится.
    pool.domains["new.example"] = {"tenant_id": "whieda", "status": "active", "display_name": "WHIEDA"}
    clock.now += TENANT_CACHE_NEGATIVE_TTL_SEC + 1
    tenant = await resolve_tenant_from_host("new.example")

    assert tenant.tenant_id == "whieda"


@pytest.mark.asyncio
async def test_negative_ttl_is_shorter_than_positive():
    assert TENANT_CACHE_NEGATIVE_TTL_SEC < TENANT_CACHE_TTL_SEC
    assert (TENANT_CACHE_TTL_SEC, TENANT_CACHE_NEGATIVE_TTL_SEC, TENANT_CACHE_MAX_HOSTS) == (60.0, 10.0, 512)


@pytest.mark.asyncio
async def test_suspended_and_inactive_tenants_keep_their_errors_from_cache(pool):
    for host, status, error in (
        ("frozen.example", 503, "tenant_suspended"),
        ("draft.example", 404, "tenant_not_found"),
    ):
        for _ in range(2):
            with pytest.raises(HTTPException) as caught:
                await resolve_tenant_from_host(host)
            assert caught.value.status_code == status
            assert caught.value.detail == {"error": error}
    # Права у неактивного арендатора не читались, у остановленного тоже.
    assert pool.checkouts == 2
    assert pool.statement_count == 2


@pytest.mark.asyncio
async def test_database_failure_is_not_cached(pool):
    pool.fail = True
    with pytest.raises(RuntimeError):
        await resolve_tenant_from_host("wwc.best")

    pool.fail = False
    tenant = await resolve_tenant_from_host("wwc.best")

    assert tenant.tenant_id == "whieda"


@pytest.mark.asyncio
async def test_clear_tenant_cache_forces_a_fresh_read(pool):
    await resolve_tenant_from_host("wwc.best")
    clear_tenant_cache()
    await resolve_tenant_from_host("wwc.best")

    assert pool.checkouts == 2


@pytest.mark.asyncio
async def test_empty_host_is_not_found_without_database(pool):
    with pytest.raises(HTTPException) as caught:
        await resolve_tenant_from_host("")

    assert caught.value.status_code == 404
    assert pool.checkouts == 0


@pytest.mark.asyncio
async def test_default_host_tenant_outside_production_reuses_one_checkout(pool, monkeypatch):
    pool.domains.clear()
    pool.tenants = {"whieda": {"tenant_id": "whieda", "status": "active", "display_name": "WHIEDA"}}
    original_answer = pool._answer

    def answer(query, params):
        if "from tenants where tenant_id" in query:
            row = pool.tenants.get(params[0])
            return [row] if row else []
        return original_answer(query, params)

    pool.answer = answer
    from app.settings import Settings

    settings = Settings(environment="development", default_host_tenant="whieda")
    monkeypatch.setattr("app.tenancy.get_settings", lambda: settings)

    tenant = await resolve_tenant_from_host("localhost")
    await resolve_tenant_from_host("localhost")

    assert tenant.tenant_id == "whieda"
    assert pool.checkouts == 1  # домены → арендатор → права: всё на одном подключении
    assert pool.statement_count == 3


@pytest.mark.asyncio
async def test_middleware_hits_the_database_once_for_many_requests(pool):
    application = FastAPI()
    application.add_middleware(TenantMiddleware)

    @application.get("/ping")
    async def ping(request: Request) -> dict:
        return {"tenant": request.state.tenant.tenant_id}

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://wwc.best") as client:
        bodies = [(await client.get("/ping")).json() for _ in range(5)]
        unknown = [await client.get("/ping", headers={"host": "nobody.example"}) for _ in range(3)]

    assert bodies == [{"tenant": "whieda"}] * 5
    assert [response.status_code for response in unknown] == [404, 404, 404]
    # wwc.best: подключение и два запроса один раз; nobody.example: подключение и один запрос один раз.
    assert pool.checkouts == 2
    assert pool.statement_count == 3


def test_cache_is_bounded_and_drops_the_oldest_host(monkeypatch):
    cache: TtlCache[int] = TtlCache(max_entries=3)
    for index in range(5):
        cache.set(f"host{index}", index, ttl=60)

    assert len(cache) == 3
    assert cache.get("host0") is None
    assert cache.get("host1") is None
    assert [cache.get(f"host{index}") for index in (2, 3, 4)] == [2, 3, 4]


def test_resolved_tenant_cache_is_capped_at_the_documented_size():
    assert tenancy._tenant_cache._max_entries == TENANT_CACHE_MAX_HOSTS
