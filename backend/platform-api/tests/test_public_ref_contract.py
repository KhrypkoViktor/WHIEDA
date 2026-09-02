from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ref.service import load_public_ref_by_subdomain

# Fields of the legacy (pre-full-contract) response that must never change.
LEGACY_CONTRACT = {
    "ok": True,
    "ref_code": "olga",
    "display_mode": "named",
    "enabled": True,
    "profile_version": 2,
    "consultant": {
        "display_name": "Ольга",
        "page_mode": "named",
        "public_site_url": "https://example",
        "site_type": None,
        "focus_group": False,
        "access_tier": None,
    },
}

SOCIALS_CONTRACT_KEYS = {
    "telegramUrl",
    "instagramUrl",
    "vkUrl",
    "vkCommunityUrl",
    "youtubeUrl",
    "tiktokUrl",
    "telegramChannelUrl",
}


@pytest.mark.asyncio
async def test_unknown_ref_returns_404(client, monkeypatch):
    monkeypatch.setattr("app.ref.routes.load_public_ref", AsyncMock(return_value=None))
    response = await client.get("/v1/public/ref/missing", headers={"host": "wwc.best"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_public_ref_contract_shape(client, monkeypatch):
    async def fake_load(tenant_id: str, ref_code: str):
        return {
            "ref_code": ref_code,
            "display_mode": "named",
            "enabled": True,
            "profile_version": 2,
            "public_profile": {
                "display_name": "Ольга",
                "page_mode": "named",
                "public_site_url": "https://example",
            },
        }

    monkeypatch.setattr("app.ref.routes.load_public_ref", fake_load)
    response = await client.get("/v1/public/ref/olga", headers={"host": "wwc.best"})
    body = response.json()
    assert body["ok"] is True
    assert body["consultant"]["display_name"] == "Ольга"
    assert body["profile_version"] == 2


@pytest.mark.asyncio
async def test_public_ref_keeps_legacy_fields_unchanged(client, monkeypatch):
    async def fake_load(tenant_id: str, ref_code: str):
        return {
            "ref_code": "olga",
            "display_mode": "named",
            "enabled": True,
            "profile_version": 2,
            "public_profile": {
                "display_name": "Ольга",
                "page_mode": "named",
                "public_site_url": "https://example",
            },
        }

    monkeypatch.setattr("app.ref.routes.load_public_ref", fake_load)
    response = await client.get("/v1/public/ref/olga", headers={"host": "wwc.best"})
    body = response.json()

    for key, value in LEGACY_CONTRACT.items():
        if isinstance(value, dict):
            for inner_key, inner_value in value.items():
                assert body[key][inner_key] == inner_value
        else:
            assert body[key] == value

    # Additive fields stay nullable when the profile has no new data yet.
    assert body["theme"] is None
    assert body["personalPageUrl"] is None
    assert body["consultant"]["photoUrl"] is None
    assert body["consultant"]["subdomain"] is None
    assert body["consultant"]["socials"] is None


@pytest.mark.asyncio
async def test_public_ref_full_public_contract(client, monkeypatch):
    async def fake_load(tenant_id: str, ref_code: str):
        return {
            "ref_code": "olga-samtsova",
            "display_mode": "named",
            "enabled": True,
            "profile_version": 7,
            "public_profile": {
                "display_name": "Ольга Самцова",
                "page_mode": "personal_site",
                "public_site_url": "https://samtsova.wwc.best/partner/",
                "site_type": "personal",
                "focus_group": True,
                "access_tier": "pro",
                "subdomain": "samtsova",
                "photo_url": "https://media.sysarch.pro/media/partners/olga-samtsova.jpg?v=2",
                "selected_theme_id": "sankofa",
                "personal_page_url": "https://samtsova.wwc.best/partner/",
                "telegram_url": "https://t.me/olga_samtsova",
                "youtube_url": "https://www.youtube.com/@olga",
            },
        }

    monkeypatch.setattr("app.ref.routes.load_public_ref", fake_load)
    response = await client.get("/v1/public/ref/olga-samtsova", headers={"host": "wwc.best"})
    assert response.status_code == 200
    body = response.json()

    assert body["ok"] is True
    assert body["enabled"] is True
    assert body["theme"] == "sankofa"
    assert body["personalPageUrl"] == "https://samtsova.wwc.best/partner/"

    consultant = body["consultant"]
    assert consultant["display_name"] == "Ольга Самцова"
    assert consultant["page_mode"] == "personal_site"
    assert consultant["public_site_url"] == "https://samtsova.wwc.best/partner/"
    assert consultant["site_type"] == "personal"
    assert consultant["focus_group"] is True
    assert consultant["access_tier"] == "pro"
    assert consultant["photoUrl"] == "https://media.sysarch.pro/media/partners/olga-samtsova.jpg?v=2"
    assert consultant["subdomain"] == "samtsova"

    assert consultant["socials"] == {
        "telegramUrl": "https://t.me/olga_samtsova",
        "instagramUrl": None,
        "vkUrl": None,
        "vkCommunityUrl": None,
        "youtubeUrl": "https://www.youtube.com/@olga",
        "tiktokUrl": None,
        "telegramChannelUrl": None,
    }
    assert set(consultant["socials"]) == SOCIALS_CONTRACT_KEYS


@pytest.mark.asyncio
async def test_public_ref_subdomain_falls_back_to_issued_map(client, monkeypatch):
    async def fake_load(tenant_id: str, ref_code: str):
        return {
            "ref_code": "onlineelena",
            "display_mode": "named",
            "enabled": True,
            "profile_version": 3,
            "public_profile": {"display_name": "Елена Дацкевич"},
        }

    monkeypatch.setattr("app.ref.routes.load_public_ref", fake_load)
    response = await client.get("/v1/public/ref/onlineelena", headers={"host": "wwc.best"})
    body = response.json()
    assert body["consultant"]["subdomain"] == "elena"
    assert body["personalPageUrl"] == "https://elena.wwc.best/partner/"


@pytest.mark.asyncio
async def test_public_ref_photo_url_absolutized_with_media_base(client, monkeypatch):
    async def fake_load(tenant_id: str, ref_code: str):
        return {
            "ref_code": "ladnaya",
            "display_mode": "named",
            "enabled": True,
            "profile_version": 4,
            "public_profile": {
                "display_name": "Анна Ладная",
                "photo_url": "/media/partners/ladnaya.jpg",
            },
        }

    monkeypatch.setattr("app.ref.routes.load_public_ref", fake_load)
    monkeypatch.setattr(
        "app.ref.service.get_settings",
        lambda: SimpleNamespace(tenant_media_base_url="https://media.sysarch.pro"),
    )
    response = await client.get("/v1/public/ref/ladnaya", headers={"host": "wwc.best"})
    assert response.json()["consultant"]["photoUrl"] == (
        "https://media.sysarch.pro/media/partners/ladnaya.jpg"
    )


@pytest.mark.asyncio
async def test_photo_url_stays_relative_without_media_base(client, monkeypatch):
    async def fake_load(tenant_id: str, ref_code: str):
        return {
            "ref_code": "ladnaya",
            "display_mode": "named",
            "enabled": True,
            "profile_version": 4,
            "public_profile": {
                "display_name": "Анна Ладная",
                "photo_url": "/media/partners/ladnaya.jpg",
            },
        }

    monkeypatch.setattr("app.ref.routes.load_public_ref", fake_load)
    monkeypatch.setattr("app.ref.service.get_settings", lambda: SimpleNamespace(tenant_media_base_url=None))
    response = await client.get("/v1/public/ref/ladnaya", headers={"host": "wwc.best"})
    assert response.json()["consultant"]["photoUrl"] == "/media/partners/ladnaya.jpg"


@pytest.mark.asyncio
async def test_public_ref_by_subdomain_resolves_profile(client, monkeypatch):
    captured = {}

    async def fake_load_by_subdomain(tenant_id: str, subdomain: str):
        captured["tenant_id"] = tenant_id
        captured["subdomain"] = subdomain
        return {
            "ref_code": "olga-samtsova",
            "display_mode": "named",
            "enabled": True,
            "profile_version": 7,
            "public_profile": {
                "display_name": "Ольга Самцова",
                "subdomain": "samtsova",
                "photo_url": "/media/partners/olga-samtsova.jpg?v=2",
                "telegram_url": "https://t.me/olga_samtsova",
            },
        }

    monkeypatch.setattr("app.ref.routes.load_public_ref_by_subdomain", fake_load_by_subdomain)

    for path in ("/v1/public/ref/by-subdomain/samtsova", "/api/v1/public/ref/by-subdomain/samtsova"):
        response = await client.get(path, headers={"host": "wwc.best"})
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["ref_code"] == "olga-samtsova"
        assert body["theme"] is None
        assert body["personalPageUrl"] == "https://samtsova.wwc.best/partner/"
        assert body["consultant"]["subdomain"] == "samtsova"
        assert body["consultant"]["photoUrl"] == "/media/partners/olga-samtsova.jpg?v=2"
        assert body["consultant"]["socials"]["telegramUrl"] == "https://t.me/olga_samtsova"

    assert captured == {"tenant_id": "whieda", "subdomain": "samtsova"}


@pytest.mark.asyncio
async def test_public_ref_by_subdomain_unknown_returns_404(client, monkeypatch):
    monkeypatch.setattr(
        "app.ref.routes.load_public_ref_by_subdomain", AsyncMock(return_value=None)
    )
    response = await client.get(
        "/v1/public/ref/by-subdomain/nobody", headers={"host": "wwc.best"}
    )
    assert response.status_code == 404
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "subdomain_not_found"


@pytest.mark.asyncio
async def test_load_public_ref_by_subdomain_binds_tenant_and_issued_map(monkeypatch):
    captured = {}

    @asynccontextmanager
    async def fake_connection(tenant_id: str):
        captured["tenant_id"] = tenant_id
        yield object()

    async def fake_fetch_one(conn, query, params=None):
        captured["query"] = query
        captured["params"] = params
        return {"ref_code": "olga-samtsova", "public_profile": {"subdomain": "samtsova"}}

    monkeypatch.setattr("app.ref.service.tenant_connection", fake_connection)
    monkeypatch.setattr("app.ref.service.fetch_one", fake_fetch_one)

    row = await load_public_ref_by_subdomain("whieda", " Samtsova ")
    assert row["ref_code"] == "olga-samtsova"
    assert captured["tenant_id"] == "whieda"
    assert captured["params"] == ("whieda", "samtsova", "olga-samtsova", "samtsova")
    assert "enabled = true" in captured["query"]
    assert "public_profile->>'subdomain'" in captured["query"]
