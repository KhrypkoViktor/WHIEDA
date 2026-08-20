from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.telegram.bindings import BotBindingContext
from app.telegram.processor import process_core_telegram_update
from app.tenancy import TenantContext


@pytest.fixture
def whieda_bot_binding() -> BotBindingContext:
    tenant = TenantContext(
        tenant_id="whieda",
        status="active",
        display_name="WHIEDA",
        entitlements={"structure_basic": True},
    )
    return BotBindingContext(
        binding_id="whieda-bot",
        tenant=tenant,
        bot_token_ref="env:WHIEDA_TELEGRAM_BOT_TOKEN",
        webhook_secret_ref="env:WHIEDA_TELEGRAM_WEBHOOK_SECRET",
        bot_username="WHIEDA_Advisor_bot",
        status="active",
        processing_mode="core",
        bot_token="whieda-test-token",
        webhook_secret="whieda-test-secret",
    )


@pytest.mark.asyncio
async def test_telegram_core_sends_answer(monkeypatch, whieda_bot_binding):
    tenant = whieda_bot_binding.tenant

    monkeypatch.setattr(
        "app.telegram.processor.handle_structured_query",
        AsyncMock(
            return_value={
                "ok": True,
                "answer_text": "Спирулина: розница 45 BYN",
                "answer_mode": "structured_price",
            }
        ),
    )
    deliver = AsyncMock(return_value={"text_sent": True, "photo_sent": False})
    monkeypatch.setattr("app.telegram.processor.deliver_advisor_response", deliver)

    update = {
        "message": {
            "chat": {"id": 12345},
            "from": {"id": 999},
            "text": "цена спирулина",
        }
    }
    result = await process_core_telegram_update(
        tenant, update, "trace-1", binding=whieda_bot_binding
    )
    assert result["route"] == "advisor"
    assert result["answer_mode"] == "structured_price"
    deliver.assert_awaited_once()
