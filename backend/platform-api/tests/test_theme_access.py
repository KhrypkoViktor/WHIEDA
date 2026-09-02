from contextlib import asynccontextmanager
from logging import INFO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app.settings import Settings
from app.theme_access.routes import site_id_from_request_host
from app.theme_access.routes import router as theme_access_router
from app.theme_access.service import (
    entitlement_payload,
    normalize_site_id,
    public_theme_payload,
    save_selected_theme,
    site_identity_aliases,
)
from app.tenancy import TenantContext

def test_platform_registers_theme_access_routes() -> None:
    main_source = Path(__file__).parents[1] / "app" / "main.py"
    source = main_source.read_text(encoding="utf-8")

    assert "from app.theme_access.routes import router as theme_access_router" in source
    assert "app.include_router(theme_access_router)" in source


def test_theme_entitlement_requires_telegram_identity_of_the_exact_site_owner() -> None:
    site = {
        "ref_code": "sofiya",
        "telegram_chat_id": "8421",
        "public_profile": {"selected_theme_id": "sankofa"},
    }

    owner = entitlement_payload(site, "8421")
    other_partner = entitlement_payload(site, "1428")

    assert owner["theme_customization_allowed"] is True
    assert owner["allowed_theme_ids"] == ["whieda-bright", "sankofa"]
    assert owner["selected_theme_id"] == "sankofa"
    assert other_partner["theme_customization_allowed"] is False
    assert other_partner["allowed_theme_ids"] == []
    assert other_partner["selected_theme_id"] == ""


def test_public_theme_omits_identity_and_rejects_invalid_site_ids() -> None:
    payload = public_theme_payload(
        {"ref_code": "sofiya", "public_profile": {"selected_theme_id": "sankofa"}}
    )
    assert payload == {"ok": True, "site_id": "sofiya", "selected_theme_id": "sankofa"}

    with pytest.raises(HTTPException) as invalid:
        normalize_site_id("../sofiya")
    assert invalid.value.status_code == 400


def test_theme_site_aliases_include_issued_subdomain_and_ref_code() -> None:
    aliases = site_identity_aliases(
        {
            "ref_code": "olga-samtsova",
            "public_profile": {"subdomain": "samtsova"},
        }
    )
    assert aliases == {"olga-samtsova", "samtsova"}
    assert site_identity_aliases({"ref_code": "onlineelena", "public_profile": {}}) == {
        "onlineelena",
        "elena",
    }


def test_theme_routes_bind_requested_site_to_the_personal_host() -> None:
    request = Request(
        {"type": "http", "method": "GET", "headers": [(b"host", b"sofiya.wwc.best")]}
    )
    assert site_id_from_request_host(request) == "sofiya"

    root_request = Request(
        {"type": "http", "method": "GET", "headers": [(b"host", b"wwc.best")]}
    )
    with pytest.raises(HTTPException) as root:
        site_id_from_request_host(root_request)
    assert root.value.status_code == 403


def test_site_nginx_proxies_theme_access_with_the_original_personal_host() -> None:
    config_path = Path(__file__).parents[2] / "deploy" / "core" / "wwc.best.nginx-core-routes.conf"
    config = config_path.read_text(encoding="utf-8")

    assert "location ^~ /api/v1/theme-access" in config
    theme_block = config.split("location ^~ /api/v1/theme-access", 1)[1]
    assert "whieda-platform/api/v1/theme-access" in theme_block
    assert "proxy_set_header X-Forwarded-Host wwc.best;" in theme_block
    assert "proxy_set_header X-WWC-Personal-Host $host;" in theme_block


def test_theme_access_site_proxy_has_an_explicit_safe_apply_script() -> None:
    script_path = (
        Path(__file__).parents[3]
        / "n8n"
        / "current"
        / "patch_site_nginx_theme_access_2026-08-31.py"
    )
    assert script_path.is_file()
    script = script_path.read_text(encoding="utf-8")
    assert 'parser.add_argument("--apply", action="store_true")' in script
    assert "nginx -t" in script
    assert "bak-theme-access" in script


