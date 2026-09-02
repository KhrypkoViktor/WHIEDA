from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.telegram.bindings import BotBindingContext
from app.tenancy import TenantContext


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr("app.main.init_pool", AsyncMock())
    monkeypatch.setattr("app.main.close_pool", AsyncMock())
    monkeypatch.setattr("app.main.check_postgres", AsyncMock(return_value=True))

    async def default_resolve(host: str) -> TenantContext:
        from fastapi import HTTPException

        from app.tenancy import normalize_host

        normalized = normalize_host(host)
        if normalized in {
            "wwc.best",
            "samtsova.wwc.best",
            "cabinet.staging.wwc.best",
            "cabinet.test.local",
        }:
            return TenantContext(
                tenant_id="whieda",
                status="active",
                display_name="WHIEDA",
                entitlements={
                    "structure_basic": True,
                    "partner_leads": True,
                    "deep_coach": False,
                },
            )
        if normalized == "acme.test.local":
            return TenantContext(
                tenant_id="test-acme",
                status="active",
                display_name="Acme",
                entitlements={
                    "structure_basic": True,
                    "partner_leads": True,
                    "deep_coach": False,
                },
            )
        raise HTTPException(status_code=404, detail={"error": "tenant_not_found"})

    monkeypatch.setattr("app.tenancy.resolve_tenant_from_host", default_resolve)

    application = create_app()
    application.state.http_client = AsyncMock()
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def whieda_tenant() -> TenantContext:
    return TenantContext(
        tenant_id="whieda",
        status="active",
        display_name="WHIEDA",
        entitlements={
            "structure_basic": True,
            "partner_leads": True,
            "deep_coach": False,
        },
    )


@pytest.fixture
def whieda_bot_binding(whieda_tenant: TenantContext) -> BotBindingContext:
    return BotBindingContext(
        binding_id="whieda-test-binding",
        tenant=whieda_tenant,
        bot_token_ref="env:TEST_WHIEDA_BOT_TOKEN",
        webhook_secret_ref="env:TEST_WHIEDA_WEBHOOK_SECRET",
        bot_username="WHIEDA_Advisor_bot",
        status="active",
        processing_mode="core",
        bot_token="whieda-test-token",
        webhook_secret="whieda-test-secret",
    )
