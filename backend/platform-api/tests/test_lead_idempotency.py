from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_duplicate_idempotency_returns_same_lead(client, monkeypatch):
    calls = {"count": 0}

    async def fake_save(lead):
        calls["count"] += 1
        return {
            "public_id": "L-TEST-001",
            "created": calls["count"] == 1,
            "lead_id": "00000000-0000-0000-0000-000000000001",
        }

    monkeypatch.setattr("app.leads.routes.save_lead", fake_save)

    payload = {
        "name": "Test",
        "contact": "+79990000000",
        "product": "Активатор",
        "idempotency_key": "dup-key-1",
    }
    first = await client.post("/v1/leads", json=payload, headers={"host": "wwc.best"})
    second = await client.post("/v1/leads", json=payload, headers={"host": "wwc.best"})
    assert first.status_code == 201
    assert second.status_code == 201
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_forged_owner_field_rejected(client):
    response = await client.post(
        "/v1/leads",
        json={
            "name": "Test",
            "contact": "+79990000000",
            "product": "Активатор",
            "idempotency_key": "k1",
            "owner_id": "evil",
        },
        headers={"host": "wwc.best"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_site_lead_alias_uses_same_core_handler(client, monkeypatch):
    saved: list[str] = []

    async def fake_save(lead):
        saved.append(lead.product_name)
        return {
            "public_id": "L-TEST-ALIAS",
            "created": True,
            "lead_id": "00000000-0000-0000-0000-000000000099",
        }

    monkeypatch.setattr("app.leads.routes.save_lead", fake_save)
    payload = {
        "name": "Test",
        "contact": "+79990000000",
        "product": "Корзина",
        "idempotency_key": "alias-key-1",
        "product_sku": "cart-order",
    }
    canonical = await client.post(
        "/api/v1/leads",
        json=payload,
        headers={"host": "wwc.best"},
    )
    alias = await client.post(
        "/api/lead",
        json={**payload, "idempotency_key": "alias-key-2"},
        headers={"host": "wwc.best"},
    )
    assert canonical.status_code == 201
    assert alias.status_code == 201
    assert saved == ["Корзина", "Корзина"]


@pytest.mark.asyncio
async def test_acme_lead_stays_on_acme_tenant(client, monkeypatch):
    saved = {}

    async def fake_save(lead):
        saved["tenant_id"] = lead.tenant_id
        return {
            "public_id": "L-ACME",
            "created": True,
            "lead_id": "00000000-0000-0000-0000-000000000077",
        }

    monkeypatch.setattr("app.leads.routes.save_lead", fake_save)
    response = await client.post(
        "/api/v1/leads",
        json={
            "name": "Test",
            "contact": "+79990000000",
            "product": "Активатор",
            "idempotency_key": "acme-key-1",
        },
        headers={"host": "acme.test.local"},
    )
    assert response.status_code == 201
    assert saved["tenant_id"] == "test-acme"
    assert "whieda" not in str(saved["tenant_id"])


@pytest.mark.asyncio
async def test_lead_routes_reject_get(client):
    for path in ("/v1/leads", "/api/v1/leads", "/api/lead"):
        response = await client.get(path, headers={"host": "wwc.best"})
        assert response.status_code == 405


@pytest.mark.asyncio
async def test_invalid_lead_json_is_client_error(client):
    response = await client.post(
        "/api/v1/leads",
        content=b"not-json",
        headers={"host": "wwc.best", "content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_json"