@pytest.mark.anyio
async def test_entitlement_endpoint_enables_theme_only_for_the_host_site_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()
    app.include_router(theme_access_router)
    site = {
        "ref_code": "sofiya",
        "telegram_chat_id": "8421",
        "public_profile": {"selected_theme_id": "sankofa"},
    }
    tenant = TenantContext(
        tenant_id="whieda",
        status="active",
        display_name="WHIEDA",
        entitlements={"structure_basic": True},
    )
    monkeypatch.setattr("app.theme_access.routes.get_request_tenant", lambda _: tenant)
    monkeypatch.setattr("app.theme_access.routes.load_theme_site", AsyncMock(return_value=site))
    monkeypatch.setattr(
        "app.theme_access.routes.validate_content_session",
        AsyncMock(return_value={"telegram_user_id": "8421"}),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="https://wwc.best",
        cookies={"wwc_content_session": "opaque"},
    ) as client:
        owner = await client.get(
            "/api/v1/theme-access/entitlement",
            headers={"X-WWC-Personal-Host": "sofiya.wwc.best"},
        )
        assert owner.status_code == 200
        assert owner.json()["theme_customization_allowed"] is True

        monkeypatch.setattr(
            "app.theme_access.routes.validate_content_session",
            AsyncMock(return_value={"telegram_user_id": "1428"}),
        )
        other = await client.get(
            "/api/v1/theme-access/entitlement",
            headers={"X-WWC-Personal-Host": "sofiya.wwc.best"},
        )
        assert other.status_code == 200
        assert other.json()["theme_customization_allowed"] is False


def _whieda_tenant() -> TenantContext:
    return TenantContext(
        tenant_id="whieda",
        status="active",
        display_name="WHIEDA",
        entitlements={"structure_basic": True},
    )


def _entitlement_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    site: dict | None,
    telegram_user_id: str,
    temporary_free: bool,
) -> FastAPI:
    app = FastAPI()
    app.include_router(theme_access_router)
    monkeypatch.setattr("app.theme_access.routes.get_request_tenant", lambda _: _whieda_tenant())
    monkeypatch.setattr("app.theme_access.routes.load_theme_site", AsyncMock(return_value=site))
    monkeypatch.setattr(
        "app.theme_access.routes.validate_content_session",
        AsyncMock(
            return_value={"telegram_user_id": telegram_user_id} if telegram_user_id else None
        ),
    )
    monkeypatch.setattr(
        "app.theme_access.routes.get_settings",
        lambda: SimpleNamespace(temporary_free_for_verified_telegram_users=temporary_free),
    )
    return app


def test_temporary_free_flag_defaults_to_false_and_reads_env() -> None:
    settings = Settings(_env_file=None)
    assert settings.temporary_free_for_verified_telegram_users is False
    enabled = Settings(_env_file=None, temporary_free_for_verified_telegram_users=True)
    assert enabled.temporary_free_for_verified_telegram_users is True


