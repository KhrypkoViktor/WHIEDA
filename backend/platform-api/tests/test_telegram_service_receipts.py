"""Чек за услугу и автоответы Gemini (владелец, 09.11.2026).

A: чек Татьяны за Gemini не должен уходить в забытое продление; при двух кандидатах бот спрашивает кнопками.
B: вложение клиента — одним сообщением с подписью; карточка «снова нажал» — не чаще раза в сутки.
C: автоответы клиенту Gemini (заказ, чек, «готово»), реквизиты услуг из настроек витрины.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.renewal_requests.service import renewal_is_fresh
from app.telegram import support
from app.telegram.bindings import binding_context_scope
from app.telegram.update_parser import parse_telegram_callback, parse_telegram_message
from tests.test_telegram_support_site import KARINA, OWNER, USER, _gemini_ticket, two_admins  # noqa: F401


def _photo_message(*, message_id: int = 41, user: int = USER, caption: str = ""):
    message = {
        "message_id": message_id,
        "chat": {"id": user, "type": "private"},
        "from": {"id": user, "first_name": "Татьяна"},
        "photo": [{"file_id": "PHOTO-1"}],
    }
    if caption:
        message["caption"] = caption
    parsed = parse_telegram_message({"message": message})
    assert parsed is not None
    return parsed


def _renewal(*, age: timedelta, status: str = "awaiting_payment") -> dict:
    return {"request_id": "e87f9cfb-0000-0000-0000-000000000000", "status": status, "updated_at": datetime.now(timezone.utc) - age}


def _patched(renewal: dict | None, ticket: dict | None):
    return (
        patch("app.telegram.support.ensure_telegram_actor", AsyncMock(return_value="actor-1")),
        patch("app.telegram.support.get_open_renewal_request", AsyncMock(return_value=renewal)),
        patch("app.telegram.support.get_open_site_request", AsyncMock(return_value=None)),
        patch("app.telegram.support.get_open_ticket_for_user", AsyncMock(return_value=ticket)),
    )


def test_renewal_freshness_is_six_hours():
    assert renewal_is_fresh(_renewal(age=timedelta(hours=5, minutes=50)))
    assert not renewal_is_fresh(_renewal(age=timedelta(hours=6, minutes=10)))
    assert not renewal_is_fresh(_renewal(age=timedelta(days=2)))


@pytest.mark.asyncio
async def test_tatiana_case_old_renewal_and_open_service_ticket_send_receipt_to_the_ticket(whieda_tenant, whieda_bot_binding, two_admins):
    """Продление 07.11 висит в awaiting_payment, чек 08.11 — по заявке Gemini #S-68: чек идёт в тикет."""
    ticket = _gemini_ticket(forum_chat_id=-100123, forum_thread_id=68)
    relay = AsyncMock(return_value={"ok": True, "route": "support_relay"})
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    a, b, c, d = _patched(_renewal(age=timedelta(days=1, hours=14)), ticket)
    with a, b, c, d, patch("app.telegram.support._relay_user_to_admin", relay), patch("app.telegram.support.send_telegram_text", send), binding_context_scope(whieda_bot_binding):
        result = await support.try_handle_support_message(whieda_tenant, _photo_message(), trace_id="t1")
    assert result == {"ok": True, "route": "support_relay"}
    relay.assert_awaited_once()
    assert relay.await_args.args[2] is ticket
    send.assert_not_awaited()  # вопрос «за что оплата» не нужен


@pytest.mark.asyncio
async def test_fresh_renewal_without_ticket_still_takes_the_receipt(whieda_tenant, whieda_bot_binding, two_admins):
    a, b, c, d = _patched(_renewal(age=timedelta(hours=1)), None)
    with a, b, c, d, binding_context_scope(whieda_bot_binding):
        result = await support.try_handle_support_message(whieda_tenant, _photo_message(), trace_id="t2")
    assert result is None  # дальше его берёт try_handle_renewal_message


