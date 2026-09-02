"""Telegram update parser and Core processor tests."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.telegram.admin_login import try_handle_admin_login
from app.telegram.bindings import binding_context_scope
from app.telegram.processor import (
    handle_onboarding,
    handle_start_token,
    process_core_telegram_update,
)
from app.telegram.routes import _process_telegram_update
from app.telegram.update_parser import (
    parse_start_token,
    parse_telegram_message,
    should_process_telegram_message,
)


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


def test_group_message_requires_bot_mention_or_reply():
    base = {
        "message": {
            "text": "что такое активатор",
            "chat": {"id": -100, "type": "supergroup"},
            "from": {"id": 200},
        }
    }
    assert not should_process_telegram_message(parse_telegram_message(base), "WHIEDA_Advisor_bot")

    mention = {"message": {**base["message"], "text": "@WHIEDA_Advisor_bot что такое активатор"}}
    assert should_process_telegram_message(parse_telegram_message(mention), "WHIEDA_Advisor_bot")

    reply = {
        "message": {
            **base["message"],
            "reply_to_message": {"from": {"username": "WHIEDA_Advisor_bot"}},
        }
    }
    assert should_process_telegram_message(parse_telegram_message(reply), "WHIEDA_Advisor_bot")


def test_nsp_group_mention_does_not_accept_whieda_username():
    base = {
        "message": {
            "text": "@WHIEDA_Advisor_bot что такое активатор",
            "chat": {"id": -100, "type": "supergroup"},
            "from": {"id": 200},
        }
    }
    nsp = {
        "message": {
            "text": "@NSP_Leader_bot что такое активатор",
            "chat": {"id": -100, "type": "supergroup"},
            "from": {"id": 200},
        }
    }
    assert not should_process_telegram_message(
        parse_telegram_message(base), "NSP_Leader_bot"
    )
    assert should_process_telegram_message(parse_telegram_message(nsp), "NSP_Leader_bot")


@pytest.mark.asyncio
async def test_group_message_is_ignored_before_advisor(
    whieda_tenant, whieda_bot_binding
):
    update = {
        "message": {
            "text": "обычная реплика в группе",
            "chat": {"id": -100, "type": "supergroup"},
            "from": {"id": 200},
        }
    }
    with patch("app.telegram.processor.handle_advisor_query", AsyncMock()) as advisor:
        result = await process_core_telegram_update(
            whieda_tenant,
            update,
            "group-ignore",
            binding=whieda_bot_binding,
        )
    assert result["route"] == "ignored_group_message"
    advisor.assert_not_called()


@pytest.mark.asyncio
async def test_process_core_routes_start_token(whieda_tenant, whieda_bot_binding):
    update = {
        "message": {
            "text": "/start opaque-token",
            "chat": {"id": 100},
            "from": {"id": 200},
        }
    }
    with patch("app.telegram.processor.handle_start_token", AsyncMock(return_value={"ok": True, "route": "start_token"})):
        result = await process_core_telegram_update(
            whieda_tenant,
            update,
            "t1",
            binding=whieda_bot_binding,
        )
    assert result["route"] == "start_token"


@pytest.mark.asyncio
async def test_process_core_routes_onboarding_before_advisor(
    whieda_tenant, whieda_bot_binding
):
    update = {
        "message": {
            "text": "мой план",
            "chat": {"id": 100},
            "from": {"id": 200},
        }
    }
    with patch("app.telegram.processor.handle_onboarding", AsyncMock(return_value={"ok": True, "route": "onboarding"})):
        with patch("app.telegram.processor.handle_advisor_query", AsyncMock()) as advisor:
            result = await process_core_telegram_update(
                whieda_tenant,
                update,
                "t2",
                binding=whieda_bot_binding,
            )
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


@pytest.mark.asyncio
async def test_admin_login_start_calls_confirm_not_start_token(
    whieda_tenant, whieda_bot_binding
):
    update = {
        "update_id": 555001,
        "message": {
            "text": "/start admin_login_smoke_token_abc",
            "chat": {"id": 100},
            "from": {"id": 200},
        },
    }
    confirm = AsyncMock(return_value={"ok": True, "status": "approved", "challenge_id": "c1"})
    with patch("app.telegram.admin_login.confirm_login_from_telegram", confirm):
        with patch("app.telegram.admin_login.deliver_text", AsyncMock()) as deliver:
            with patch("app.telegram.processor.handle_start_token", AsyncMock()) as start_token:
                result = await process_core_telegram_update(
                    whieda_tenant,
                    update,
                    "t-admin",
                    binding=whieda_bot_binding,
                )
    assert result["route"] == "admin_login"
    assert result["update_id"] == 555001
    confirm.assert_awaited_once_with(
        challenge_token="admin_login_smoke_token_abc",
        telegram_user_id=200,
    )
    start_token.assert_not_called()
    deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_login_unknown_user_neutral_refusal(whieda_bot_binding):
    update = {
        "update_id": 555002,
        "message": {
            "text": "/start admin_login_rejected",
            "chat": {"id": 101},
            "from": {"id": 999},
        },
    }
    with patch(
        "app.telegram.admin_login.confirm_login_from_telegram",
        AsyncMock(
            side_effect=HTTPException(status_code=403, detail={"error": "admin_not_allowed"}),
        ),
    ):
        with patch("app.telegram.admin_login.deliver_text", AsyncMock()) as deliver:
            with binding_context_scope(whieda_bot_binding):
                result = await try_handle_admin_login(update, trace_id="t-refuse")
    assert result["ok"] is False
    assert result["status"] == "admin_not_allowed"
    message = deliver.await_args.args[1]
    assert "администратор" not in message.lower()
    assert "список" not in message.lower()


@pytest.mark.asyncio
async def test_legacy_route_intercepts_admin_login_before_forward(
    whieda_bot_binding,
):
    update = {
        "update_id": 555003,
        "message": {
            "text": "/start admin_login_legacy",
            "chat": {"id": 102},
            "from": {"id": 201},
        },
    }
    with patch(
        "app.telegram.routes.try_handle_admin_login",
        AsyncMock(return_value={"ok": True, "route": "admin_login"}),
    ) as admin_login:
        with patch("app.telegram.routes._forward_to_legacy_consultant", AsyncMock()) as legacy:
            await _process_telegram_update(
                replace(whieda_bot_binding, processing_mode="legacy"),
                update,
                "trace-legacy",
            )
    admin_login.assert_awaited_once()
    legacy.assert_not_called()


@pytest.mark.asyncio
async def test_content_access_start_runs_after_admin_before_identity(
    whieda_tenant, whieda_bot_binding
):
    update = {
        "update_id": 555010,
        "message": {
            "text": "/start content_access_tokXYZ",
            "chat": {"id": 110},
            "from": {"id": 210},
        },
    }
    confirm = AsyncMock(return_value={"ok": True, "status": "approved", "challenge_id": "c-content"})
    with patch("app.telegram.content_access.confirm_content_from_telegram", confirm):
        with patch("app.telegram.content_access.deliver_text", AsyncMock()) as deliver:
            with patch("app.telegram.processor.handle_start_token", AsyncMock()) as start_token:
                result = await process_core_telegram_update(
                    whieda_tenant,
                    update,
                    "t-content",
                    binding=whieda_bot_binding,
                )
    assert result["route"] == "content_access"
    confirm.assert_awaited_once_with(
        tenant_id="whieda",
        challenge_token="content_access_tokXYZ",
        telegram_user_id=210,
        telegram_chat_id=110,
    )
    start_token.assert_not_called()
    message = deliver.await_args.args[1]
    assert "Доступ к материалам подтверждён" in message
    assert "стельк" not in message.lower()
    assert "стать" not in message.lower()


@pytest.mark.asyncio
async def test_legacy_route_intercepts_content_access_before_forward(whieda_bot_binding):
    update = {
        "update_id": 555011,
        "message": {
            "text": "/start content_access_legacy",
            "chat": {"id": 112},
            "from": {"id": 212},
        },
    }
    with patch("app.telegram.routes.try_handle_admin_login", AsyncMock(return_value=None)):
        with patch(
            "app.telegram.routes.try_handle_content_access",
            AsyncMock(return_value={"ok": True, "route": "content_access"}),
        ) as content_access:
            with patch("app.telegram.routes._forward_to_legacy_consultant", AsyncMock()) as legacy:
                await _process_telegram_update(
                    replace(whieda_bot_binding, processing_mode="legacy"),
                    update,
                    "trace-legacy-content",
                )
    content_access.assert_awaited_once()
    legacy.assert_not_called()
