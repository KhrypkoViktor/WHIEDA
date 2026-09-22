"""«Закажу сайт» / «сколько стоит подписка» are answered by WWC, not by the
WHIEDA advisor (owner, 22.09.2026)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.update_parser import parse_telegram_message
from app.telegram.wwc_services import (
    PRICE_TEXT,
    SITE_ORDER_TEXT,
    is_our_price_question,
    is_site_order_request,
    try_handle_wwc_service_text,
)
from app.tenancy import TenantContext

WHIEDA = TenantContext(
    tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={}
)


def _msg(text: str):
    return parse_telegram_message(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "text": text,
                "chat": {"id": 555, "type": "private"},
                "from": {"id": 555, "first_name": "T"},
            },
        }
    )


@pytest.mark.parametrize(
    "text",
    ["хочу заказать сайт", "нужен сайт", "сайт", "закажу сайт", "хочу свой сайт", "оформить платформу"],
)
def test_site_order_phrases(text: str) -> None:
    assert is_site_order_request(text)


@pytest.mark.parametrize(
    "text",
    ["сколько стоит активатор", "цена красного эликсира", "/start", "мой сайт", ""],
)
def test_not_a_site_order(text: str) -> None:
    assert not is_site_order_request(text)


@pytest.mark.parametrize(
    "text", ["сколько стоит подписка", "цена клуба", "стоимость платформы", "сколько стоит сайт wwc"]
)
def test_our_price_questions(text: str) -> None:
    assert is_our_price_question(text)


@pytest.mark.parametrize("text", ["цена", "сколько стоит активатор", "цена сауны"])
def test_catalogue_price_questions_stay_with_the_advisor(text: str) -> None:
    assert not is_our_price_question(text)


@pytest.mark.asyncio
async def test_site_order_answers_with_the_order_button() -> None:
    with patch("app.telegram.wwc_services.current_bot_binding") as binding:
        binding.return_value.bot_token = "token"
        with patch("app.telegram.wwc_services.send_telegram_text", AsyncMock()) as send:
            result = await try_handle_wwc_service_text(
                WHIEDA, _msg("хочу заказать сайт"), trace_id="t-1"
            )
    assert result == {"ok": True, "route": "wwc_site_order", "trace_id": "t-1"}
    kwargs = send.await_args.kwargs
    assert kwargs["text"] == SITE_ORDER_TEXT
    buttons = [b for row in kwargs["reply_markup"]["inline_keyboard"] for b in row]
    assert any(b["callback_data"] == "site:create" for b in buttons)


@pytest.mark.asyncio
async def test_price_question_answers_with_the_price_list() -> None:
    with patch("app.telegram.wwc_services.current_bot_binding") as binding:
        binding.return_value.bot_token = "token"
        with patch("app.telegram.wwc_services.send_telegram_text", AsyncMock()) as send:
            result = await try_handle_wwc_service_text(
                WHIEDA, _msg("сколько стоит подписка"), trace_id="t-2"
            )
    assert result == {"ok": True, "route": "wwc_prices", "trace_id": "t-2"}
    assert send.await_args.kwargs["text"] == PRICE_TEXT
    assert "96 WWC$" in PRICE_TEXT  # 12 месяцев — 9 600 ₽ (owner, 22.09.2026)


@pytest.mark.asyncio
async def test_other_text_is_left_to_the_advisor() -> None:
    with patch("app.telegram.wwc_services.send_telegram_text", AsyncMock()) as send:
        result = await try_handle_wwc_service_text(
            WHIEDA, _msg("сколько стоит активатор"), trace_id="t-3"
        )
    assert result is None
    send.assert_not_awaited()
