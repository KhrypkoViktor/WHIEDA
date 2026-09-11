from __future__ import annotations

import hashlib
import hmac
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_edge_snapshot_requires_secret(client, monkeypatch):
    monkeypatch.setattr(
        "app.subscriptions.edge_routes.get_settings",
        lambda: SimpleNamespace(platform_edge_snapshot_secret="edge-test-secret"),
    )
    export = AsyncMock()
    monkeypatch.setattr("app.subscriptions.edge_routes.export_active_partner_hosts", export)

    response = await client.get(
        "/v1/internal/edge/partner-hosts",
        headers={"host": "wwc.best"},
    )

    assert response.status_code == 404
    assert export.await_count == 0


@pytest.mark.asyncio
async def test_edge_snapshot_is_tenant_scoped_and_signed(client, monkeypatch):
    secret = "edge-test-secret"
    payload = {
        "tenant_id": "whieda",
        "generated_at": "2026-09-09T12:00:00+00:00",
        "version": "a" * 64,
        "allowed_hosts": ["active.wwc.best", "grace.wwc.best"],
    }
    monkeypatch.setattr(
        "app.subscriptions.edge_routes.get_settings",
        lambda: SimpleNamespace(platform_edge_snapshot_secret=secret),
    )
    export = AsyncMock(return_value=payload)
    monkeypatch.setattr("app.subscriptions.edge_routes.export_active_partner_hosts", export)

    response = await client.get(
        "/v1/internal/edge/partner-hosts",
        headers={"host": "wwc.best", "x-wwc-edge-secret": secret},
    )

    assert response.status_code == 200
    assert response.json() == payload
    assert response.headers["cache-control"] == "private, no-store"
    expected = hmac.new(secret.encode(), response.content, hashlib.sha256).hexdigest()
    assert response.headers["x-wwc-snapshot-signature"] == f"sha256={expected}"
    export.assert_awaited_once_with("whieda")
