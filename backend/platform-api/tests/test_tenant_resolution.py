from __future__ import annotations

import pytest

from app.tenancy import normalize_host


def test_normalize_host_strips_port_and_lowercases():
    assert normalize_host("WWC.Best:443") == "wwc.best"
    assert normalize_host("  Samtsova.WWC.Best ") == "samtsova.wwc.best"


@pytest.mark.asyncio
async def test_unknown_host_raises_not_found(client):
    response = await client.get(
        "/v1/public/ref/test",
        headers={"host": "unknown.example"},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "tenant_not_found"


@pytest.mark.asyncio
async def test_forged_tenant_body_does_not_change_host_resolution(client, monkeypatch):
    captured = {}

    async def fake_load(tenant_id: str, ref_code: str):
        captured["tenant_id"] = tenant_id
        return None

    monkeypatch.setattr("app.ref.routes.load_public_ref", fake_load)

    response = await client.get(
        "/v1/public/ref/ladnaya",
        headers={"host": "wwc.best"},
    )
    assert response.status_code == 404
    assert captured["tenant_id"] == "whieda"
