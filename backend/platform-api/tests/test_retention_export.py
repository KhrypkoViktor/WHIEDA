"""Retention registry — export hooks only, no delete."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_retention_registry_list(client):
    with patch(
        "app.retention.routes.list_retention_registry",
        AsyncMock(return_value=[{"data_class": "operational", "table_name": "visitor_sessions", "delete_enabled": False}]),
    ):
        resp = await client.get("/v1/retention/registry", headers={"host": "wwc.best"})
    assert resp.status_code == 200
    assert resp.json()["registry"][0]["delete_enabled"] is False


@pytest.mark.asyncio
async def test_export_request_idempotent(client):
    with patch(
        "app.retention.routes.request_export",
        AsyncMock(return_value={"ok": True, "created": False, "export_id": "e1", "status": "pending"}),
    ):
        resp = await client.post(
            "/v1/retention/export-requests",
            headers={"host": "wwc.best"},
            json={"data_class": "operational", "idempotency_key": "exp1"},
        )
    assert resp.json()["created"] is False
