"""Onboarding reminder scheduling tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.onboarding.reminders import schedule_next_reminder


@pytest.mark.asyncio
async def test_schedule_reminder_skips_when_paused():
    @asynccontextmanager
    async def fake_conn(_tenant_id: str):
        yield object()

    with patch("app.onboarding.reminders.tenant_connection", fake_conn):
        with patch(
            "app.onboarding.reminders.fetch_one",
            AsyncMock(return_value={"reminders_paused": True, "status": "active"}),
        ):
            result = await schedule_next_reminder("whieda", "00000000-0000-4000-8000-000000000030", 2)
    assert result is None


@pytest.mark.asyncio
async def test_schedule_reminder_idempotent():
    class FakeCursor:
        async def execute(self, *args, **kwargs) -> None:
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

    class FakeConn:
        def cursor(self):
            return FakeCursor()

    @asynccontextmanager
    async def fake_conn(_tenant_id: str):
        yield FakeConn()

    calls = {"n": 0}

    async def fake_fetch(conn, sql, params):
        calls["n"] += 1
        if "reminders_paused" in sql:
            return {"reminders_paused": False, "status": "active"}
        if calls["n"] == 2:
            return {"reminder_id": "existing"}
        return None

    with patch("app.onboarding.reminders.tenant_connection", fake_conn):
        with patch("app.onboarding.reminders.fetch_one", fake_fetch):
            result = await schedule_next_reminder("whieda", "00000000-0000-4000-8000-000000000030", 2)
    assert result is not None
    assert result["created"] is False
