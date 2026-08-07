"""Tests for pilot telemetry API (mocked DB)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_pilot_summary_ok(client):
    with patch(
        "app.pilot.routes.get_pilot_summary",
        AsyncMock(return_value={"ok": True, "days": 7, "totals": {}, "daily": []}),
    ):
        resp = await client.get("/v1/pilot/summary", headers={"host": "wwc.best"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_record_outcome_invalid_type(client):
    with patch("app.pilot.routes.record_outcome", AsyncMock(return_value={"ok": False, "error": "invalid_outcome_type"})):
        resp = await client.post(
            "/v1/pilot/outcomes",
            headers={"host": "wwc.best"},
            json={"outcome_type": "bad", "idempotency_key": "k1"},
        )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


@pytest.mark.asyncio
async def test_pilot_outcome_idempotent(client):
    with patch(
        "app.pilot.routes.record_outcome",
        AsyncMock(return_value={"ok": True, "created": False, "outcome_id": "x"}),
    ):
        resp = await client.post(
            "/v1/pilot/outcomes",
            headers={"host": "wwc.best"},
            json={"outcome_type": "contacted", "idempotency_key": "dup"},
        )
    assert resp.json()["created"] is False
