"""A partner is linked to Telegram by the username the owner already entered.

The owner puts ``@username`` into Partners_Ref; the partner only presses /start.
Nobody has to look up or send a numeric id.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.leads.actor_link import link_lead_actor_by_username
from app.telegram.processor import process_core_telegram_update
from app.telegram.update_parser import parse_telegram_message


def test_parse_telegram_message_keeps_sender_username():
    update = {
        "message": {
            "text": "/start",
            "chat": {"id": 100},
            "from": {"id": 200, "username": "IgorYefimenko"},
        }
    }
    msg = parse_telegram_message(update)
    assert msg is not None
    assert msg.username == "IgorYefimenko"

    anonymous = parse_telegram_message(
        {"message": {"text": "/start", "chat": {"id": 100}, "from": {"id": 200}}}
    )
    assert anonymous is not None
    assert anonymous.username is None


@pytest.mark.anyio
async def test_link_fills_empty_chat_id_by_username(monkeypatch: pytest.MonkeyPatch):
    captured: dict = {}

    async def fake_fetch_one(conn, sql, params=None):
        captured["sql"] = " ".join(sql.split())
        captured["params"] = params
        return {"actor_id": "igoref"}

    @asynccontextmanager
    async def fake_conn(tenant_id: str):
        captured["tenant_id"] = tenant_id
        yield object()

    monkeypatch.setattr("app.leads.actor_link.tenant_connection", fake_conn)
    monkeypatch.setattr("app.leads.actor_link.fetch_one", fake_fetch_one)

    actor_id = await link_lead_actor_by_username(
        "whieda",
        username="@IgorYefimenko",
        telegram_user_id=200,
        telegram_chat_id=100,
    )

    assert actor_id == "igoref"
    assert captured["tenant_id"] == "whieda"
    assert captured["params"] == {
        "tenant_id": "whieda",
        "username": "igoryefimenko",
        "chat_id": "100",
        "user_id": 200,
    }
    sql = captured["sql"].lower()
    assert "update lead_actors" in sql
    # Only an empty chat id is filled; an existing link is never overwritten.
    assert "coalesce(telegram_chat_id, '') = ''" in sql
    assert "returning actor_id" in sql


@pytest.mark.anyio
async def test_link_without_username_does_not_touch_db(monkeypatch: pytest.MonkeyPatch):
    fetch = AsyncMock()
    monkeypatch.setattr("app.leads.actor_link.fetch_one", fetch)

    assert await link_lead_actor_by_username(
        "whieda", username=None, telegram_user_id=200, telegram_chat_id=100
    ) is None
    assert await link_lead_actor_by_username(
        "whieda", username="   ", telegram_user_id=200, telegram_chat_id=100
    ) is None
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_private_start_links_actor_before_routing(whieda_tenant, whieda_bot_binding):
    update = {
        "message": {
            "text": "/start",
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200, "username": "IgorYefimenko"},
        }
    }
    link = AsyncMock(return_value="igoref")
    with patch("app.telegram.processor.link_lead_actor_by_username", link):
        with patch(
            "app.telegram.processor.handle_newcomer_panel",
            AsyncMock(return_value={"ok": True, "route": "newcomer_panel"}),
        ):
            result = await process_core_telegram_update(
                whieda_tenant, update, "t-link", binding=whieda_bot_binding
            )
    assert result["route"] == "newcomer_panel"
    link.assert_awaited_once_with(
        "whieda", username="IgorYefimenko", telegram_user_id=200, telegram_chat_id=100
    )


@pytest.mark.asyncio
async def test_link_failure_never_blocks_the_reply(whieda_tenant, whieda_bot_binding):
    update = {
        "message": {
            "text": "/start",
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200, "username": "IgorYefimenko"},
        }
    }
    with patch(
        "app.telegram.processor.link_lead_actor_by_username",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        with patch(
            "app.telegram.processor.handle_newcomer_panel",
            AsyncMock(return_value={"ok": True, "route": "newcomer_panel"}),
        ) as panel:
            result = await process_core_telegram_update(
                whieda_tenant, update, "t-link-fail", binding=whieda_bot_binding
            )
    assert result["route"] == "newcomer_panel"
    panel.assert_awaited_once()


@pytest.mark.asyncio
async def test_group_message_does_not_link(whieda_tenant, whieda_bot_binding):
    update = {
        "message": {
            "text": "@WHIEDA_Advisor_bot привет",
            "chat": {"id": -100, "type": "supergroup"},
            "from": {"id": 200, "username": "IgorYefimenko"},
        }
    }
    link = AsyncMock()
    with patch("app.telegram.processor.link_lead_actor_by_username", link):
        with patch("app.telegram.processor.handle_advisor_query", AsyncMock(return_value={"ok": True, "route": "advisor"})):
            await process_core_telegram_update(
                whieda_tenant, update, "t-group", binding=whieda_bot_binding
            )
    link.assert_not_called()