@pytest.mark.anyio
@pytest.mark.parametrize("ref_code", ["igoref", "ladnaya", "fedorov"])
async def test_temporary_free_grants_verified_non_owner_on_enabled_profiles(
    monkeypatch: pytest.MonkeyPatch,
    ref_code: str,
) -> None:
    site = {
        "ref_code": ref_code,
        "telegram_chat_id": "8421",
        "public_profile": {"selected_theme_id": "sankofa"},
    }
    app = _entitlement_app(
        monkeypatch, site=site, telegram_user_id="1428", temporary_free=True
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="https://wwc.best",
        cookies={"wwc_content_session": "opaque"},
    ) as client:
        response = await client.get(
            "/api/v1/theme-access/entitlement",
            headers={"X-WWC-Personal-Host": f"{ref_code}.wwc.best"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["theme_customization_allowed"] is True
    assert payload["allowed_theme_ids"] == ["whieda-bright", "sankofa"]
    assert payload["selected_theme_id"] == "sankofa"
    assert payload["source_status"] == "temporary_free"


@pytest.mark.anyio
async def test_flag_off_keeps_owner_only_entitlement(monkeypatch: pytest.MonkeyPatch) -> None:
    site = {
        "ref_code": "igoref",
        "telegram_chat_id": "8421",
        "public_profile": {"selected_theme_id": "sankofa"},
    }
    app = _entitlement_app(
        monkeypatch, site=site, telegram_user_id="8421", temporary_free=False
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="https://wwc.best",
        cookies={"wwc_content_session": "opaque"},
    ) as client:
        owner = await client.get(
            "/api/v1/theme-access/entitlement",
            headers={"X-WWC-Personal-Host": "igoref.wwc.best"},
        )
        assert owner.status_code == 200
        assert owner.json()["theme_customization_allowed"] is True
        assert owner.json()["source_status"] == "site_owner"

        app2 = _entitlement_app(
            monkeypatch, site=site, telegram_user_id="1428", temporary_free=False
        )
        transport2 = ASGITransport(app=app2)
        async with AsyncClient(
            transport=transport2,
            base_url="https://wwc.best",
            cookies={"wwc_content_session": "opaque"},
        ) as client2:
            other = await client2.get(
                "/api/v1/theme-access/entitlement",
                headers={"X-WWC-Personal-Host": "igoref.wwc.best"},
            )
            assert other.status_code == 200
            assert other.json()["theme_customization_allowed"] is False
            assert other.json()["allowed_theme_ids"] == []
            assert other.json()["selected_theme_id"] == ""
            assert other.json()["source_status"] == "not_site_owner"


@pytest.mark.anyio
async def test_guest_without_valid_session_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    site = {
        "ref_code": "igoref",
        "telegram_chat_id": "8421",
        "public_profile": {"selected_theme_id": "sankofa"},
    }
    # Even with the temporary free flag on, no valid session means no answer.
    app = _entitlement_app(monkeypatch, site=site, telegram_user_id="", temporary_free=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="https://wwc.best",
        cookies={"wwc_content_session": "opaque"},
    ) as client:
        invalid = await client.get(
            "/api/v1/theme-access/entitlement",
            headers={"X-WWC-Personal-Host": "igoref.wwc.best"},
        )
        assert invalid.status_code == 401
        assert invalid.json()["detail"] == {"error": "content_session_invalid"}

    # No session cookie at all is rejected before the session lookup.
    async with AsyncClient(transport=transport, base_url="https://wwc.best") as client:
        anon = await client.get(
            "/api/v1/theme-access/entitlement",
            headers={"X-WWC-Personal-Host": "igoref.wwc.best"},
        )
    assert anon.status_code == 401
    assert anon.json()["detail"] == {"error": "content_session_required"}


@pytest.mark.anyio
async def test_unknown_disabled_and_foreign_tenant_profiles_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # load_theme_site only returns enabled rows of the request tenant, so an
    # unknown profile, a disabled profile and another tenant's profile all
    # look the same to the route: the tenant-scoped lookup finds nothing.
    lookup = AsyncMock(return_value=None)
    app = _entitlement_app(monkeypatch, site=None, telegram_user_id="1428", temporary_free=True)
    monkeypatch.setattr("app.theme_access.routes.load_theme_site", lookup)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="https://wwc.best",
        cookies={"wwc_content_session": "opaque"},
    ) as client:
        response = await client.get(
            "/api/v1/theme-access/entitlement",
            headers={"X-WWC-Personal-Host": "igoref.wwc.best"},
        )
    assert response.status_code == 404
    assert response.json()["detail"] == {"error": "site_not_found"}
    # The lookup is always bound to the request tenant, never global.
    assert lookup.await_args.args[0] == "whieda"


@pytest.mark.anyio
async def test_save_selected_theme_persists_and_audits(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    site = {
        "ref_code": "igoref",
        "telegram_chat_id": "8421",
        "public_profile": {"selected_theme_id": "whieda-bright"},
    }
    captured: dict = {}

    async def fake_fetch_one(conn, sql, params=None):
        captured["sql"] = " ".join(sql.split())
        captured["params"] = params
        return {"ref_code": "igoref", "public_profile": {"selected_theme_id": "sankofa"}}

    @asynccontextmanager
    async def fake_conn(tenant_id: str):
        captured["tenant_id"] = tenant_id
        yield object()

    monkeypatch.setattr("app.theme_access.service.tenant_connection", fake_conn)
    monkeypatch.setattr("app.theme_access.service.fetch_one", fake_fetch_one)
    monkeypatch.setattr("app.theme_access.service.load_theme_site", AsyncMock(return_value=site))

    with caplog.at_level(INFO, logger="app.observability"):
        payload = await save_selected_theme(
            "whieda",
            site_id="igoref",
            telegram_user_id="8421",
            theme_id="sankofa",
            temporary_free=False,
        )

    assert payload["ok"] is True
    assert payload["theme_customization_allowed"] is True
    assert payload["selected_theme_id"] == "sankofa"
    assert captured["tenant_id"] == "whieda"
    assert "update referral_profiles" in captured["sql"]
    assert captured["params"][:3] == ("sankofa", "whieda", "igoref")

    audit = [r.getMessage() for r in caplog.records if "theme_access.theme_saved" in r.getMessage()]
    assert len(audit) == 1
    assert "site_id=igoref" in audit[0]
    assert "telegram_user_id=8421" in audit[0]
    assert "theme_id=sankofa" in audit[0]
    assert "previous_theme_id=whieda-bright" in audit[0]
    assert "granted_via=site_owner" in audit[0]


@pytest.mark.anyio
async def test_put_entitlement_saves_for_verified_non_owner_when_temporary_free(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    site = {"ref_code": "sofiya", "telegram_chat_id": "8421", "public_profile": {}}
    app = _entitlement_app(
        monkeypatch, site=site, telegram_user_id="1428", temporary_free=True
    )
    monkeypatch.setattr("app.theme_access.service.load_theme_site", AsyncMock(return_value=site))
    captured: dict = {}

    async def fake_fetch_one(conn, sql, params=None):
        captured["params"] = params
        return {"ref_code": "sofiya", "public_profile": {"selected_theme_id": "sankofa"}}

    @asynccontextmanager
    async def fake_conn(tenant_id: str):
        captured["tenant_id"] = tenant_id
        yield object()

    monkeypatch.setattr("app.theme_access.service.tenant_connection", fake_conn)
    monkeypatch.setattr("app.theme_access.service.fetch_one", fake_fetch_one)

    with caplog.at_level(INFO, logger="app.observability"):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="https://wwc.best",
            cookies={"wwc_content_session": "opaque"},
        ) as client:
            saved = await client.put(
                "/api/v1/theme-access/entitlement",
                headers={"X-WWC-Personal-Host": "sofiya.wwc.best"},
                json={"site_id": "sofiya", "selected_theme_id": "sankofa"},
            )
            assert saved.status_code == 200
            payload = saved.json()
            assert payload["theme_customization_allowed"] is True
            assert payload["selected_theme_id"] == "sankofa"

            denied = await client.put(
                "/api/v1/theme-access/entitlement",
                headers={"X-WWC-Personal-Host": "sofiya.wwc.best"},
                json={"site_id": "sofiya", "selected_theme_id": "promo-pulse"},
            )
            assert denied.status_code == 400
            assert denied.json()["detail"] == {"error": "theme_not_allowed"}

    assert captured["tenant_id"] == "whieda"
    assert captured["params"][:3] == ("sankofa", "whieda", "sofiya")
    audit = [r.getMessage() for r in caplog.records if "theme_access.theme_saved" in r.getMessage()]
    assert len(audit) == 1
    assert "granted_via=temporary_free" in audit[0]
