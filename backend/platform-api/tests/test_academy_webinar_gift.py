"""Подарок за эфир (20.10.2026): кодовое слово «мастерство» боту — подарочный курс на 7 дней.

Слово узнаём в любом написании («Мастерство!», «кодовое слово: МАСТЕРСТВО»), но не в длинном
сообщении; действует в день эфира от начала до полуночи по Минску; вне окна бот объясняет, когда.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.academy.webinar_gift import (
    Gift,
    Granted,
    gift_closed_text,
    gift_config,
    gift_text,
    gift_webinar,
    is_code_word,
)
from app.academy.webinars import webinars_config
from app.telegram.processor import process_core_telegram_update
from tests.test_academy_showcase_bot import ALMIRA, _text, almira_binding  # noqa: F401 — фикстура

MINSK = timezone(timedelta(hours=3))
WEBINARS = {
    "ig_web24": {"at": "2026-10-24T17:30:00+03:00", "date": "24 октября", "url": ""},
    "ig_web31": {"at": "2026-10-31T17:30:00+03:00", "date": "31 октября", "url": ""},
}
GIFT = {"word": "Мастерство", "course": "igra-formy-podarok", "days": 7}


def at(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=MINSK) if day <= 31 else datetime(
        2026, 11, day - 31, hour, minute, tzinfo=MINSK
    )


def test_gift_config_accepts_only_a_word_and_a_slug():
    assert gift_config({"webinar_gift": GIFT}) == Gift(word="мастерство", course="igra-formy-podarok", days=7)
    assert gift_config({"webinar_gift": {**GIFT, "days": None}}).days == 7
    assert gift_config({}) is None
    assert gift_config({"webinar_gift": {**GIFT, "word": " !! "}}) is None
    assert gift_config({"webinar_gift": {**GIFT, "course": "../igra"}}) is None
    assert gift_config({"webinar_gift": {**GIFT, "days": 0}}) is None


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("мастерство", True),
        ("Мастерство!", True),
        ("Кодовое слово: МАСТЕРСТВО 🙂", True),
        ("«мастерство»", True),
        ("мастер", False),
        ("мастерства", False),
        ("Спасибо за эфир, очень понравилось, особенно про мастерство", False),
        ("", False),
    ],
)
def test_code_word_in_any_spelling_but_not_in_a_long_message(text, ok):
    assert is_code_word(text, gift_config({"webinar_gift": GIFT})) is ok


def test_word_works_from_the_start_of_the_webinar_till_midnight():
    webinars = webinars_config({"webinars": WEBINARS})
    assert gift_webinar(webinars, at(24, 17, 29)) is None
    assert gift_webinar(webinars, at(24, 17, 30)).tag == "ig_web24"
    assert gift_webinar(webinars, at(24, 23, 59)).tag == "ig_web24"
    assert gift_webinar(webinars, at(25, 0, 0)) is None
    assert gift_webinar(webinars, at(31, 18, 0)).tag == "ig_web31"


def test_out_of_window_answer_says_when():
    webinars = webinars_config({"webinars": WEBINARS})
    assert gift_closed_text(webinars, at(23, 12, 0)) == (
        "Кодовое слово назовут на эфире. Напишите его сюда в день эфира — 24 октября, с 17:30 до полуночи."
    )
    assert "Следующий эфир — 31 октября, с 17:30 до полуночи" in gift_closed_text(webinars, at(25, 9, 0))
    assert "срок закончился" in gift_closed_text(webinars, at(33, 9, 0))


def test_gift_text_names_the_course_and_the_last_day():
    granted = Granted(title="Урок 1. Точка, линия, пятно", expires_at=at(31, 18, 0), already=False)
    assert gift_text(granted).startswith("🎁 Готово! Открыли вам «Урок 1. Точка, линия, пятно» — до 31 октября.")
    assert gift_text(replace(granted, already=True)) == "🎁 «Урок 1. Точка, линия, пятно» уже открыт вам — до 31 октября."
    assert gift_text(replace(granted, expires_at=None)).startswith("🎁 Готово! Открыли вам «Урок 1. Точка, линия, пятно».")


ALMIRA_GIFT = replace(ALMIRA, support={"webinars": WEBINARS, "webinar_gift": GIFT})


@pytest.mark.asyncio
async def test_code_word_in_window_opens_the_gift_with_a_button(whieda_tenant, almira_binding):  # noqa: F811
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    grant = AsyncMock(return_value=Granted(title="Урок 1. Точка, линия, пятно", expires_at=at(31, 18, 0), already=False))
    window = webinars_config({"webinars": WEBINARS})[0]
    with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=ALMIRA_GIFT)), patch(
        "app.telegram.showcase_bot.send_telegram_text", sent
    ), patch("app.telegram.showcase_bot.ensure_telegram_actor", AsyncMock()), patch(
        "app.telegram.showcase_bot.gift_webinar", lambda webinars, now: window
    ), patch("app.telegram.showcase_bot.grant_gift", grant), patch(
        "app.telegram.showcase_bot.with_site_login", AsyncMock(return_value="https://academy.almira.by/academy/?course=igra-formy-podarok")
    ):
        result = await process_core_telegram_update(whieda_tenant, _text("Мастерство!", 4001), "t", binding=almira_binding)
    assert (result["route"], result["webinar"]) == ("showcase_gift", "ig_web24")
    assert grant.await_args.args[2:4] == (4001, "ig_web24")
    kwargs = sent.await_args.kwargs
    assert kwargs["text"].startswith("🎁 Готово!")
    assert kwargs["reply_markup"]["inline_keyboard"][0][0]["text"] == "🎨 Открыть урок"


@pytest.mark.asyncio
async def test_code_word_out_of_window_explains_and_grants_nothing(whieda_tenant, almira_binding):  # noqa: F811
    sent = AsyncMock(return_value={"ok": True, "message_id": 1})
    grant = AsyncMock()
    with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=ALMIRA_GIFT)), patch(
        "app.telegram.showcase_bot.send_telegram_text", sent
    ), patch("app.telegram.showcase_bot.ensure_telegram_actor", AsyncMock()), patch(
        "app.telegram.showcase_bot.gift_webinar", lambda webinars, now: None
    ), patch("app.telegram.showcase_bot.preview_admin_ids", lambda: frozenset()), patch(
        "app.telegram.showcase_bot.grant_gift", grant
    ):
        result = await process_core_telegram_update(whieda_tenant, _text("мастерство", 4002), "t", binding=almira_binding)
    assert result["route"] == "showcase_gift_closed"
    grant.assert_not_awaited()
    assert "Кодовое слово" in sent.await_args.kwargs["text"]
