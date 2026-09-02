"""P0.4.1 — product_name on GET /v1/admin/prices from advisor_structured_products."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.admin.auth.dependencies import AdminSession, resolve_effective_tenant
from app.admin.services.markets import build_prices_payload
from app.main import create_app
from app.settings import get_settings
from app.tenancy import TenantContext

SUPER_ID = 900001
OTHER_ID = 900002


def _price_row(
    *,
    sku: str,
    market_id: str = "BY",
    formatted: str = "120 BYN",
    price_state: str = "published",
) -> dict:
    return {
        "sku": sku,
        "market_id": market_id,
        "currency_code": "BYN",
        "amount": 120.0,
        "formatted": formatted,
        "price_state": price_state,
        "is_active": True,
        "updated_at": datetime(2026, 8, 12, tzinfo=timezone.utc),
    }


@pytest.fixture
def admin_app(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", str(SUPER_ID))
    monkeypatch.setenv("PLATFORM_ADMIN_CONFIRM_SECRET", "test-secret")
    monkeypatch.setenv("PLATFORM_TELEGRAM_BOT_USERNAME", "wwc_test_bot")
    monkeypatch.setenv("PLATFORM_ADMIN_COOKIE_SECURE", "false")
    get_settings.cache_clear()

    monkeypatch.setattr("app.main.init_pool", AsyncMock())
    monkeypatch.setattr("app.main.close_pool", AsyncMock())
    monkeypatch.setattr("app.main.check_postgres", AsyncMock(return_value=True))

    async def resolve(host: str) -> TenantContext:
        from app.tenancy import normalize_host

        mapping = {"cabinet.test.local": "whieda", "acme.test.local": "test-acme"}
        normalized = normalize_host(host)
        tenant_id = mapping.get(normalized, "whieda")
        return TenantContext(
            tenant_id=tenant_id,
            status="active",
            display_name=tenant_id,
            entitlements={"structure_basic": True, "partner_leads": True, "deep_coach": False},
        )

    monkeypatch.setattr("app.tenancy.resolve_tenant_from_host", resolve)
    monkeypatch.setattr("app.tenancy._load_tenant", resolve)

    application = create_app()
    application.state.http_client = AsyncMock()
    yield application
    get_settings.cache_clear()


@pytest.fixture
async def admin_client(admin_app):
    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://cabinet.test.local") as ac:
        yield ac


@pytest.mark.asyncio
async def test_prices_unauthenticated_returns_401(admin_client):
    resp = await admin_client.get("/v1/admin/prices")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_tenant_admin_cannot_read_foreign_tenant_prices():
    session = AdminSession(
        session_id="s1",
        principal_id=str(uuid.uuid4()),
        telegram_user_id=OTHER_ID,
        role="admin",
        display_name="Tenant Admin",
        allowed_tenant_ids=["whieda"],
        active_tenant_id=None,
    )

    class Req:
        state = type(
            "S",
            (),
            {"tenant": TenantContext("whieda", "active", "WHIEDA", {"partner_leads": True})},
        )()

    with pytest.raises(HTTPException) as exc:
        await resolve_effective_tenant(Req(), session, requested_tenant_id="test-acme")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_build_prices_payload_resolves_product_names():
    rows = [
        _price_row(sku="M015-00", formatted="4 800 RUB"),
        _price_row(sku="EU-N000024-24", market_id="EU", formatted="24 EUR"),
        _price_row(sku="D014", formatted="90 BYN"),
        _price_row(sku="UNKNOWN-SKU-999", formatted="—"),
    ]
    names = {
        "M015-00": "Активатор клеток",
        "EU-N000024-24": "Anion pads daily",
        "D014": "Спирулина",
    }

    with patch("app.admin.repositories.markets.list_prices", new=AsyncMock(return_value=(rows, 4))):
        with patch(
            "app.admin.repositories.products.fetch_product_names_by_skus",
            new=AsyncMock(return_value=names),
        ) as fetch_names:
            payload = await build_prices_payload("whieda", market_id=None, limit=25, offset=0)

    fetch_names.assert_awaited_once_with("whieda", ["M015-00", "EU-N000024-24", "D014", "UNKNOWN-SKU-999"])
    assert payload["ok"] is True
    assert payload["tenant_id"] == "whieda"
    assert len(payload["items"]) == 4

    by_sku = {item["sku"]: item for item in payload["items"]}
    assert by_sku["M015-00"]["product_name"] == "Активатор клеток"
    assert by_sku["EU-N000024-24"]["product_name"] == "Anion pads daily"
    assert by_sku["D014"]["product_name"] == "Спирулина"
    assert by_sku["UNKNOWN-SKU-999"]["product_name"] is None
    assert by_sku["UNKNOWN-SKU-999"]["product_name"] != "UNKNOWN-SKU-999"

    item = by_sku["M015-00"]
    assert item["market_id"] == "BY"
    assert item["formatted"] == "4 800 RUB"
    assert item["price_state"] == "published"
    assert "meta" not in item


@pytest.mark.asyncio
async def test_build_prices_payload_empty_list_skips_name_lookup():
    with patch("app.admin.repositories.markets.list_prices", new=AsyncMock(return_value=([], 0))):
        with patch(
            "app.admin.repositories.products.fetch_product_names_by_skus",
            new=AsyncMock(return_value={}),
        ) as fetch_names:
            payload = await build_prices_payload("whieda", market_id=None, limit=25, offset=0)

    fetch_names.assert_not_awaited()
    assert payload["items"] == []
    assert payload["meta"]["field_status"] == "gap"


@pytest.mark.asyncio
async def test_fetch_product_names_by_skus_batches_query():
    from app.admin.repositories import products as products_repo

    rows = [
        {"sku": "M015-00", "canonical_name": "Активатор клеток"},
        {"sku": "D014", "canonical_name": "Спирулина"},
    ]

    with patch("app.admin.repositories.products.fetch_all", new=AsyncMock(return_value=rows)):
        with patch("app.admin.repositories.products.tenant_connection") as tenant_cm:
            tenant_cm.return_value.__aenter__ = AsyncMock(return_value=object())
            tenant_cm.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await products_repo.fetch_product_names_by_skus(
                "whieda",
                ["M015-00", "D014", "M015-00", "", "  "],
            )

    assert result == {"M015-00": "Активатор клеток", "D014": "Спирулина"}
