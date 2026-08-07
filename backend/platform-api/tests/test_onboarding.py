"""Onboarding command parser and service tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.onboarding.commands import parse_onboarding_command
from app.onboarding.service import create_escalation, handle_onboarding_text


@pytest.mark.parametrize(
    "text,command",
    [
        ("мой план", "plan"),
        ("начать обучение", "start"),
        ("сделал", "done"),
        ("нужна помощь", "help"),
        ("перенести", "postpone"),
        ("мой наставник", "mentor"),
        ("остановить напоминания", "pause_reminders"),
    ],
)
def test_parse_onboarding_commands(text: str, command: str):
    parsed = parse_onboarding_command(text)
    assert parsed is not None
    assert parsed["command"] == command


def test_parse_unknown_returns_none():
    assert parse_onboarding_command("цена активатор") is None


@pytest.mark.asyncio
async def test_handle_start_without_enrollment_prompts():
    with patch("app.onboarding.service._load_enrollment", AsyncMock(return_value=None)):
        with patch(
            "app.onboarding.service.enroll_user",
            AsyncMock(return_value={"ok": True, "created": True, "answer_text": "Старт"}),
        ):
            result = await handle_onboarding_text("whieda", telegram_user_id=1, text="начать обучение")
    assert result["created"] is True


@pytest.mark.asyncio
async def test_escalation_deduplicated():
    enrollment = {
        "enrollment_id": "00000000-0000-4000-8000-000000000020",
        "program_id": "a1000000-0000-4000-8000-000000000001",
        "status": "active",
        "current_day": 3,
        "first_ref": "ladnaya",
    }

    @asynccontextmanager
    async def fake_conn(_tenant_id: str):
        yield object()

    with patch("app.onboarding.service.tenant_connection", fake_conn):
        with patch(
            "app.onboarding.service.fetch_one",
            AsyncMock(return_value={"escalation_id": "x", "status": "open"}),
        ):
            result = await create_escalation("whieda", enrollment, "не понимаю PV")
    assert result.escalation_created is False
    assert "уже отправлен" in result.answer_text


@pytest.mark.asyncio
async def test_onboarding_command_http(client, monkeypatch):
    monkeypatch.setattr(
        "app.onboarding.routes.handle_onboarding_text",
        AsyncMock(return_value={"ok": True, "answer_text": "План", "enrollment_id": "e1"}),
    )
    resp = await client.post(
        "/v1/onboarding/command",
        json={"telegram_user_id": 42, "text": "мой план"},
        headers={"host": "wwc.best"},
    )
    assert resp.status_code == 200
    assert "План" in resp.json()["answer_text"]
