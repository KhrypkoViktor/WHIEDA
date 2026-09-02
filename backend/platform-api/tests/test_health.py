from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_health_live(client):
    response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_ready_fails_without_db(client, monkeypatch):
    monkeypatch.setattr("app.main.check_postgres", AsyncMock(return_value=False))
    response = await client.get("/health/ready")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_health_ready_includes_pool_and_outbox(client, monkeypatch):
    monkeypatch.setattr("app.main.check_postgres", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "app.main.runtime_health",
        AsyncMock(
            return_value={
                "status": "ready",
                "db_pool": {"min": 1, "max": 10, "size": 2, "available": 2, "waiting": 0},
                "outbox_pending": 0,
                "outbox_lag_sec": 0,
            }
        ),
    )
    response = await client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["db_pool"]["max"] == 10


@pytest.mark.asyncio
async def test_runtime_health_is_safe_without_pool(monkeypatch):
    from app import health as health_mod

    monkeypatch.setattr(
        "app.health.get_pool",
        lambda: (_ for _ in ()).throw(RuntimeError("no pool")),
    )
    payload = await health_mod.runtime_health()
    assert payload["status"] == "ready"
    assert payload["db_pool"] is None
    assert payload["outbox_pending"] is None
    assert payload["outbox_lag_sec"] is None
