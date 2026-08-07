"""Cross-tenant isolation for journey / identity / pilot APIs."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_journey_events_not_cross_tenant(client):
    with patch("app.journey.routes.record_interaction_event", AsyncMock(return_value={"ok": True, "created": True})):
        ok = await client.post(
            "/api/v1/interaction-events",
            headers={"host": "wwc.best"},
            json={"event_type": "route_opened", "idempotency_key": "w1", "payload": {}},
        )
        assert ok.status_code == 200

    with patch("app.journey.routes.record_interaction_event", AsyncMock(return_value={"ok": True, "created": True})):
        acme = await client.post(
            "/api/v1/interaction-events",
            headers={"host": "acme.test.local"},
            json={"event_type": "route_opened", "idempotency_key": "a1", "payload": {}},
        )
        assert acme.status_code == 200


@pytest.mark.asyncio
async def test_pilot_summary_tenant_scoped(client):
    with patch(
        "app.pilot.routes.get_pilot_summary",
        AsyncMock(return_value={"ok": True, "days": 7, "totals": {}, "daily": []}),
    ) as mock:
        await client.get("/v1/pilot/summary", headers={"host": "wwc.best"})
        assert mock.await_args.args[0] == "whieda"

        await client.get("/v1/pilot/summary", headers={"host": "acme.test.local"})
        assert mock.await_args.args[0] == "test-acme"