@pytest.mark.asyncio
async def test_fresh_renewal_and_open_ticket_asks_with_two_buttons(whieda_tenant, whieda_bot_binding, two_admins):
    ticket = _gemini_ticket(forum_chat_id=-100123, forum_thread_id=68)
    relay = AsyncMock()
    send = AsyncMock(return_value={"ok": True, "message_id": 77})
    a, b, c, d = _patched(_renewal(age=timedelta(hours=2)), ticket)
    with a, b, c, d, patch("app.telegram.support._relay_user_to_admin", relay), patch("app.telegram.support.send_telegram_text", send), binding_context_scope(whieda_bot_binding):
        result = await support.try_handle_support_message(whieda_tenant, _photo_message(message_id=41), trace_id="t3")
    assert result["status"] == "payment_choice_asked"
    relay.assert_not_awaited()
    kwargs = send.await_args.kwargs
    assert kwargs["text"] == "Это оплата за:"
    assert kwargs["reply_to_message_id"] == 41
    row = kwargs["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in row] == ["Gemini Pro", "Продление сайта и клуба"]
    assert [b["callback_data"] for b in row] == ["svc:pay:t", "svc:pay:r"]


def _choice_callback(data: str, *, user: int = USER):
    original = {
        "message_id": 41,
        "chat": {"id": user, "type": "private"},
        "from": {"id": user, "first_name": "Татьяна"},
        "photo": [{"file_id": "PHOTO-1"}],
    }
    callback = parse_telegram_callback(
        {
            "callback_query": {
                "id": "cb-pay",
                "data": data,
                "from": {"id": user},
                "message": {"message_id": 77, "chat": {"id": user, "type": "private"}, "reply_to_message": original},
            }
        }
    )
    assert callback is not None
    return callback


@pytest.mark.asyncio
async def test_choice_gemini_sends_the_file_to_the_ticket(whieda_tenant, whieda_bot_binding, two_admins):
    ticket = _gemini_ticket(forum_chat_id=-100123, forum_thread_id=68)
    relay = AsyncMock(return_value={"ok": True, "route": "support_relay"})
    renewal = AsyncMock()
    with patch("app.telegram.support.answer_callback_query", AsyncMock()), patch("app.telegram.support.edit_message_reply_markup", AsyncMock()), patch(
        "app.telegram.support.is_duplicate_source", AsyncMock(return_value=False)
    ), patch("app.telegram.support.get_open_ticket_for_user", AsyncMock(return_value=ticket)), patch(
        "app.telegram.support._relay_user_to_admin", relay
    ), patch("app.telegram.renewal_requests.try_handle_renewal_message", renewal), binding_context_scope(whieda_bot_binding):
        result = await support.try_handle_support_callback(whieda_tenant, _choice_callback("svc:pay:t"), trace_id="t4")
    assert result["route"] == "support_relay"
    relay.assert_awaited_once()
    assert relay.await_args.args[1].file_id == "PHOTO-1" and relay.await_args.args[1].message_id == 41
    renewal.assert_not_awaited()


@pytest.mark.asyncio
async def test_choice_renewal_sends_the_file_to_the_renewal(whieda_tenant, whieda_bot_binding, two_admins):
    relay = AsyncMock()
    renewal = AsyncMock(return_value={"ok": True, "route": "renewal", "status": "pending_confirmation"})
    with patch("app.telegram.support.answer_callback_query", AsyncMock()), patch("app.telegram.support.edit_message_reply_markup", AsyncMock()), patch(
        "app.telegram.support.is_duplicate_source", AsyncMock(return_value=False)
    ), patch("app.telegram.support._relay_user_to_admin", relay), patch("app.telegram.renewal_requests.try_handle_renewal_message", renewal), binding_context_scope(
        whieda_bot_binding
    ):
        result = await support.try_handle_support_callback(whieda_tenant, _choice_callback("svc:pay:r"), trace_id="t5")
    assert result["route"] == "renewal"
    relay.assert_not_awaited()
    assert renewal.await_args.args[1].file_id == "PHOTO-1"


@pytest.mark.asyncio
async def test_choice_pressed_by_someone_else_is_ignored(whieda_tenant, whieda_bot_binding, two_admins):
    relay = AsyncMock()
    callback = _choice_callback("svc:pay:t")
    callback.user_id = 999
    with patch("app.telegram.support.answer_callback_query", AsyncMock()), patch("app.telegram.support.send_telegram_text", AsyncMock(return_value={"ok": True})), patch(
        "app.telegram.support._relay_user_to_admin", relay
    ), binding_context_scope(whieda_bot_binding):
        result = await support.try_handle_support_callback(whieda_tenant, callback, trace_id="t6")
    assert result["status"] == "payment_choice_lost"
    relay.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_card_shows_the_expected_amount(whieda_tenant, whieda_bot_binding, monkeypatch):
    from app.settings import get_settings
    from app.telegram import renewal_requests as rr

    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    get_settings.cache_clear()
    request = {
        "request_id": "e87f9cfb-1111-2222-3333-444444444444", "ref_code": "tatiana", "status": "pending_confirmation",
        "amount_minor": 1500000, "currency": "RUB", "plan_code": None, "access_months": 3,
    }
    deliver = AsyncMock()
    msg = _photo_message()
    with patch.object(rr, "_actor", AsyncMock(return_value="actor-1")), patch.object(
        rr, "get_open_renewal_request", AsyncMock(return_value={**request, "status": "awaiting_payment"})
    ), patch.object(rr, "submit_renewal_payment_proof", AsyncMock(return_value=request)), patch.object(
        rr, "plan_title", AsyncMock(return_value="Сайт и клуб")
    ), patch.object(rr, "copy_telegram_message", AsyncMock()), patch.object(rr, "_deliver", deliver), patch.object(
        rr, "_prompt", AsyncMock()
    ), binding_context_scope(whieda_bot_binding):
        await rr.try_handle_renewal_message(whieda_tenant, msg, trace_id="t7")
    card = [c.args[1] for c in deliver.await_args_list if c.args[0] == OWNER][0]
    assert "Ожидаем 15 000 ₽ — сверьте с чеком" in card
    get_settings.cache_clear()


# ----------------------------------------------------------------------------
# B: меньше шума у Карины
# ----------------------------------------------------------------------------

def _forum_ticket():
    return _gemini_ticket(forum_chat_id=-100123, forum_thread_id=68)


@pytest.mark.asyncio
async def test_attachment_goes_as_one_message_with_our_line_in_front_of_the_client_caption(whieda_tenant, whieda_bot_binding, two_admins):
    copy = AsyncMock(return_value={"ok": True, "message_id": 555})
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    record = AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    with patch("app.telegram.support.copy_telegram_message", copy), patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.record_relayed_message", record
    ), patch("app.telegram.support.set_message_reaction", AsyncMock()), patch(
        "app.telegram.support.user_file_count", AsyncMock(return_value=1)
    ), binding_context_scope(whieda_bot_binding):
        await support._relay_user_to_admin(whieda_tenant, _photo_message(caption="Оплатила, чек"), _forum_ticket(), trace_id="b1")
    copy.assert_awaited_once()
    assert copy.await_args.kwargs["caption"] == "Клиент WWC · Заявка #S-1043\nОплатила, чек"
    send.assert_not_awaited()  # отдельной строки «(вложение выше)» больше нет
    assert record.await_args.kwargs["delivered_message_id"] == 555  # Reply админа ляжет на саму копию


