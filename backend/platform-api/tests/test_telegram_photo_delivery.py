"""Telegram photo-first delivery and mode coverage."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.delivery import deliver_structured_advisor_response, extract_photo_url, send_telegram_photo
from app.telegram.modes import TELEGRAM_DELIVERABLE_MODES, is_telegram_deliverable
from app.telegram.processor import deliver_advisor_response, handle_advisor_query


def test_extract_photo_url_from_structured_media():
    assert extract_photo_url({"photo_url": "https://cdn.example/p.jpg", "videos": []}) == "https://cdn.example/p.jpg"
    assert extract_photo_url({}) is None


def test_product_detail_mode_is_deliverable():
    assert is_telegram_deliverable("structured_product_detail")
    assert is_telegram_deliverable("structured_comparison_layer")
    assert not is_telegram_deliverable("fallback")
    assert not is_telegram_deliverable(None)


def test_all_engine_modes_in_deliverable_set():
    engine_modes = {
        "structured_price",
        "structured_card",
        "structured_photo",
        "structured_video",
        "structured_certificate",
        "structured_product_detail",
        "structured_comparison",
        "structured_comparison_layer",
        "structured_business",
        "structured_business_faq",
        "structured_business_objection",
        "structured_promotion",
        "structured_event",
        "structured_community",
        "structured_starter_basket",
        "structured_cart",
        "clarification",
    }
    missing = engine_modes - TELEGRAM_DELIVERABLE_MODES
    assert not missing, f"missing from TELEGRAM_DELIVERABLE_MODES: {missing}"


@pytest.mark.asyncio
async def test_photo_then_text_separate_messages():
    with patch("app.telegram.delivery.send_telegram_photo", AsyncMock(return_value={"ok": True})) as photo, patch(
        "app.telegram.delivery.send_telegram_text", AsyncMock(return_value={"ok": True})
    ) as text:
        result = await deliver_structured_advisor_response(
            123,
            {
                "answer_text": "Подробное описание товара",
                "media": {"photo_url": "https://cdn/p.jpg", "videos": [], "documents": []},
            },
            bot_token="tok",
        )
    photo.assert_awaited_once()
    call_kwargs = photo.await_args.kwargs
    assert "caption" not in call_kwargs
    text.assert_awaited_once()
    assert text.await_args.kwargs["text"] == "Подробное описание товара"
    assert result["photo_sent"] is True
    assert result["text_sent"] is True


@pytest.mark.asyncio
async def test_photo_failure_still_sends_text():
    with patch(
        "app.telegram.delivery.send_telegram_photo",
        AsyncMock(return_value={"ok": False, "status_code": 400}),
    ) as photo, patch(
        "app.telegram.delivery.send_telegram_text", AsyncMock(return_value={"ok": True})
    ) as text:
        result = await deliver_structured_advisor_response(
            456,
            {
                "answer_text": "Текст после ошибки фото",
                "media": {"photo_url": "https://cdn/bad.jpg", "videos": [], "documents": []},
            },
            bot_token="tok",
        )
    photo.assert_awaited_once()
    text.assert_awaited_once()
    assert result["photo_sent"] is False
    assert result["text_sent"] is True


@pytest.mark.asyncio
async def test_product_detail_triggers_delivery():
    tenant = type("T", (), {"tenant_id": "whieda"})()
    msg = type("M", (), {"chat_id": 99, "text": "подробнее", "user_id": 1})()
    core = {
        "answer_text": "Детали продукта",
        "answer_mode": "structured_product_detail",
        "media": {"photo_url": None, "videos": [], "documents": []},
    }
    with patch("app.telegram.processor.handle_structured_query", AsyncMock(return_value=core)), patch(
        "app.telegram.processor.deliver_advisor_response", AsyncMock()
    ) as deliver:
        out = await handle_advisor_query(tenant, msg, "trace-1")
    deliver.assert_awaited_once()
    assert out["answer_mode"] == "structured_product_detail"


@pytest.mark.asyncio
async def test_fallback_mode_skips_delivery():
    tenant = type("T", (), {"tenant_id": "whieda"})()
    msg = type("M", (), {"chat_id": 99, "text": "?", "user_id": 1})()
    with patch(
        "app.telegram.processor.handle_structured_query",
        AsyncMock(return_value={"answer_text": "x", "answer_mode": "fallback"}),
    ), patch("app.telegram.processor.deliver_advisor_response", AsyncMock()) as deliver:
        await handle_advisor_query(tenant, msg, "trace-2")
    deliver.assert_not_awaited()
