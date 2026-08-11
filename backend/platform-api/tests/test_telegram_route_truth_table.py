"""Mock-only route truth table for Telegram Core vs legacy paths."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.routes import _process_telegram_update_body


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


@pytest.fixture()
def whieda_binding_tenant(whieda_tenant, monkeypatch):
    monkeypatch.setattr(
        "app.telegram.routes.resolve_tenant_from_bot_binding",
        AsyncMock(return_value=whieda_tenant),
    )
    return whieda_tenant


@pytest.mark.asyncio
async def test_core_route_never_forwards_legacy(whieda_binding_tenant, monkeypatch):
    monkeypatch.setenv("CORE_ROUTE_TELEGRAM", "core")
    from app.settings import get_settings

    get_settings.cache_clear()
    update = _private_chat_update()

    with patch("app.telegram.routes._forward_to_legacy_consultant", AsyncMock()) as legacy:
        with patch("app.telegram.routes.process_core_telegram_update", AsyncMock()) as core:
            await _process_telegram_update_body("binding", update, "trace-core")
    legacy.assert_not_awaited()
    core.assert_awaited_once()


@pytest.mark.asyncio
async def test_legacy_route_forwards_consultant(whieda_binding_tenant, monkeypatch):
    monkeypatch.setenv("CORE_ROUTE_TELEGRAM", "legacy")
    from app.settings import get_settings

    get_settings.cache_clear()
    update = _private_chat_update()

    with patch("app.telegram.routes._forward_to_legacy_consultant", AsyncMock()) as legacy:
        with patch("app.telegram.routes.process_core_telegram_update", AsyncMock()) as core:
            await _process_telegram_update_body("binding", update, "trace-legacy")
    legacy.assert_awaited_once()
    core.assert_not_awaited()


@pytest.mark.asyncio
async def test_shadow_route_forwards_legacy_without_core_delivery(whieda_binding_tenant, monkeypatch):
    monkeypatch.setenv("CORE_ROUTE_TELEGRAM", "shadow")
    from app.settings import get_settings

    get_settings.cache_clear()
    update = _private_chat_update()

    core_response = {
        "answer_mode": "structured_price",
        "answer_text": "Цена 1750 BYN",
    }
    with patch("app.telegram.routes._forward_to_legacy_consultant", AsyncMock()) as legacy:
        with patch("app.telegram.routes.handle_structured_query", AsyncMock(return_value=core_response)):
            with patch("app.telegram.routes._deliver_core_answer", AsyncMock()) as deliver:
                await _process_telegram_update_body("binding", update, "trace-shadow")
    legacy.assert_awaited_once()
    deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_ignored_group_message_delivers_nothing(whieda_binding_tenant, monkeypatch):
    monkeypatch.setenv("CORE_ROUTE_TELEGRAM", "core")
    monkeypatch.setenv("PLATFORM_TELEGRAM_BOT_USERNAME", "WHIEDA_Advisor_bot")
    from app.settings import get_settings

    get_settings.cache_clear()
    update = _group_update_without_mention()

    with patch("app.telegram.routes._forward_to_legacy_consultant", AsyncMock()) as legacy:
        with patch("app.telegram.routes.process_core_telegram_update", AsyncMock()) as core:
            with patch("app.telegram.routes._deliver_core_answer", AsyncMock()) as deliver:
                await _process_telegram_update_body("binding", update, "trace-group")
    legacy.assert_not_awaited()
    core.assert_not_awaited()
    deliver.assert_not_awaited()
