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
