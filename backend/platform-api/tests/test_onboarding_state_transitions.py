"""Onboarding FSM state transition contract tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


INTERNAL_SECRET = "test-internal-secret"
INTERNAL_HEADERS = {"host": "wwc.best", "X-Platform-Internal-Secret": INTERNAL_SECRET}


@pytest.fixture(autouse=True)
def _internal_secret(monkeypatch):
    """These routes act on a named identity and are gated since the 17.09.2026
    security audit (F021); the tests call them as Core itself does."""
    from app.settings import get_settings

    monkeypatch.setenv("PLATFORM_INTERNAL_API_SECRET", INTERNAL_SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_onboarding_enroll_returns_state(client):
    with patch(
        "app.onboarding.routes.enroll_user",
        AsyncMock(return_value={"ok": True, "status": "active", "current_day": 1}),
    ):
        resp = await client.post(
            "/v1/onboarding/enroll",
            headers=INTERNAL_HEADERS,
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
            headers=INTERNAL_HEADERS,
            json={"telegram_user_id": 999, "text": "перенести"},
        )
    assert resp.json()["status"] == "paused"
