"""Consents in the bot.

Privacy-policy notice on /start: shown once per user, recorded, never blocking.
Marketing opt-in (38-ФЗ ст. 18, 26.09.2026): asked on /start until answered, buttons
and /news commands record yes or no, storage failure never breaks the reply.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.consent import (
    CONSENT_POLICY_VERSION,
    MARKETING_CONSENT_VERSION,
    MARKETING_NO,
    MARKETING_OFF_TEXT,
    MARKETING_ON_TEXT,
    MARKETING_PROMPT,
    MARKETING_STORAGE_FAILED_TEXT,
    MARKETING_YES,
    consent_notice,
    first_start_consent_notice,
    is_start_command,
    marketing_command,
    marketing_consent_keyboard,
    offer_marketing_consent,
    try_handle_marketing_consent_callback,
    try_handle_marketing_consent_message,
)
from app.telegram.processor import process_core_telegram_update
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage


def test_is_start_command_matches_plain_and_deep_links():
    assert is_start_command("/start")
    assert is_start_command("/start abc123")
    assert is_start_command("/start@WHIEDA_Advisor_bot")
    assert not is_start_command("/started")
    assert not is_start_command("привет")
    assert not is_start_command("")


def test_notice_only_for_tenants_with_published_policy():
    assert "https://wwc.best/privacy-policy/" in (consent_notice("whieda") or "")
    assert consent_notice("nsp") is None


@pytest.mark.asyncio
async def test_notice_returned_only_on_first_record():
    with patch("app.telegram.consent.record_telegram_consent", AsyncMock(return_value=True)) as rec:
        assert await first_start_consent_notice("whieda", telegram_user_id=1, telegram_chat_id=1)
    rec.assert_awaited_once_with("whieda", telegram_user_id=1, telegram_chat_id=1)
    with patch("app.telegram.consent.record_telegram_consent", AsyncMock(return_value=False)):
        assert await first_start_consent_notice("whieda", telegram_user_id=1, telegram_chat_id=1) is None


@pytest.mark.asyncio
async def test_storage_failure_skips_notice_without_raising():
    with patch(
        "app.telegram.consent.record_telegram_consent",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        assert await first_start_consent_notice("whieda", telegram_user_id=1, telegram_chat_id=1) is None


def _start_update(text: str = "/start") -> dict:
    return {
        "message": {
            "text": text,
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200, "username": "someone"},
        }
    }


@pytest.mark.asyncio
async def test_start_sends_notice_before_panel(whieda_tenant, whieda_bot_binding):
    sent: list[str] = []

    async def fake_deliver(chat_id, text):
        sent.append(text)

    with patch("app.telegram.processor.link_lead_actor_by_username", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.fill_lead_actor_user_id", AsyncMock(return_value=None)
    ), patch("app.telegram.processor.merge_anonymous_actor_into_partner", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.first_start_consent_notice", AsyncMock(return_value=consent_notice("whieda"))
    ) as notice, patch("app.telegram.processor.deliver_text", fake_deliver), patch(
        "app.telegram.processor.offer_marketing_consent", AsyncMock(return_value=True)
    ) as offer, patch(
        "app.telegram.processor._remove_legacy_reply_keyboard", AsyncMock(return_value=None)
    ), patch(
        # /start открывает кабинет (владелец, 19.09.2026); уведомление 152-ФЗ
        # уходит раньше того, что открывается.
        "app.telegram.processor.show_referral_dashboard",
        AsyncMock(return_value={"ok": True, "route": "cabinet"}),
    ):
        result = await process_core_telegram_update(
            whieda_tenant, _start_update(), "t-consent", binding=whieda_bot_binding
        )
    assert result["route"] == "cabinet"
    notice.assert_awaited_once_with("whieda", telegram_user_id=200, telegram_chat_id=100, trace_id="t-consent")
    assert sent == [consent_notice("whieda")]
    # Вопрос о рассылке — сразу после уведомления, до кабинета.
    offer.assert_awaited_once_with("whieda", telegram_user_id=200, telegram_chat_id=100, trace_id="t-consent")


@pytest.mark.asyncio
async def test_non_start_message_never_touches_consent(whieda_tenant, whieda_bot_binding):
    with patch("app.telegram.processor.link_lead_actor_by_username", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.fill_lead_actor_user_id", AsyncMock(return_value=None)
    ), patch("app.telegram.processor.merge_anonymous_actor_into_partner", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.first_start_consent_notice", AsyncMock(return_value=None)
    ) as notice, patch(
        "app.telegram.processor.offer_marketing_consent", AsyncMock(return_value=False)
    ) as offer, patch("app.telegram.processor.handle_onboarding", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.handle_navigation_text", AsyncMock(return_value=None)
    ), patch("app.telegram.processor.handle_advisor_query", AsyncMock(return_value={"ok": True, "route": "advisor"})), patch(
        "app.telegram.support.get_open_ticket_for_user", AsyncMock(return_value=None)
    ):
        await process_core_telegram_update(
            whieda_tenant, _start_update("какая цена у кордицепса"), "t-no", binding=whieda_bot_binding
        )
    notice.assert_not_awaited()
    offer.assert_not_awaited()


def test_policy_version_is_a_date():
    assert len(CONSENT_POLICY_VERSION) == 10
    assert len(MARKETING_CONSENT_VERSION) == 10


# --- marketing opt-in --------------------------------------------------------


def test_marketing_keyboard_has_yes_and_not_now():
    rows = marketing_consent_keyboard()["inline_keyboard"]
    assert [b["callback_data"] for row in rows for b in row] == [MARKETING_YES, MARKETING_NO]
    assert "новости и предложения" in rows[0][0]["text"]
    assert MARKETING_YES.startswith("consent:news:")


def test_marketing_prompt_is_optional_and_says_how_to_unsubscribe():
    assert "необязательно" in MARKETING_PROMPT
    assert "/news_off" in MARKETING_PROMPT
    assert "/news_off" in MARKETING_ON_TEXT
    assert "/news_on" in MARKETING_OFF_TEXT


def test_marketing_command_parsing():
    assert marketing_command("/news") == "prompt"
    assert marketing_command("/news@WHIEDA_Advisor_bot") == "prompt"
    assert marketing_command("рассылка") == "prompt"
    assert marketing_command("Новости!") == "prompt"
    assert marketing_command("/news_on") == "on"
    assert marketing_command("/news_off") == "off"
    assert marketing_command("/NEWS_OFF@bot") == "off"
    assert marketing_command("новости про стельки") is None
    assert marketing_command("/newsletter") is None
    assert marketing_command("") is None
    assert marketing_command(None) is None


def _msg(text: str, chat_type: str = "private") -> TelegramMessage:
    return TelegramMessage(
        chat_id=100, user_id=200, message_id=1, text=text, chat_type=chat_type, file_id=None, raw={}
    )


def _callback(data: str, chat_type: str = "private") -> TelegramCallbackQuery:
    return TelegramCallbackQuery(
        chat_id=100, user_id=200, callback_query_id="cb-1", data=data, chat_type=chat_type, raw={}
    )


@pytest.mark.asyncio
async def test_callback_yes_records_opt_in_and_confirms(whieda_tenant, whieda_bot_binding):
    sent: list[tuple[int, str, dict | None]] = []

    async def fake_send(chat_id, text, reply_markup=None):
        sent.append((chat_id, text, reply_markup))

    with patch("app.telegram.consent.record_marketing_consent", AsyncMock(return_value={"opted_in": True})) as rec, patch(
        "app.telegram.consent._send", fake_send
    ), patch("app.telegram.consent.answer_callback_query", AsyncMock()) as ack, patch(
        "app.telegram.consent.current_bot_binding", return_value=whieda_bot_binding
    ):
        result = await try_handle_marketing_consent_callback(whieda_tenant, _callback(MARKETING_YES), trace_id="t")
    assert result == {"ok": True, "route": "marketing_consent", "status": "opted_in", "trace_id": "t"}
    rec.assert_awaited_once_with("whieda", telegram_user_id=200, telegram_chat_id=100, opted_in=True)
    ack.assert_awaited_once()
    assert sent == [(100, MARKETING_ON_TEXT, None)]


@pytest.mark.asyncio
async def test_callback_no_records_explicit_refusal(whieda_tenant, whieda_bot_binding):
    sent: list[str] = []

    async def fake_send(chat_id, text, reply_markup=None):
        sent.append(text)

    with patch("app.telegram.consent.record_marketing_consent", AsyncMock(return_value={"opted_in": False})) as rec, patch(
        "app.telegram.consent._send", fake_send
    ), patch("app.telegram.consent.answer_callback_query", AsyncMock()), patch(
        "app.telegram.consent.current_bot_binding", return_value=whieda_bot_binding
    ):
        result = await try_handle_marketing_consent_callback(whieda_tenant, _callback(MARKETING_NO), trace_id="t")
    assert result["status"] == "opted_out"
    rec.assert_awaited_once_with("whieda", telegram_user_id=200, telegram_chat_id=100, opted_in=False)
    assert sent == [MARKETING_OFF_TEXT]


@pytest.mark.asyncio
async def test_foreign_callback_is_not_ours(whieda_tenant):
    with patch("app.telegram.consent.record_marketing_consent", AsyncMock()) as rec:
        assert await try_handle_marketing_consent_callback(whieda_tenant, _callback("renew:start"), trace_id="t") is None
    rec.assert_not_awaited()


@pytest.mark.asyncio
async def test_group_callback_records_nothing(whieda_tenant, whieda_bot_binding):
    with patch("app.telegram.consent.record_marketing_consent", AsyncMock()) as rec, patch(
        "app.telegram.consent.answer_callback_query", AsyncMock()
    ), patch("app.telegram.consent.current_bot_binding", return_value=whieda_bot_binding):
        result = await try_handle_marketing_consent_callback(
            whieda_tenant, _callback(MARKETING_YES, chat_type="supergroup"), trace_id="t"
        )
    assert result["status"] == "private_chat_required"
    rec.assert_not_awaited()


@pytest.mark.asyncio
async def test_news_off_command_opts_out(whieda_tenant):
    sent: list[str] = []

    async def fake_send(chat_id, text, reply_markup=None):
        sent.append(text)

    with patch("app.telegram.consent.record_marketing_consent", AsyncMock(return_value={"opted_in": False})) as rec, patch(
        "app.telegram.consent._send", fake_send
    ):
        result = await try_handle_marketing_consent_message(whieda_tenant, _msg("/news_off"), trace_id="t")
    assert result["status"] == "opted_out"
    rec.assert_awaited_once_with("whieda", telegram_user_id=200, telegram_chat_id=100, opted_in=False)
    assert sent == [MARKETING_OFF_TEXT]


@pytest.mark.asyncio
async def test_news_on_command_opts_in(whieda_tenant):
    with patch("app.telegram.consent.record_marketing_consent", AsyncMock(return_value={"opted_in": True})) as rec, patch(
        "app.telegram.consent._send", AsyncMock()
    ):
        result = await try_handle_marketing_consent_message(whieda_tenant, _msg("/news_on"), trace_id="t")
    assert result["status"] == "opted_in"
    rec.assert_awaited_once_with("whieda", telegram_user_id=200, telegram_chat_id=100, opted_in=True)


@pytest.mark.asyncio
async def test_news_and_word_show_buttons_without_recording(whieda_tenant):
    sent: list[tuple[str, dict | None]] = []

    async def fake_send(chat_id, text, reply_markup=None):
        sent.append((text, reply_markup))

    with patch("app.telegram.consent.record_marketing_consent", AsyncMock()) as rec, patch(
        "app.telegram.consent._send", fake_send
    ):
        for text in ("/news", "рассылка"):
            result = await try_handle_marketing_consent_message(whieda_tenant, _msg(text), trace_id="t")
            assert result["status"] == "prompted"
    rec.assert_not_awaited()
    assert sent == [(MARKETING_PROMPT, marketing_consent_keyboard())] * 2


@pytest.mark.asyncio
async def test_other_text_is_not_a_marketing_command(whieda_tenant):
    with patch("app.telegram.consent._send", AsyncMock()) as send:
        assert await try_handle_marketing_consent_message(whieda_tenant, _msg("какая цена"), trace_id="t") is None
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_storage_failure_answers_without_raising(whieda_tenant):
    sent: list[str] = []

    async def fake_send(chat_id, text, reply_markup=None):
        sent.append(text)

    with patch(
        "app.telegram.consent.record_marketing_consent", AsyncMock(side_effect=RuntimeError("db down"))
    ), patch("app.telegram.consent._send", fake_send):
        result = await try_handle_marketing_consent_message(whieda_tenant, _msg("/news_off"), trace_id="t")
    assert result == {"ok": False, "route": "marketing_consent", "status": "storage_failed", "trace_id": "t"}
    assert sent == [MARKETING_STORAGE_FAILED_TEXT]


@pytest.mark.asyncio
async def test_offer_asks_only_until_answered():
    with patch("app.telegram.consent.marketing_consent_state", AsyncMock(return_value=None)), patch(
        "app.telegram.consent.send_marketing_prompt", AsyncMock()
    ) as prompt:
        assert await offer_marketing_consent("whieda", telegram_user_id=1, telegram_chat_id=1) is True
    prompt.assert_awaited_once_with(1)
    for answered in (True, False):
        with patch("app.telegram.consent.marketing_consent_state", AsyncMock(return_value=answered)), patch(
            "app.telegram.consent.send_marketing_prompt", AsyncMock()
        ) as prompt:
            assert await offer_marketing_consent("whieda", telegram_user_id=1, telegram_chat_id=1) is False
        prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_offer_skips_tenants_without_policy_and_storage_failures():
    with patch("app.telegram.consent.marketing_consent_state", AsyncMock(return_value=None)) as state, patch(
        "app.telegram.consent.send_marketing_prompt", AsyncMock()
    ) as prompt:
        assert await offer_marketing_consent("nsp", telegram_user_id=1, telegram_chat_id=1) is False
    state.assert_not_awaited()
    prompt.assert_not_awaited()
    with patch(
        "app.telegram.consent.marketing_consent_state", AsyncMock(side_effect=RuntimeError("db down"))
    ), patch("app.telegram.consent.send_marketing_prompt", AsyncMock()) as prompt:
        assert await offer_marketing_consent("whieda", telegram_user_id=1, telegram_chat_id=1) is False
    prompt.assert_not_awaited()


@pytest.mark.asyncio
async def test_news_off_in_chat_is_handled_before_the_advisor(whieda_tenant, whieda_bot_binding):
    with patch("app.telegram.processor.link_lead_actor_by_username", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.fill_lead_actor_user_id", AsyncMock(return_value=None)
    ), patch("app.telegram.processor.merge_anonymous_actor_into_partner", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.try_handle_marketing_consent_message",
        AsyncMock(return_value={"ok": True, "route": "marketing_consent", "status": "opted_out"}),
    ) as handler, patch(
        "app.telegram.processor.handle_advisor_query", AsyncMock(return_value={"ok": True, "route": "advisor"})
    ) as advisor:
        result = await process_core_telegram_update(
            whieda_tenant, _start_update("/news_off"), "t-off", binding=whieda_bot_binding
        )
    assert result["route"] == "marketing_consent"
    handler.assert_awaited_once()
    advisor.assert_not_awaited()


@pytest.mark.asyncio
async def test_marketing_button_is_dispatched_from_callback(whieda_tenant, whieda_bot_binding):
    update = {
        "callback_query": {
            "id": "cb-news",
            "data": MARKETING_YES,
            "from": {"id": 200},
            "message": {"chat": {"id": 100, "type": "private"}},
        }
    }
    with patch("app.telegram.processor.try_handle_admin_login", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.try_handle_content_access", AsyncMock(return_value=None)
    ), patch("app.telegram.processor.try_handle_billing_callback", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.try_handle_referral_admin_callback", AsyncMock(return_value=None)
    ), patch(
        "app.telegram.processor.try_handle_marketing_consent_callback",
        AsyncMock(return_value={"ok": True, "route": "marketing_consent", "status": "opted_in"}),
    ) as handler, patch("app.telegram.processor.try_handle_support_callback", AsyncMock(return_value=None)) as support:
        result = await process_core_telegram_update(whieda_tenant, update, "t-cb", binding=whieda_bot_binding)
    assert result["status"] == "opted_in"
    handler.assert_awaited_once()
    support.assert_not_awaited()
