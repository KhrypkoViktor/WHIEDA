"""Mock-only route truth table for Telegram Core vs legacy paths."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.routes import _process_telegram_update_body
from app.telegram.bindings import BotBindingContext


@pytest.fixture
def whieda_bot_binding(whieda_tenant):
    return BotBindingContext(
        binding_id="whieda-test-binding",
        tenant=whieda_tenant,
        bot_token_ref="env:TEST_WHIEDA_BOT_TOKEN",
        webhook_secret_ref="env:TEST_WHIEDA_WEBHOOK_SECRET",
        bot_username="WHIEDA_Advisor_bot",
        status="active",
        processing_mode="core",
        bot_token="whieda-test-token",
        webhook_secret="whieda-test-secret",
    )


def _private_chat_update(text: str = "что можешь", *, chat_id: int = 501) -> dict:
    return {
        "update_id": 7001,
        "message": {
            "text": text,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": 9001},
        },
    }


def _group_update_without_mention() -> dict:
    return {
        "update_id": 7002,
        "message": {
            "text": "обычная реплика в группе",
            "chat": {"id": -100501, "type": "supergroup"},
            "from": {"id": 9001},
        },
    }


@pytest.mark.asyncio
async def test_core_route_never_forwards_legacy(whieda_bot_binding):
    update = _private_chat_update()

    with patch("app.telegram.routes._forward_to_legacy_consultant", AsyncMock()) as legacy:
        with patch("app.telegram.routes.process_core_telegram_update", AsyncMock()) as core:
            await _process_telegram_update_body(whieda_bot_binding, update, "trace-core")
    legacy.assert_not_awaited()
    core.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_route_forwards_consultant(whieda_bot_binding):
    update = _private_chat_update()
    binding = replace(whieda_bot_binding, processing_mode="legacy")

    with patch("app.telegram.routes._forward_to_legacy_consultant", AsyncMock()) as legacy:
        with patch("app.telegram.routes.process_core_telegram_update", AsyncMock()) as core:
            await _process_telegram_update_body(binding, update, "trace-legacy")
    legacy.assert_awaited_once()
    core.assert_not_awaited()


@pytest.mark.asyncio
async def test_shadow_route_forwards_legacy_without_core_delivery(whieda_bot_binding):
    update = _private_chat_update()
    binding = replace(whieda_bot_binding, processing_mode="shadow")

    core_response = {
        "answer_mode": "structured_price",
        "answer_text": "Цена 1750 BYN",
    }
    with patch("app.telegram.routes._forward_to_legacy_consultant", AsyncMock()) as legacy:
        with patch("app.telegram.routes.handle_structured_query", AsyncMock(return_value=core_response)):
            with patch("app.telegram.routes._deliver_core_answer", AsyncMock()) as deliver:
                await _process_telegram_update_body(binding, update, "trace-shadow")
    legacy.assert_awaited_once()
    deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_ignored_group_message_delivers_nothing(whieda_bot_binding):
    update = _group_update_without_mention()

    with patch("app.telegram.routes._forward_to_legacy_consultant", AsyncMock()) as legacy:
        with patch("app.telegram.routes.process_core_telegram_update", AsyncMock()) as core:
            with patch("app.telegram.routes._deliver_core_answer", AsyncMock()) as deliver:
                await _process_telegram_update_body(
                    whieda_bot_binding,
                    update,
                    "trace-group",
                )
    legacy.assert_not_awaited()
    core.assert_not_awaited()
    deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_nsp_core_never_forwards_legacy(whieda_bot_binding):
    nsp = replace(
        whieda_bot_binding,
        binding_id="nsp-binding",
        tenant=type(whieda_bot_binding.tenant)(
            tenant_id="nsp-maxim",
            status="active",
            display_name="NSP",
            entitlements={"structure_basic": True},
        ),
        bot_username="NSP_Leader_bot",
        processing_mode="core",
        bot_token="nsp-token",
    )
    update = _private_chat_update()
    with patch("app.telegram.routes._forward_to_legacy_consultant", AsyncMock()) as legacy:
        with patch("app.telegram.routes.process_core_telegram_update", AsyncMock()) as core:
            await _process_telegram_update_body(nsp, update, "trace-nsp")
    legacy.assert_not_awaited()
    core.assert_awaited_once()


@pytest.mark.asyncio
async def test_nsp_group_whieda_mention_is_ignored(whieda_bot_binding):
    nsp = replace(
        whieda_bot_binding,
        binding_id="nsp-binding",
        tenant=type(whieda_bot_binding.tenant)(
            tenant_id="nsp-maxim",
            status="active",
            display_name="NSP",
            entitlements={"structure_basic": True},
        ),
        bot_username="NSP_Leader_bot",
        processing_mode="core",
        bot_token="nsp-token",
    )
    update = {
        "update_id": 7003,
        "message": {
            "text": "@WHIEDA_Advisor_bot цена",
            "chat": {"id": -100501, "type": "supergroup"},
            "from": {"id": 9001},
        },
    }
    with patch("app.telegram.routes._forward_to_legacy_consultant", AsyncMock()) as legacy:
        with patch("app.telegram.routes.process_core_telegram_update", AsyncMock()) as core:
            await _process_telegram_update_body(nsp, update, "trace-nsp-group")
    legacy.assert_not_awaited()
    core.assert_not_awaited()
