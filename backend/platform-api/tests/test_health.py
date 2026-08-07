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
