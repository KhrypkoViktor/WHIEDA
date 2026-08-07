from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


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
