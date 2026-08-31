from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

from app.theme_access.routes import site_id_from_request_host
from app.theme_access.routes import router as theme_access_router
from app.theme_access.service import (
    entitlement_payload,
    normalize_site_id,
    public_theme_payload,
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
