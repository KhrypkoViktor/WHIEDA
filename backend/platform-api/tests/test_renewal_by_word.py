"""Партнёр пишет «оплата» словом — открывается продление, а не «Команда недоступна»."""
from unittest.mock import AsyncMock, patch

import pytest

from app.renewal_requests.service import RenewalRequestError
from app.telegram import renewal_requests as rr
from app.telegram.update_parser import TelegramMessage


def _msg(text: str, user_id: int = 777) -> TelegramMessage:
    return TelegramMessage(
        chat_id=777, user_id=user_id, message_id=1, text=text, chat_type="private",
        file_id=None, raw={}, username="partner",
    )


def _tenant():
    class T:
        tenant_id = "whieda"

    return T()


def test_words_that_mean_renewal():
    for text in ("оплата", "Оплатить", "продлить", "продление", "  оплата  ", "продлить платформу"):
        assert rr.is_renewal_request_text(text), text
    for text in ("оплата ref:kira 3000 rub 3", "сколько стоит", "", "оплатили уже?"):
        assert not rr.is_renewal_request_text(text), text


@pytest.mark.asyncio
async def test_partner_word_opens_the_period_question():
    sent = []

    async def deliver(chat_id, text, *, reply_markup=None):
        sent.append((text, reply_markup))

    offers = [
        {"plan_code": "platform_3m", "title": "Сайт на 3 месяца", "price_wusd_minor": 3000, "price_rub_minor": 300000},
        {"plan_code": "bundle_pro_club_3m", "title": "Сайт + Клуб на 3 месяца", "price_wusd_minor": 10500, "price_rub_minor": 1050000},
    ]
    with patch.object(rr, "_deliver", deliver), patch.object(
        rr, "_actor", AsyncMock(return_value="ladnaya")
    ), patch.object(
        rr, "begin_renewal_request", AsyncMock(return_value={"status": "awaiting_period"})
    ), patch.object(rr, "list_renewal_offers", AsyncMock(return_value=offers)), patch.object(
        rr, "_owner_allowed", lambda user_id: False
    ):
        result = await rr.try_start_renewal_by_text(_tenant(), _msg("оплата"), trace_id="t")

    assert result["status"] == "awaiting_period"
    text, markup = sent[0]
    assert text == "Что оплачиваете?"
    buttons = [row[0] for row in markup["inline_keyboard"]]
    assert buttons[0] == {"text": "Сайт на 3 месяца — 30 W$ / 3 000 ₽", "callback_data": "renew:plan:platform_3m"}
    assert buttons[1]["callback_data"] == "renew:plan:bundle_pro_club_3m"
    assert buttons[-1]["callback_data"] == "renew:cancel"


@pytest.mark.asyncio
async def test_a_guest_without_a_site_falls_through_to_the_advisor():
    with patch.object(rr, "_actor", AsyncMock(return_value="guest")), patch.object(
        rr, "begin_renewal_request", AsyncMock(side_effect=RenewalRequestError("Сначала создайте персональный сайт."))
    ), patch.object(rr, "_owner_allowed", lambda user_id: False):
        assert await rr.try_start_renewal_by_text(_tenant(), _msg("оплата"), trace_id="t") is None


@pytest.mark.asyncio
async def test_owner_keeps_the_billing_command():
    with patch.object(rr, "_owner_allowed", lambda user_id: True):
        assert await rr.try_start_renewal_by_text(_tenant(), _msg("оплата", user_id=688931415), trace_id="t") is None
