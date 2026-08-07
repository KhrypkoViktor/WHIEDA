"""Telegram update parser and Core processor tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.processor import (
    handle_onboarding,
    handle_start_token,
    process_core_telegram_update,
)
from app.telegram.update_parser import parse_start_token, parse_telegram_message


def test_parse_start_token():
    assert parse_start_token("/start abc123") == "abc123"
    assert parse_start_token("/start") is None
    assert parse_start_token("привет") is None


def test_parse_telegram_message():
    update = {
        "message": {
            "text": "привет",
            "chat": {"id": 100},
            "from": {"id": 200},
        }
    }
    msg = parse_telegram_message(update)
    assert msg is not None
    assert msg.chat_id == 100
    assert msg.user_id == 200


@pytest.mark.asyncio
async def test_process_core_routes_start_token(whieda_tenant):
    update = {
        "message": {
            "text": "/start opaque-token",
            "chat": {"id": 100},
            "from": {"id": 200},
        }
    }
    with patch("app.telegram.processor.handle_start_token", AsyncMock(return_value={"ok": True, "route": "start_token"})):
        result = await process_core_telegram_update(whieda_tenant, update, "t1")
    assert result["route"] == "start_token"


@pytest.mark.asyncio
async def test_process_core_routes_onboarding_before_advisor(whieda_tenant):
    update = {
        "message": {
            "text": "мой план",
            "chat": {"id": 100},
            "from": {"id": 200},
        }
    }
    with patch("app.telegram.processor.handle_onboarding", AsyncMock(return_value={"ok": True, "route": "onboarding"})):
        with patch("app.telegram.processor.handle_advisor_query", AsyncMock()) as advisor:
            result = await process_core_telegram_update(whieda_tenant, update, "t2")
    assert result["route"] == "onboarding"
    advisor.assert_not_called()


@pytest.mark.asyncio
async def test_handle_start_token_welcomes(whieda_tenant):
    from app.identity.service import LinkTokenExchangeResult
    from app.telegram.update_parser import parse_telegram_message

    update = {"message": {"text": "/start tok", "chat": {"id": 1}, "from": {"id": 2}}}
    msg = parse_telegram_message(update)
    fake = LinkTokenExchangeResult(
        link_id="l1",
        visitor_session_id="s1",
        first_ref="ladnaya",
        attributed_owner_id="o1",
        mentor_display_name="Mentor",
        journey_type="product",
        context={"topic": "activator"},
    )
    with patch("app.telegram.processor.exchange_telegram_link_token", AsyncMock(return_value=fake)):
        with patch("app.telegram.processor.deliver_text", AsyncMock()) as deliver:
            result = await handle_start_token(whieda_tenant, msg, "tok", "trace")
    assert result["route"] == "start_token"
    deliver.assert_awaited_once()
    assert "Mentor" in deliver.await_args.args[1]
