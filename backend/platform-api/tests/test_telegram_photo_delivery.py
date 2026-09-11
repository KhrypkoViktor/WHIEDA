"""Telegram photo-first delivery and mode coverage."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.delivery import deliver_structured_advisor_response, extract_photo_url
from app.telegram.modes import (
    TELEGRAM_INTERNAL_MODES,
    TELEGRAM_STRUCTURED_MODES,
    should_deliver_telegram_response,
)
from app.telegram.processor import handle_advisor_query


def test_extract_photo_url_from_structured_media():
    assert extract_photo_url({"photo_url": "https://cdn.example/p.jpg", "videos": []}) == "https://cdn.example/p.jpg"
    assert extract_photo_url({}) is None


def test_knowledge_gap_is_delivered_when_text_present():
    assert should_deliver_telegram_response(
        {"answer_text": "Пока не могу ответить точно.", "answer_mode": "knowledge_gap"}
    )


def test_fallback_with_text_is_not_delivered():
    assert not should_deliver_telegram_response({"answer_text": "x", "answer_mode": "fallback"})
    assert "fallback" in TELEGRAM_INTERNAL_MODES


def test_all_engine_structured_modes_listed():
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
    missing = engine_modes - TELEGRAM_STRUCTURED_MODES
    assert not missing, f"missing from TELEGRAM_STRUCTURED_MODES: {missing}"


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
    assert "caption" not in photo.await_args.kwargs
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
async def test_advisor_delivery_removes_legacy_persistent_menu():
    tenant = type("T", (), {"tenant_id": "whieda"})()
    msg = type("M", (), {"chat_id": 99, "text": "активатор", "user_id": 1})()
    core = {"answer_text": "Карточка", "answer_mode": "structured_card", "media": {}}
    with patch("app.telegram.processor.handle_structured_query", AsyncMock(return_value=core)), patch(
        "app.telegram.processor.deliver_advisor_response", AsyncMock()
    ) as deliver:
        await handle_advisor_query(tenant, msg, "trace-menu")

    assert deliver.await_args.kwargs["reply_markup"] == {"remove_keyboard": True}


@pytest.mark.asyncio
async def test_knowledge_gap_triggers_delivery():
    tenant = type("T", (), {"tenant_id": "whieda"})()
    msg = type("M", (), {"chat_id": 99, "text": "что такое квантовый чай?", "user_id": 1})()
    core = {
        "answer_text": "Пока не могу ответить на этот вопрос.",
        "answer_mode": "knowledge_gap",
        "media": {"photo_url": None, "videos": [], "documents": []},
    }
    with patch("app.telegram.processor.handle_structured_query", AsyncMock(return_value=core)), patch(
        "app.telegram.processor.deliver_advisor_response", AsyncMock()
    ) as deliver:
        out = await handle_advisor_query(tenant, msg, "trace-2")
    deliver.assert_awaited_once()
    assert out["answer_mode"] == "knowledge_gap"


@pytest.mark.asyncio
async def test_fallback_mode_skips_delivery():
    tenant = type("T", (), {"tenant_id": "whieda"})()
    msg = type("M", (), {"chat_id": 99, "text": "?", "user_id": 1})()
    with patch(
        "app.telegram.processor.handle_structured_query",
        AsyncMock(return_value={"answer_text": "x", "answer_mode": "fallback"}),
    ), patch("app.telegram.processor.deliver_advisor_response", AsyncMock()) as deliver:
        await handle_advisor_query(tenant, msg, "trace-3")
    deliver.assert_not_awaited()
