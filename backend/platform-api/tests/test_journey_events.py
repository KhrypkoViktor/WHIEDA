"""Journey events and visitor session API tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.journey.events import parse_event_body, record_interaction_event
from app.journey.sessions import upsert_visitor_session


def test_parse_event_requires_idempotency_key():
    with pytest.raises(Exception) as exc:
        parse_event_body({"event_type": "route_opened"})
    assert exc.value.status_code == 400


def test_parse_event_rejects_invalid_type():
    with pytest.raises(Exception) as exc:
        parse_event_body({"event_type": "hack", "idempotency_key": "k1"})
    assert exc.value.status_code == 400


def test_parse_event_strips_forbidden_payload():
    parsed = parse_event_body(
        {
            "event_type": "product_viewed",
            "idempotency_key": "evt-1",
            "payload": {"sku": "SKU-1", "owner_id": "evil", "phone": "+375"},
        }
    )
    assert parsed["payload"] == {"sku": "SKU-1"}


@pytest.mark.asyncio
async def test_record_event_idempotent():
    existing_row = {
        "event_id": "e-existing",
        "event_type": "route_opened",
        "created_at": None,
    }

    @asynccontextmanager
    async def fake_conn(_tenant_id: str):
        yield object()

    with patch("app.journey.events.tenant_connection", fake_conn):
        with patch("app.journey.events.fetch_one", AsyncMock(return_value=existing_row)):
            result = await record_interaction_event(
                "whieda",
                {
                    "event_type": "route_opened",
                    "idempotency_key": "dup",
                    "visitor_session_id": None,
                    "payload": {},
                },
            )
    assert result["created"] is False
    assert result["event_id"] == "e-existing"


@pytest.mark.asyncio
async def test_first_ref_immutable_on_upsert():
    session_id = "00000000-0000-4000-8000-000000000010"

    class FakeCursor:
        executed: list[tuple] = []

        async def execute(self, sql, params=None) -> None:
            self.executed.append((sql, params))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    existing = {"session_id": session_id, "first_ref": "ladnaya", "attributed_owner_id": "o1"}

    @asynccontextmanager
    async def fake_conn(_tenant_id: str):
        yield FakeConn()

    with patch("app.journey.sessions.tenant_connection", fake_conn):
        with patch("app.journey.sessions.fetch_one", AsyncMock(return_value=existing)):
            with patch(
                "app.journey.sessions._resolve_owner_from_ref",
                AsyncMock(return_value=("evilref", "o-evil")),
            ):
                result = await upsert_visitor_session(
                    "whieda",
                    {
                        "visitor_session_id": session_id,
                        "ref": "evilref",
                        "journey_type": "product",
                    },
                )

    assert result["first_ref"] == "ladnaya"
    assert result["created"] is False


@pytest.mark.asyncio
async def test_post_interaction_event_http(client, monkeypatch):
    monkeypatch.setattr(
        "app.journey.routes.record_interaction_event",
        AsyncMock(
            return_value={
                "ok": True,
                "created": True,
                "event_id": "e1",
                "event_type": "route_opened",
            }
        ),
    )
    resp = await client.post(
        "/api/v1/interaction-events",
        json={
            "event_type": "route_opened",
            "idempotency_key": "page-home-1",
            "payload": {"route": "product"},
        },
        headers={"host": "wwc.best"},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] is True
