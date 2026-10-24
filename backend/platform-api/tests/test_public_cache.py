"""Одинаковые для всех посетителей ответы: кэш в памяти, public-заголовки, no-store на ошибках (24.10.2026).

``/public/ref/{ref}`` и ``/theme-access/public`` не зависят ни от cookie, ни от входа. Опрос
``/content-access/challenges/{id}`` — наоборот, у каждого свой и никогда не кэшируется."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.public_cache import PUBLIC_CACHE_CONTROL, forget_public_profiles, public_ref_cache, theme_site_cache
from app.settings import get_settings
from app.tenancy import TenantContext
from app.theme_access import service as theme_service
from tests.fake_db import FakeDb

REF_URL = "/api/v1/public/ref/olga-samtsova"
SUB_URL = "/api/v1/public/ref/by-subdomain/samtsova"
THEME_URL = "/api/v1/theme-access/public"
HOST = {"host": "wwc.best"}
PERSONAL = {"host": "samtsova.wwc.best"}

ROW = {
    "ref_code": "olga-samtsova",
    "tenant_id": "whieda",
    "owner_id": "olga",
    "display_mode": "named",
    "enabled": True,
    "country_code": "BY",
    "region_code": None,
    "profile_version": 4,
    "public_profile": {"display_name": "Ольга Самцова", "subdomain": "samtsova", "selected_theme_id": "sankofa"},
}
SITE = {
    "ref_code": "olga-samtsova",
    "public_profile": {"subdomain": "samtsova", "selected_theme_id": "sankofa"},
    "telegram_chat_id": "525317405",
}


class _Clock:
    now = 7000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr("app.ttl_cache._now", fake)
    return fake


@pytest.fixture
def loader(monkeypatch):
    fake = AsyncMock(return_value=ROW)
    monkeypatch.setattr("app.ref.routes.load_public_ref", fake)
    return fake


@pytest.fixture
def sub_loader(monkeypatch):
    fake = AsyncMock(return_value=ROW)
    monkeypatch.setattr("app.ref.routes.load_public_ref_by_subdomain", fake)
    return fake


@pytest.fixture
def any_personal_host(monkeypatch):
    """Любой *.wwc.best — арендатор whieda (в боевой базе так и есть: wildcard-домен)."""

    async def resolve(host: str) -> TenantContext:
        return TenantContext(
            tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"structure_basic": True}
        )

    monkeypatch.setattr("app.tenancy.resolve_tenant_from_host", resolve)


@pytest.fixture
def site_loader(monkeypatch):
    fake = AsyncMock(return_value=SITE)
    monkeypatch.setattr("app.theme_access.routes.load_theme_site", fake)
    return fake


# ---- /public/ref ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_ref_is_read_once_and_cacheable_by_the_browser(client, loader, clock):
    first = await client.get(REF_URL, headers=HOST)
    second = await client.get(REF_URL, headers=HOST)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["ref_code"] == "olga-samtsova"
    assert first.headers["cache-control"] == second.headers["cache-control"] == PUBLIC_CACHE_CONTROL == "public, max-age=120"
    assert loader.await_count == 1


@pytest.mark.asyncio
async def test_public_ref_is_read_again_after_a_minute(client, loader, clock):
    await client.get(REF_URL, headers=HOST)
    clock.now += 59
    await client.get(REF_URL, headers=HOST)
    assert loader.await_count == 1

    clock.now += 2
    await client.get(REF_URL, headers=HOST)
    assert loader.await_count == 2


@pytest.mark.asyncio
async def test_public_ref_code_case_shares_one_entry(client, loader, clock):
    await client.get("/api/v1/public/ref/Olga-Samtsova", headers=HOST)
    await client.get("/api/v1/public/ref/olga-samtsova", headers=HOST)

    assert loader.await_count == 1


@pytest.mark.asyncio
async def test_public_ref_404_is_never_cached_and_never_stored(client, loader, clock):
    loader.return_value = None

    first = await client.get(REF_URL, headers=HOST)
    second = await client.get(REF_URL, headers=HOST)

    assert first.status_code == second.status_code == 404
    assert first.json()["error"] == "referral_not_available"
    assert first.headers["cache-control"] == "no-store"
    assert loader.await_count == 2
    assert len(public_ref_cache) == 0

    # Партнёр появился — следующий же запрос его находит.
    loader.return_value = ROW
    assert (await client.get(REF_URL, headers=HOST)).status_code == 200


@pytest.mark.asyncio
async def test_public_ref_cache_is_per_tenant(client, loader, clock):
    await client.get(REF_URL, headers=HOST)  # whieda
    await client.get(REF_URL, headers={"host": "acme.test.local"})  # test-acme

    assert [call.args[0] for call in loader.await_args_list] == ["whieda", "test-acme"]


@pytest.mark.asyncio
async def test_ref_and_subdomain_lookups_do_not_share_an_entry(client, loader, sub_loader, clock):
    await client.get("/api/v1/public/ref/samtsova", headers=HOST)
    await client.get(SUB_URL, headers=HOST)
    await client.get(SUB_URL, headers=HOST)

    assert loader.await_count == 1
    assert sub_loader.await_count == 1
    response = await client.get(SUB_URL, headers=HOST)
    assert response.headers["cache-control"] == "public, max-age=120"


@pytest.mark.asyncio
async def test_public_ref_body_does_not_depend_on_cookies_or_other_headers(client, loader, clock):
    plain = await client.get(REF_URL, headers=HOST)
    with_noise = await client.get(
        REF_URL,
        headers={**HOST, "cookie": "wwc_content_session=abc; other=1", "authorization": "Bearer x", "x-browser-nonce": "n"},
    )

    assert plain.json() == with_noise.json()
    assert loader.await_count == 1  # и кэш от них не зависит


@pytest.mark.asyncio
async def test_public_ref_without_the_feature_is_403_no_store(client, loader, monkeypatch):
    async def resolve(_host: str) -> TenantContext:
        return TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"structure_basic": False})

    monkeypatch.setattr("app.tenancy.resolve_tenant_from_host", resolve)

    response = await client.get(REF_URL, headers=HOST)

    assert response.status_code == 403
    assert response.headers["cache-control"] == "no-store"
    assert loader.await_count == 0


@pytest.mark.asyncio
async def test_public_ref_legacy_mode_is_501_no_store(client, loader, monkeypatch):
    monkeypatch.setenv("CORE_ROUTE_PUBLIC_REF", "legacy")
    get_settings.cache_clear()
    try:
        response = await client.get(REF_URL, headers=HOST)
    finally:
        get_settings.cache_clear()

    assert response.status_code == 501
    assert response.headers["cache-control"] == "no-store"
    assert loader.await_count == 0


# ---- /theme-access/public ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_public_theme_is_read_once_and_cacheable_by_the_browser(client, site_loader, clock):
    params = {"site_id": "samtsova"}
    first = await client.get(THEME_URL, params=params, headers=PERSONAL)
    second = await client.get(THEME_URL, params=params, headers=PERSONAL)

    assert first.status_code == second.status_code == 200
    assert first.json() == {"ok": True, "site_id": "olga-samtsova", "selected_theme_id": "sankofa"}
    assert first.json() == second.json()
    assert first.headers["cache-control"] == "public, max-age=120"
    assert "x-wwc-personal-host" in first.headers["vary"].lower()
    assert site_loader.await_count == 1

    clock.now += 61
    await client.get(THEME_URL, params=params, headers=PERSONAL)
    assert site_loader.await_count == 2


@pytest.mark.asyncio
async def test_public_theme_keeps_the_host_check_on_cached_sites(client, site_loader, clock, any_personal_host):
    params = {"site_id": "samtsova"}
    assert (await client.get(THEME_URL, params=params, headers=PERSONAL)).status_code == 200

    # Чужой личный адрес просит тему этого сайта — запись в кэше есть, но доступа нет.
    stranger = await client.get(THEME_URL, params=params, headers={"host": "someone-else.wwc.best"})

    assert stranger.status_code == 403
    assert stranger.json()["error"] == "site_host_mismatch"
    assert stranger.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_public_theme_errors_are_no_store_and_not_cached(client, site_loader, clock, any_personal_host):
    site_loader.return_value = None

    missing = [await client.get(THEME_URL, params={"site_id": "nobody"}, headers={"host": "nobody.wwc.best"}) for _ in range(2)]
    not_personal = await client.get(THEME_URL, params={"site_id": "samtsova"}, headers=HOST)
    bad_id = await client.get(THEME_URL, params={"site_id": "Bad_Id!"}, headers=PERSONAL)

    assert [r.status_code for r in missing] == [404, 404]
    assert not_personal.status_code == 403
    assert bad_id.status_code == 400
    for response in (*missing, not_personal, bad_id):
        assert response.headers["cache-control"] == "no-store"
    assert len(theme_site_cache) == 0


@pytest.mark.asyncio
async def test_saving_a_theme_forgets_the_cached_public_profile(client, site_loader, loader, clock, monkeypatch):
    params = {"site_id": "samtsova"}
    await client.get(THEME_URL, params=params, headers=PERSONAL)
    await client.get(REF_URL, headers=HOST)
    assert len(theme_site_cache) == 1

    db = FakeDb(lambda query, _params: [{"ref_code": "olga-samtsova", "public_profile": {"selected_theme_id": "whieda-bright"}}])
    monkeypatch.setattr(theme_service, "tenant_connection", db.tenant_connection)
    monkeypatch.setattr(theme_service, "load_theme_site", AsyncMock(return_value=SITE))

    saved = await theme_service.save_selected_theme(
        "whieda", site_id="samtsova", telegram_user_id="525317405", theme_id="whieda-bright"
    )

    assert saved["selected_theme_id"] == "whieda-bright"
    assert len(theme_site_cache) == 0
    assert len(public_ref_cache) == 0


def test_forget_public_profiles_clears_both_caches():
    public_ref_cache.set(("whieda", "ref", "x"), ROW, ttl=60)
    theme_site_cache.set(("whieda", "x"), SITE, ttl=60)

    forget_public_profiles()

    assert len(public_ref_cache) == 0 and len(theme_site_cache) == 0


# ---- /content-access/challenges/{id} ---------------------------------------------------------------

CHALLENGE = "/api/v1/content-access/challenges/6f1f2f1e-0d6b-4e0c-9d9e-5f4d1d6d9a11"


@pytest.mark.asyncio
async def test_challenge_poll_is_never_cacheable(client):
    pending = ({"ok": True, "status": "pending"}, None)
    with patch("app.content_access.routes.poll_content_challenge", AsyncMock(return_value=pending)):
        ok = await client.get(CHALLENGE, headers={**HOST, "x-browser-nonce": "n"})
    missing_nonce = await client.get(CHALLENGE, headers=HOST)
    from fastapi import HTTPException

    with patch(
        "app.content_access.routes.poll_content_challenge",
        AsyncMock(side_effect=HTTPException(status_code=404, detail={"error": "challenge_not_found"})),
    ):
        not_found = await client.get(CHALLENGE, headers={**HOST, "x-browser-nonce": "n"})

    assert (ok.status_code, missing_nonce.status_code, not_found.status_code) == (200, 400, 404)
    for response in (ok, missing_nonce, not_found):
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_approved_challenge_poll_sets_the_cookie_and_stays_no_store(client):
    approved = ({"ok": True, "status": "authenticated"}, "raw-session-token")
    with patch("app.content_access.routes.poll_content_challenge", AsyncMock(return_value=approved)):
        response = await client.get(CHALLENGE, headers={**HOST, "x-browser-nonce": "n"})

    assert response.status_code == 200
    assert "wwc_content_session=" in response.headers.get("set-cookie", "")
    assert response.headers["cache-control"] == "no-store"
