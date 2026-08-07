from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.telegram.processor import process_core_telegram_update


@pytest.mark.asyncio
async def test_telegram_core_sends_answer(monkeypatch):
    tenant = type(
        "T",
        (),
        {
            "tenant_id": "whieda",
            "entitlements": {"structure_basic": True},
        },
    )()

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
    result = await process_core_telegram_update(tenant, update, "trace-1")
    assert result["route"] == "advisor"
    assert result["answer_mode"] == "structured_price"
    deliver.assert_awaited_once()
