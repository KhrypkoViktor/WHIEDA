"""Заявка с сайта → карточка через внутренний роут (25.09.2026).

На бою заявки пишет n8n, а не save_lead, поэтому n8n зовёт
POST /api/v1/leads/{public_id}/crm-card с секретом PLATFORM_INTERNAL_API_SECRET.
"""
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.settings import get_settings

from tests.test_content_access import HOST, content_app  # noqa: F401  (fixture)

HDR = "X-Platform-Internal-Secret"
SECRET = "s3cret-for-tests"


@pytest.fixture
def client(content_app):  # noqa: F811
    return AsyncClient(transport=ASGITransport(app=content_app), base_url="http://wwc.best")


async def test_closed_without_secret(client, monkeypatch):
    monkeypatch.delenv("PLATFORM_INTERNAL_API_SECRET", raising=False)
    get_settings.cache_clear()
    with patch("app.crm.routes.add_lead_card_for_public_id", new=AsyncMock()) as svc:
        resp = await client.post("/api/v1/leads/L-ABC12345/crm-card", headers=HOST)
        wrong = await client.post("/api/v1/leads/L-ABC12345/crm-card", headers={**HOST, HDR: "guess"})
    assert resp.status_code == 403 and wrong.status_code == 403
    assert resp.json()["error"] == "internal_secret_required"
    assert resp.headers["cache-control"] == "private, no-store"
    svc.assert_not_awaited()


async def test_secret_serves_route_with_tenant_from_host(client, monkeypatch):
    monkeypatch.setenv("PLATFORM_INTERNAL_API_SECRET", SECRET)
    get_settings.cache_clear()
    with patch(
        "app.crm.routes.add_lead_card_for_public_id",
        new=AsyncMock(return_value={"ok": True, "card_id": "c1", "reason": None}),
    ) as svc:
        resp = await client.post("/api/v1/leads/L-ABC12345/crm-card", headers={**HOST, HDR: SECRET})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "card_id": "c1", "reason": None}
    svc.assert_awaited_once_with("whieda", "L-ABC12345")