@pytest.mark.asyncio
async def test_attachment_without_caption_gets_only_our_line_and_falls_back_when_copy_with_caption_fails(whieda_tenant, whieda_bot_binding, two_admins):
    copy = AsyncMock(side_effect=[{"ok": False}, {"ok": True, "message_id": 556}])
    send = AsyncMock(return_value={"ok": True, "message_id": 9})
    with patch("app.telegram.support.copy_telegram_message", copy), patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    ), patch("app.telegram.support.set_message_reaction", AsyncMock()), patch(
        "app.telegram.support.user_file_count", AsyncMock(return_value=1)
    ), binding_context_scope(whieda_bot_binding):
        await support._relay_user_to_admin(whieda_tenant, _photo_message(), _forum_ticket(), trace_id="b2")
    assert copy.await_args_list[0].kwargs["caption"] == "Клиент WWC · Заявка #S-1043"
    assert "caption" not in copy.await_args_list[1].kwargs
    assert send.await_args.kwargs["text"].startswith("Клиент WWC · Заявка #S-1043\n(вложение выше)")


@pytest.mark.asyncio
async def test_video_note_cannot_carry_a_caption_so_it_keeps_the_pair(whieda_tenant, whieda_bot_binding, two_admins):
    note = parse_telegram_message({"message": {
        "message_id": 50, "chat": {"id": USER, "type": "private"}, "from": {"id": USER}, "video_note": {"file_id": "VN"},
    }})
    copy = AsyncMock(return_value={"ok": True, "message_id": 600})
    send = AsyncMock(return_value={"ok": True, "message_id": 9})
    with patch("app.telegram.support.copy_telegram_message", copy), patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    ), patch("app.telegram.support.set_message_reaction", AsyncMock()), binding_context_scope(whieda_bot_binding):
        await support._relay_user_to_admin(whieda_tenant, note, _forum_ticket(), trace_id="b3")
    copy.assert_awaited_once()
    assert "caption" not in copy.await_args.kwargs
    assert "(видеокружок выше)" in send.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_repeat_order_card_is_sent_once_per_ticket_per_day(whieda_tenant, whieda_bot_binding, two_admins):
    existing = _gemini_ticket(ticket_no=68, forum_chat_id=-100123, forum_thread_id=68, created=False)
    callback = parse_telegram_callback({"callback_query": {
        "id": "c", "data": "svc:confirm:gemini_6m", "from": {"id": USER}, "message": {"message_id": 5, "chat": {"id": USER, "type": "private"}},
    }})
    send = AsyncMock(return_value={"ok": True, "message_id": 950})
    recent = AsyncMock(side_effect=[False, True])
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.answer_callback_query", AsyncMock()), patch(
        "app.telegram.support.open_or_reuse_ticket", AsyncMock(return_value=existing)
    ), patch("app.telegram.support.has_recent_system_message", recent), patch(
        "app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    ), patch("app.telegram.support._site_card", AsyncMock(return_value=None)), binding_context_scope(whieda_bot_binding):
        await support.try_handle_support_callback(whieda_tenant, callback, trace_id="b4")
        await support.try_handle_support_callback(whieda_tenant, callback, trace_id="b5")
    to_forum = [c for c in send.await_args_list if c.kwargs["chat_id"] == "-100123"]
    assert len(to_forum) == 1 and "снова нажал" in to_forum[0].kwargs["text"]
    assert len([c for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)]) == 2  # клиенту отвечаем оба раза


