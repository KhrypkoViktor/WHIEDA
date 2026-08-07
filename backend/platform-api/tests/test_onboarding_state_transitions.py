"""Onboarding FSM state transition contract tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_onboarding_enroll_returns_state(client):
    with patch(
        "app.onboarding.routes.enroll_user",
        AsyncMock(return_value={"ok": True, "status": "active", "current_day": 1}),
    ):
        resp = await client.post(
            "/v1/onboarding/enroll",
            headers={"host": "wwc.best"},
            json={"telegram_user_id": 999, "idempotency_key": "en1"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


@pytest.mark.asyncio
async def test_onboarding_command_postpone(client):
    with patch(
        "app.onboarding.routes.handle_onboarding_text",
        AsyncMock(return_value={"ok": True, "status": "paused", "command": "postpone"}),
    ):
        resp = await client.post(
            "/v1/onboarding/command",
            headers={"host": "wwc.best"},
            json={"telegram_user_id": 999, "text": "перенести"},
        )
    assert resp.json()["status"] == "paused"
