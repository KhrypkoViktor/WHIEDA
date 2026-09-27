"""Поддержка, 27.09.2026 (владелец: «чаты спутаны, распутывай»).

* Обращение по сайту: имя партнёра — ссылка в его Telegram, в шапке кнопка
  «Написать в Telegram» и «Закрыть», без «Оплачено» (это кнопка продаж Gemini).
  Если человек запретил ссылки на себя по id, шапка уходит без кнопки.
* Заявка Gemini: под сообщением человека — «↪ В поддержку WWC». Вопрос про сайт,
  попавший к Карине, переезжает в обращение по сайту к владельцу вместе с
  соседними сообщениями (текст + скриншоты), человек получает номер.
* Шаги анкеты и чеки — в тему «Заявки на сайты» группы WWC Support;
  «Подтвердить / Отклонить» работают из этой группы и только из неё.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.telegram import site_requests as sr
from app.telegram.processor import process_core_telegram_update
from app.telegram.support import is_support_forum_traffic
from tests.test_telegram_support_site import (  # noqa: F401 — фикстуры
    KARINA,
    OWNER,
    SERVICES_FORUM,
    SITE,
    SITE_FORUM,
    USER,
    _callback,
    _forums,
    _gemini_ticket,
    _message,
    _site_ticket,
    both_forums,
    quiet_linking,
    two_admins,
)


def _keyboards(send: AsyncMock, chat: int) -> list[dict]:
    return [c.kwargs.get("reply_markup") for c in send.await_args_list if c.kwargs["chat_id"] == str(chat)]


@pytest.mark.asyncio
async def test_site_header_links_the_partner_and_has_no_paid_button(whieda_tenant, whieda_bot_binding, two_admins, both_forums, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 501})
    ticket = _site_ticket(user_display="Ольга Злобина")
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.open_or_reuse_ticket", AsyncMock(return_value=ticket)), patch(
        "app.telegram.support.create_forum_topic", AsyncMock(return_value={"ok": True, "message_thread_id": 8})
    ), patch("app.telegram.support.attach_forum_topic", AsyncMock(return_value={**ticket, "forum_chat_id": SITE_FORUM, "forum_thread_id": 8})), patch(
        "app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    ):
        await process_core_telegram_update(whieda_tenant, _message("поддержка", username=None), "u1", binding=whieda_bot_binding)
    header = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(SITE_FORUM)][0]
    assert header["text"].startswith(f'#S-1042 · <a href="tg://user?id={USER}">Ольга Злобина</a>')
    rows = header["reply_markup"]["inline_keyboard"]
    assert rows[0] == [{"text": "✉️ Написать в Telegram", "url": f"tg://user?id={USER}"}]
    assert rows[1][0]["callback_data"].startswith("svc:close:")
    assert not any(b.get("callback_data", "").startswith("sale:") for row in rows for b in row), "no «Оплачено» in site support"


@pytest.mark.asyncio
async def test_header_is_resent_without_the_direct_button_when_privacy_blocks_it(whieda_tenant, whieda_bot_binding, two_admins, both_forums, quiet_linking):
    send = AsyncMock(side_effect=[{"ok": False, "status_code": 400}, {"ok": True, "message_id": 502}, {"ok": True, "message_id": 503}])
    ticket = _site_ticket(user_display="Ольга Злобина", forum_chat_id=SITE_FORUM, forum_thread_id=8, created=True)
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.open_or_reuse_ticket", AsyncMock(return_value=ticket)), patch(
        "app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    ):
        await process_core_telegram_update(whieda_tenant, _message("поддержка", username=None), "u2", binding=whieda_bot_binding)
    first, second = _keyboards(send, SITE_FORUM)[:2]
    assert first["inline_keyboard"][0][0].get("url")
    assert all("url" not in b for row in second["inline_keyboard"] for b in row)
    assert second["inline_keyboard"][0][0]["callback_data"].startswith("svc:close:")


@pytest.mark.asyncio
async def test_client_messages_carry_no_buttons_in_either_forum(whieda_tenant, whieda_bot_binding, two_admins, both_forums, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 601})
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.get_open_ticket_for_user", AsyncMock(return_value=_gemini_ticket(forum_chat_id=SERVICES_FORUM, forum_thread_id=69))
    ), patch("app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})), patch(
        "app.telegram.processor.handle_advisor_query", AsyncMock()
    ), patch("app.telegram.processor.handle_onboarding", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.handle_navigation_text", AsyncMock(return_value=None)
    ):
        await process_core_telegram_update(whieda_tenant, _message("В Одноклассниках ссылка не кликабельна", message_id=4567), "u3", binding=whieda_bot_binding)
    # Владелец: «очень много лишнего» — кнопки только в шапке темы.
    assert _keyboards(send, SERVICES_FORUM) == [None]


def test_group_filter_lets_move_and_site_confirm_through():
    group = {"id": SITE_FORUM, "type": "supergroup"}
    for data in ("svc:move:60001_4567", "site:confirm:" + "a" * 32, "site:reject:" + "a" * 32, "svc:close:x"):
        assert is_support_forum_traffic({"callback_query": {"data": data, "message": {"chat": group}}}), data
    assert not is_support_forum_traffic({"callback_query": {"data": "svc:order:gemini_6m", "message": {"chat": group}}})


@pytest.mark.asyncio
async def test_move_button_takes_the_burst_to_a_site_ticket_and_tells_the_partner(whieda_tenant, whieda_bot_binding, two_admins, both_forums):
    at = datetime(2026, 9, 27, 16, 5, tzinfo=timezone.utc)
    origin = _gemini_ticket(ticket_no=5, forum_chat_id=SERVICES_FORUM, forum_thread_id=69, user_display="Елена Антонова (@lite77777)")
    pressed = {"message_id": "m1", "ticket_id": origin["ticket_id"], "created_at": at, "source_chat_id": USER, "source_message_id": 4567, "text": "В Одноклассниках ссылка не кликабельна"}
    burst = [pressed, {**pressed, "message_id": "m2", "source_message_id": 4568, "text": None, "telegram_file_id": "F"}]
    site_ticket = _site_ticket(ticket_no=8, user_display="Елена Антонова (@lite77777)")
    send = AsyncMock(return_value={"ok": True, "message_id": 900})
    copy = AsyncMock(side_effect=[{"ok": True, "message_id": 901}, {"ok": True, "message_id": 902}])
    move = AsyncMock()
    open_ticket = AsyncMock(return_value=site_ticket)
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.answer_callback_query", AsyncMock()), patch(
        "app.telegram.support.get_message_by_source", AsyncMock(return_value=pressed)
    ), patch("app.telegram.support.get_ticket", AsyncMock(return_value=origin)), patch(
        "app.telegram.support.list_user_burst", AsyncMock(return_value=burst)
    ), patch("app.telegram.support.open_or_reuse_ticket", open_ticket), patch(
        "app.telegram.support.create_forum_topic", AsyncMock(return_value={"ok": True, "message_thread_id": 12})
    ), patch("app.telegram.support.attach_forum_topic", AsyncMock(return_value={**site_ticket, "forum_chat_id": SITE_FORUM, "forum_thread_id": 12})), patch(
        "app.telegram.support.copy_telegram_message", copy
    ), patch("app.telegram.support.move_message_to_ticket", move), patch(
        "app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "h"})
    ):
        result = await process_core_telegram_update(
            whieda_tenant, _callback(f"svc:move:{USER}_4567", user=KARINA, chat={"id": SERVICES_FORUM, "type": "supergroup"}), "u4", binding=whieda_bot_binding
        )
    assert result["status"] == "moved" and result["moved"] == 2 and result["to"] == "#S-8"
    assert open_ticket.await_args.kwargs["channel_code"] == "site" and open_ticket.await_args.kwargs["admin_telegram_user_id"] == OWNER
    assert [c.kwargs["message_id"] for c in copy.await_args_list] == [4567, 4568]
    assert all(c.kwargs["chat_id"] == str(SITE_FORUM) and c.kwargs["message_thread_id"] == 12 for c in copy.await_args_list)
    assert [c.kwargs["delivered_message_id"] for c in move.await_args_list] == [901, 902]
    header = [c.kwargs["text"] for c in send.await_args_list if c.kwargs["chat_id"] == str(SITE_FORUM)][0]
    assert "↪ Перенесено из заявки Gemini #S-5" in header
    to_user = [c.kwargs["text"] for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)]
    assert to_user and "#S-8" in to_user[0]


@pytest.mark.asyncio
async def test_move_is_refused_to_a_stranger_in_private_chat(whieda_tenant, whieda_bot_binding, two_admins, both_forums):
    origin = _gemini_ticket(forum_chat_id=SERVICES_FORUM, forum_thread_id=69)
    pressed = {"message_id": "m1", "ticket_id": origin["ticket_id"], "created_at": datetime.now(timezone.utc), "source_chat_id": USER, "source_message_id": 1}
    with patch("app.telegram.support.send_telegram_text", AsyncMock(return_value={"ok": True})), patch("app.telegram.support.answer_callback_query", AsyncMock()), patch(
        "app.telegram.support.get_message_by_source", AsyncMock(return_value=pressed)
    ), patch("app.telegram.support.get_ticket", AsyncMock(return_value=origin)), patch("app.telegram.support.open_or_reuse_ticket", AsyncMock()) as open_ticket:
        result = await process_core_telegram_update(whieda_tenant, _callback(f"svc:move:{USER}_1", user=777), "u5", binding=whieda_bot_binding)
    assert result["status"] == "forbidden"
    open_ticket.assert_not_called()


# ----------------------------------------------------------------------------
# Заявки на сайт → тема «Заявки на сайты» в WWC Support
# ----------------------------------------------------------------------------

def test_step_alert_goes_to_the_orders_topic_when_the_group_exists(monkeypatch):
    sent, copied = [], []

    class S:
        platform_billing_owner_telegram_id = "999"

    async def orders(tenant_id):
        return (SITE_FORUM, 5)

    async def send(**kw):
        sent.append(kw)
        return {"ok": True, "message_id": 1}

    async def copy(**kw):
        copied.append(kw)
        return {"ok": True, "message_id": 2}

    monkeypatch.setattr(sr, "get_settings", lambda: S())
    monkeypatch.setattr(sr, "_orders_topic", orders)
    monkeypatch.setattr(sr, "send_telegram_text", send)
    monkeypatch.setattr(sr, "copy_telegram_message", copy)
    monkeypatch.setattr(sr, "current_bot_binding", lambda: type("B", (), {"bot_token": "t", "binding_id": "b"})())
    from app.telegram.update_parser import TelegramMessage

    msg = TelegramMessage(chat_id=111, user_id=111, message_id=7, text="", chat_type="private", file_id="F1", raw={}, username="partner")
    asyncio.run(sr._notify_owner_step("whieda", msg, {"requested_subdomain": "olga", "status": "awaiting_text"}, done="фото"))
    assert copied[0]["chat_id"] == str(SITE_FORUM) and copied[0]["message_thread_id"] == 5
    assert sent[0]["chat_id"] == str(SITE_FORUM) and sent[0]["message_thread_id"] == 5
    assert "фото получено" in sent[0]["text"]


def test_orders_topic_failure_falls_back_to_the_owner_and_forgets_the_topic(monkeypatch):
    calls, forgotten = [], []

    class S:
        platform_billing_owner_telegram_id = "999"

    async def orders(tenant_id):
        return (SITE_FORUM, 5)

    async def send(**kw):
        calls.append(kw["chat_id"])
        return {"ok": kw["chat_id"] != str(SITE_FORUM)}

    async def forget(tenant_id):
        forgotten.append(tenant_id)

    monkeypatch.setattr(sr, "get_settings", lambda: S())
    monkeypatch.setattr(sr, "_orders_topic", orders)
    monkeypatch.setattr(sr, "_forget_orders_topic", forget)
    monkeypatch.setattr(sr, "send_telegram_text", send)
    monkeypatch.setattr(sr, "current_bot_binding", lambda: type("B", (), {"bot_token": "t", "binding_id": "b"})())
    asyncio.run(sr._send_to_owner("whieda", "Новая заявка на сайт."))
    assert calls == [str(SITE_FORUM), "999"]
    assert forgotten == ["whieda"]


@pytest.mark.asyncio
async def test_confirm_is_accepted_from_the_orders_group_and_answers_in_its_topic(whieda_tenant, whieda_bot_binding, two_admins):
    token = "440c1051a566400fa64a0e31ce7546fe"
    confirmed = {"proof_chat_id": USER, "requested_subdomain": "fedorchenko", "idempotent": True}
    deliver = AsyncMock()
    callback = _callback(f"site:confirm:{token}", user=OWNER, chat={"id": SITE_FORUM, "type": "supergroup"})
    callback["callback_query"]["message"]["message_thread_id"] = 5
    callback["callback_query"]["message"]["is_topic_message"] = True
    with patch("app.telegram.site_requests.answer_callback_query", AsyncMock()), patch(
        "app.telegram.site_requests.get_forum", _forums(site=True, services=True)
    ), patch("app.telegram.site_requests.confirm_site_request", AsyncMock(return_value=confirmed)) as confirm, patch(
        "app.telegram.site_requests._deliver", deliver
    ):
        result = await process_core_telegram_update(whieda_tenant, callback, "u6", binding=whieda_bot_binding)
    assert result["route"] == "site_request_confirm"
    confirm.assert_awaited_once()
    assert deliver.await_args.kwargs.get("thread_id") == 5


@pytest.mark.asyncio
async def test_confirm_from_another_group_is_refused(whieda_tenant, whieda_bot_binding, two_admins):
    token = "440c1051a566400fa64a0e31ce7546fe"
    callback = _callback(f"site:confirm:{token}", user=OWNER, chat={"id": SERVICES_FORUM, "type": "supergroup"})
    with patch("app.telegram.site_requests.answer_callback_query", AsyncMock()), patch(
        "app.telegram.site_requests.get_forum", _forums(site=True, services=True)
    ), patch("app.telegram.site_requests.confirm_site_request", AsyncMock()) as confirm:
        result = await process_core_telegram_update(whieda_tenant, callback, "u7", binding=whieda_bot_binding)
    assert result["status"] == "private_chat_required"
    confirm.assert_not_called()


@pytest.mark.asyncio
async def test_reply_word_in_a_gemini_topic_moves_the_message_and_is_not_relayed(whieda_tenant, whieda_bot_binding, two_admins, both_forums):
    from tests.test_telegram_support_site import _forum_message

    origin = _gemini_ticket(ticket_no=5, forum_chat_id=SERVICES_FORUM, forum_thread_id=69, user_display="Елена Антонова (@lite77777)")
    row = {"message_id": "m1", "ticket_id": origin["ticket_id"], "direction": "user_to_admin", "created_at": datetime(2026, 9, 27, 16, 5, tzinfo=timezone.utc),
           "source_chat_id": USER, "source_message_id": 4567, "text": "В Одноклассниках ссылка не кликабельна"}
    site_ticket = _site_ticket(ticket_no=8, user_display="Елена Антонова (@lite77777)", forum_chat_id=SITE_FORUM, forum_thread_id=12, created=False)
    send = AsyncMock(return_value={"ok": True, "message_id": 900})
    update = _forum_message("в поддержку", chat=SERVICES_FORUM, user=KARINA, thread_id=69, message_id=333)
    update["message"]["reply_to_message"] = {"message_id": 82}
    by_delivery = AsyncMock(return_value=row)
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.find_ticket_by_forum_thread", AsyncMock(return_value=origin)
    ), patch("app.telegram.support.get_message_by_delivery", by_delivery), patch("app.telegram.support.get_ticket", AsyncMock(return_value=origin)), patch(
        "app.telegram.support.list_user_burst", AsyncMock(return_value=[row])
    ), patch("app.telegram.support.open_or_reuse_ticket", AsyncMock(return_value=site_ticket)), patch(
        "app.telegram.support.copy_telegram_message", AsyncMock(return_value={"ok": True, "message_id": 901})
    ), patch("app.telegram.support.move_message_to_ticket", AsyncMock()) as move, patch(
        "app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "h"})
    ):
        result = await process_core_telegram_update(whieda_tenant, update, "u8", binding=whieda_bot_binding)
    assert result["status"] == "moved" and result["to"] == "#S-8"
    by_delivery.assert_awaited_once_with("whieda", delivered_chat_id=SERVICES_FORUM, delivered_message_id=82)
    move.assert_awaited_once()
    to_user = [c.kwargs["text"] for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)]
    assert to_user and all("в поддержку" != t for t in to_user) and "#S-8" in to_user[0]
    assert not any(t.startswith("Ответ администратора") for t in to_user), "the word is a command, not an answer"


@pytest.mark.asyncio
async def test_pressing_order_again_adds_one_line_not_a_second_header(whieda_tenant, whieda_bot_binding, two_admins, both_forums):
    existing = _gemini_ticket(ticket_no=5, forum_chat_id=SERVICES_FORUM, forum_thread_id=69, created=False)
    send = AsyncMock(return_value={"ok": True, "message_id": 950})
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.answer_callback_query", AsyncMock()), patch(
        "app.telegram.support.open_or_reuse_ticket", AsyncMock(return_value=existing)
    ), patch("app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})):
        await process_core_telegram_update(whieda_tenant, _callback("svc:confirm:gemini_18m"), "u9", binding=whieda_bot_binding)
    to_forum = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(SERVICES_FORUM)]
    assert len(to_forum) == 1
    assert to_forum[0]["text"].startswith("Клиент WWC · Заявка #S-5 · клиент снова нажал «Заказать»")
    assert to_forum[0].get("reply_markup") is None


@pytest.mark.asyncio
async def test_old_paid_button_on_a_site_ticket_explains_instead_of_offering_gemini(whieda_tenant, whieda_bot_binding, two_admins):
    ticket = _site_ticket(ticket_no=6, forum_chat_id=SITE_FORUM, forum_thread_id=8)
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    callback = _callback(f"sale:paid:{ticket['ticket_id']}", user=OWNER, chat={"id": SITE_FORUM, "type": "supergroup"})
    callback["callback_query"]["message"]["message_thread_id"] = 8
    with patch("app.telegram.service_sales.send_telegram_text", send), patch("app.telegram.service_sales.answer_callback_query", AsyncMock()), patch(
        "app.telegram.service_sales.get_ticket", AsyncMock(return_value=ticket)
    ), patch("app.telegram.service_sales.get_sale_for_ticket", AsyncMock()) as sale:
        result = await process_core_telegram_update(whieda_tenant, callback, "u10", binding=whieda_bot_binding)
    assert result["status"] == "site_ticket"
    sale.assert_not_called()
    text = send.await_args.kwargs["text"]
    assert "только для заказов Gemini" in text and "Заявки на сайты" in text
    assert send.await_args.kwargs.get("reply_markup") is None