# ----------------------------------------------------------------------------
# C: автоответы Gemini
# ----------------------------------------------------------------------------

def _offer_callback():
    return parse_telegram_callback({"callback_query": {
        "id": "c", "data": "svc:confirm:gemini_6m", "from": {"id": USER, "first_name": "Ольга"},
        "message": {"message_id": 5, "chat": {"id": USER, "type": "private"}},
    }})


async def _order(whieda_tenant, whieda_bot_binding, requisites: str) -> str:
    created = _gemini_ticket(ticket_no=68, forum_chat_id=-100123, forum_thread_id=68, created=True)
    send = AsyncMock(return_value={"ok": True, "message_id": 950})
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.answer_callback_query", AsyncMock()), patch(
        "app.telegram.support.open_or_reuse_ticket", AsyncMock(return_value=created)
    ), patch("app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})), patch(
        "app.telegram.support._site_card", AsyncMock(return_value=None)
    ), patch("app.telegram.support.get_services_payment_text", AsyncMock(return_value=requisites)), binding_context_scope(whieda_bot_binding):
        await support.try_handle_support_callback(whieda_tenant, _offer_callback(), trace_id="c1")
    return [c.kwargs["text"] for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)][0]


@pytest.mark.asyncio
async def test_c1_order_reply_carries_price_term_and_requisites(whieda_tenant, whieda_bot_binding, two_admins):
    text = await _order(whieda_tenant, whieda_bot_binding, "Карта Т-Банк 0000 1111 2222 3333, получатель В. Х.")
    assert text.startswith("Здравствуйте! Мы получили вашу заявку на Gemini Pro на 6 месяцев — 4 990 ₽.")
    assert "Nano Banana (картинки), Veo 3 (видео) и 2 ТБ на Google Диске" in text
    assert "Пришлите сюда, пожалуйста, вашу почту Gmail." in text
    assert "Карта Т-Банк 0000 1111 2222 3333, получатель В. Х." in text
    assert "Реквизиты для оплаты пришлёт" not in text
    assert text.endswith("После оплаты пришлите сюда скрин чека — администратор проверит и подключит вас.")


@pytest.mark.asyncio
async def test_c1_without_requisites_says_the_admin_will_send_them(whieda_tenant, whieda_bot_binding, two_admins):
    text = await _order(whieda_tenant, whieda_bot_binding, "")
    assert "Реквизиты для оплаты пришлёт администратор в этом чате." in text


@pytest.mark.asyncio
async def test_c2_first_file_gets_one_thanks_and_second_does_not(whieda_tenant, whieda_bot_binding, two_admins):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    count = AsyncMock(side_effect=[0, 1])
    with patch("app.telegram.support.copy_telegram_message", AsyncMock(return_value={"ok": True, "message_id": 5})), patch(
        "app.telegram.support.send_telegram_text", send
    ), patch("app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})), patch(
        "app.telegram.support.set_message_reaction", AsyncMock()
    ), patch("app.telegram.support.user_file_count", count), binding_context_scope(whieda_bot_binding):
        await support._relay_user_to_admin(whieda_tenant, _photo_message(message_id=41), _forum_ticket(), trace_id="c2")
        await support._relay_user_to_admin(whieda_tenant, _photo_message(message_id=42), _forum_ticket(), trace_id="c3")
    thanks = [c.kwargs["text"] for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)]
    assert thanks == ["Спасибо, чек получили! Администратор проверит оплату и подключит вас — ожидайте сообщения здесь. Обычно это в течение дня."]


def _topic_message(text: str, *, user: int, thread: int = 68):
    parsed = parse_telegram_message({"message": {
        "message_id": 900, "text": text, "chat": {"id": -100123, "type": "supergroup", "is_forum": True},
        "from": {"id": user, "first_name": "Карина"}, "is_topic_message": True, "message_thread_id": thread,
    }})
    assert parsed is not None
    return parsed


async def _done(whieda_tenant, whieda_bot_binding, user: int):
    ticket = _gemini_ticket(forum_chat_id=-100123, forum_thread_id=68)
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    close = AsyncMock(return_value={**ticket, "status": "closed"})
    relay = AsyncMock(return_value={"ok": True, "route": "support_relay", "direction": "admin_to_user"})
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.close_ticket", close), patch(
        "app.telegram.support.find_ticket_by_forum_thread", AsyncMock(return_value=ticket)
    ), patch("app.telegram.support.try_handle_service_command", AsyncMock(return_value=None)), patch(
        "app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    ), patch("app.telegram.support.set_message_reaction", AsyncMock(return_value={"ok": True})), patch(
        "app.telegram.support.edit_forum_topic", AsyncMock()
    ), patch("app.telegram.support.close_forum_topic", AsyncMock()), patch("app.telegram.support._site_card", AsyncMock(return_value=None)), patch(
        "app.telegram.support._relay_admin_to_user", relay
    ), binding_context_scope(whieda_bot_binding):
        result = await support.try_handle_support_forum_message(whieda_tenant, _topic_message("Готово", user=user), trace_id="c4")
    return result, send, close, relay


@pytest.mark.asyncio
async def test_c3_done_from_the_service_admin_sends_the_final_message_and_closes(whieda_tenant, whieda_bot_binding, two_admins):
    result, send, close, relay = await _done(whieda_tenant, whieda_bot_binding, KARINA)
    assert result["status"] == "done"
    close.assert_awaited_once()
    relay.assert_not_awaited()
    to_user = [c.kwargs["text"] for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)]
    assert to_user == [support.GEMINI_DONE_TEXT]
    assert "Готово, подписка подключена 🎉" in to_user[0] and "https://youtu.be/LHcPcKTReug" in to_user[0]


@pytest.mark.asyncio
async def test_c3_done_works_for_the_owner_too(whieda_tenant, whieda_bot_binding, two_admins):
    result, _send, close, _relay = await _done(whieda_tenant, whieda_bot_binding, OWNER)
    assert result["status"] == "done"
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_c3_done_from_anyone_else_is_a_plain_message(whieda_tenant, whieda_bot_binding, two_admins):
    result, send, close, relay = await _done(whieda_tenant, whieda_bot_binding, 123456)
    close.assert_not_awaited()
    relay.assert_awaited_once()


@pytest.mark.asyncio
async def test_payment_text_command_is_owner_only_and_never_logged(whieda_tenant, whieda_bot_binding, two_admins, caplog):
    def private(user: int, text: str):
        parsed = parse_telegram_message({"message": {
            "message_id": 7, "text": text, "chat": {"id": user, "type": "private"}, "from": {"id": user},
        }})
        assert parsed is not None
        return parsed

    send, setter = AsyncMock(return_value={"ok": True}), AsyncMock(return_value=True)
    getter = AsyncMock(return_value="Карта 1234")
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.set_services_payment_text", setter), patch(
        "app.telegram.support.get_services_payment_text", getter
    ), binding_context_scope(whieda_bot_binding):
        # не владелец (в том числе администратор услуг) — команда не срабатывает
        assert await support.try_handle_payment_text_command(whieda_tenant, private(KARINA, "реквизиты услуг Карта 999"), trace_id="p1") is None
        setter.assert_not_awaited()
        saved = await support.try_handle_payment_text_command(whieda_tenant, private(OWNER, "реквизиты услуг Карта 1234\nПолучатель В. Х."), trace_id="p2")
        assert saved["status"] == "payment_text_saved"
        setter.assert_awaited_once_with("whieda", "Карта 1234\nПолучатель В. Х.")
        shown = await support.try_handle_payment_text_command(whieda_tenant, private(OWNER, "реквизиты услуг"), trace_id="p3")
        assert shown["status"] == "payment_text_shown" and "Карта 1234" in send.await_args.kwargs["text"]
        cleared = await support.try_handle_payment_text_command(whieda_tenant, private(OWNER, "реквизиты услуг очистить"), trace_id="p4")
        assert cleared["status"] == "payment_text_cleared"
        assert setter.await_args.args == ("whieda", None)
    assert "Карта 1234" not in caplog.text
