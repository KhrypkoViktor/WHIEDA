"""The support tunnel in the bot: card → confirm → ticket; relay both ways by Reply."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.processor import process_core_telegram_update
from app.telegram.support import OFFERS, SERVICES_TEXT

ADMIN = 688931415
USER = 60001


def _message(text: str, *, user: int = USER, reply_to: int | None = None, file_id: str | None = None, message_id: int = 11) -> dict:
    message = {
        "message_id": message_id,
        "text": text,
        "chat": {"id": user, "type": "private"},
        "from": {"id": user, "first_name": "Ольга", "username": "olga"},
    }
    if reply_to is not None:
        message["reply_to_message"] = {"message_id": reply_to, "from": {"id": 999, "is_bot": True}}
    if file_id:
        message.pop("text")
        message["photo"] = [{"file_id": file_id}]
    return {"message": message}


def _callback(data: str, *, user: int = USER) -> dict:
    return {
        "callback_query": {
            "id": "cb-1",
            "data": data,
            "from": {"id": user, "first_name": "Ольга", "username": "olga"},
            "message": {"message_id": 5, "chat": {"id": user, "type": "private"}},
        }
    }


def _ticket(**over) -> dict:
    base = {
        "ticket_id": "11111111-1111-1111-1111-111111111111",
        "ticket_no": 1042,
        "channel_code": "gemini",
        "offer_code": "gemini_18m",
        "offer_title": OFFERS["gemini_18m"].title,
        "user_telegram_user_id": USER,
        "user_chat_id": USER,
        "user_display": "Ольга (@olga)",
        "admin_telegram_user_id": ADMIN,
        "status": "open",
        "created": True,
    }
    return {**base, **over}


@pytest.fixture
def support_env(monkeypatch: pytest.MonkeyPatch):
    from app.settings import get_settings

    monkeypatch.setenv("PLATFORM_SUPPORT_ADMIN_TELEGRAM_ID", str(ADMIN))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def quiet_linking():
    with patch("app.telegram.processor.link_lead_actor_by_username", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.fill_lead_actor_user_id", AsyncMock(return_value=None)
    ):
        yield


@pytest.mark.asyncio
async def test_services_word_shows_gemini_card_with_three_buttons(whieda_tenant, whieda_bot_binding, support_env, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.support.send_telegram_text", send):
        result = await process_core_telegram_update(whieda_tenant, _message("Сервисы"), "t1", binding=whieda_bot_binding)
    assert result["route"] == "services"
    kwargs = send.await_args.kwargs
    assert kwargs["text"] == SERVICES_TEXT
    buttons = [row[0]["text"] for row in kwargs["reply_markup"]["inline_keyboard"]]
    assert buttons == ["Gemini Pro 4 490 ₽", "Gemini Pro 6 900 ₽", "Поддержка"]


@pytest.mark.asyncio
async def test_order_asks_for_confirmation_with_the_offer_card(whieda_tenant, whieda_bot_binding, support_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.answer_callback_query", AsyncMock()):
        result = await process_core_telegram_update(whieda_tenant, _callback("svc:order:gemini_18m"), "t2", binding=whieda_bot_binding)
    assert result["status"] == "confirm_requested"
    kwargs = send.await_args.kwargs
    assert "Лицензия на 18 месяцев" in kwargs["text"] and "4 490 ₽" in kwargs["text"] and "Оформить заказ?" in kwargs["text"]
    row = kwargs["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in row] == ["Заказать", "Отменить"]
    assert row[0]["callback_data"] == "svc:confirm:gemini_18m"


@pytest.mark.asyncio
async def test_confirm_opens_ticket_and_tells_both_sides(whieda_tenant, whieda_bot_binding, support_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 501})
    open_ticket = AsyncMock(return_value=_ticket())
    record = AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.answer_callback_query", AsyncMock()
    ), patch("app.telegram.support.open_or_reuse_ticket", open_ticket), patch(
        "app.telegram.support.record_relayed_message", record
    ):
        result = await process_core_telegram_update(whieda_tenant, _callback("svc:confirm:gemini_18m"), "t3", binding=whieda_bot_binding)
    assert result["status"] == "ticket_opened" and result["ticket"] == "#S-1042"
    open_ticket.assert_awaited_once()
    assert open_ticket.await_args.kwargs["admin_telegram_user_id"] == ADMIN
    assert open_ticket.await_args.kwargs["offer_code"] == "gemini_18m"
    # Admin gets the header with a Reply hint and a Close button; the user gets the receipt.
    to_admin = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(ADMIN)]
    to_user = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)]
    assert len(to_admin) == 1 and len(to_user) == 1
    assert "#S-1042 · Заказ: Gemini Pro, лицензия на 18 месяцев — 4 490 ₽" in to_admin[0]["text"]
    assert "Ольга (@olga)" in to_admin[0]["text"] and "Reply" in to_admin[0]["text"]
    assert to_admin[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith("svc:close:")
    assert "Заявка #S-1042 принята" in to_user[0]["text"]
    # The admin-side header is stored with its delivered message id for Reply routing.
    assert record.await_args.kwargs["delivered_message_id"] == 501


@pytest.mark.asyncio
async def test_user_text_inside_open_ticket_goes_to_admin_not_advisor(whieda_tenant, whieda_bot_binding, support_env, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 601})
    record = AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    advisor = AsyncMock()
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.get_open_ticket_for_user", AsyncMock(return_value=_ticket())
    ), patch("app.telegram.support.record_relayed_message", record), patch(
        "app.telegram.processor.handle_advisor_query", advisor
    ), patch("app.telegram.processor.handle_onboarding", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.handle_navigation_text", AsyncMock(return_value=None)
    ):
        result = await process_core_telegram_update(whieda_tenant, _message("Когда активируете?"), "t4", binding=whieda_bot_binding)
    assert result["route"] == "support_relay" and result["direction"] == "user_to_admin"
    advisor.assert_not_awaited()
    kwargs = send.await_args.kwargs
    assert kwargs["chat_id"] == str(ADMIN)
    assert kwargs["text"] == "#S-1042 · Ольга (@olga)\nКогда активируете?"
    assert record.await_args.kwargs["source_message_id"] == 11
    assert record.await_args.kwargs["delivered_message_id"] == 601


@pytest.mark.asyncio
async def test_admin_reply_is_routed_to_the_ticket_user(whieda_tenant, whieda_bot_binding, support_env, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 701})
    find = AsyncMock(return_value=_ticket())
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.find_ticket_by_admin_message", find
    ), patch("app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})):
        result = await process_core_telegram_update(
            whieda_tenant, _message("Активирую сегодня вечером", user=ADMIN, reply_to=501), "t5", binding=whieda_bot_binding
        )
    assert result["route"] == "support_relay" and result["direction"] == "admin_to_user"
    find.assert_awaited_once_with("whieda", admin_chat_id=ADMIN, message_id=501)
    to_user = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)]
    assert to_user[0]["text"] == "Ответ администратора по заявке #S-1042:\nАктивирую сегодня вечером"
    to_admin = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(ADMIN)]
    assert "→ отправлено: Ольга (@olga) (#S-1042)" == to_admin[0]["text"]


@pytest.mark.asyncio
async def test_admin_plain_message_goes_to_the_only_open_ticket(whieda_tenant, whieda_bot_binding, support_env, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 702})
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.list_open_tickets_for_admin", AsyncMock(return_value=[_ticket()])
    ), patch("app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})):
        result = await process_core_telegram_update(whieda_tenant, _message("Готово, проверьте почту", user=ADMIN), "t6", binding=whieda_bot_binding)
    assert result["direction"] == "admin_to_user"
    assert send.await_args_list[0].kwargs["chat_id"] == str(USER)


@pytest.mark.asyncio
async def test_admin_plain_message_with_several_open_tickets_asks_for_reply(whieda_tenant, whieda_bot_binding, support_env, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 703})
    tickets = [_ticket(), _ticket(ticket_no=1043, user_display="Иван", user_telegram_user_id=60002, user_chat_id=60002)]
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.list_open_tickets_for_admin", AsyncMock(return_value=tickets)
    ):
        result = await process_core_telegram_update(whieda_tenant, _message("Готово", user=ADMIN), "t7", binding=whieda_bot_binding)
    assert result["status"] == "ambiguous"
    text = send.await_args.kwargs["text"]
    assert "Reply" in text and "#S-1042 · Ольга (@olga)" in text and "#S-1043 · Иван" in text


@pytest.mark.asyncio
async def test_admin_owner_commands_are_never_relayed(whieda_tenant, whieda_bot_binding, support_env, quiet_linking):
    """On staging the support admin is also the billing owner; «оплата …» must stay a command."""
    listing = AsyncMock(return_value=[_ticket()])
    billing = AsyncMock(return_value={"ok": True, "route": "billing"})
    with patch("app.telegram.support.list_open_tickets_for_admin", listing), patch(
        "app.telegram.processor.try_handle_billing_message", billing
    ):
        result = await process_core_telegram_update(whieda_tenant, _message("оплата ref:x 30 WWC$ 3", user=ADMIN), "t8", binding=whieda_bot_binding)
    assert result["route"] == "billing"
    listing.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_closes_ticket_and_user_is_told(whieda_tenant, whieda_bot_binding, support_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    closed = AsyncMock(return_value=_ticket(status="closed"))
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.answer_callback_query", AsyncMock()
    ), patch("app.telegram.support.close_ticket", closed):
        result = await process_core_telegram_update(
            whieda_tenant, _callback("svc:close:11111111-1111-1111-1111-111111111111", user=ADMIN), "t9", binding=whieda_bot_binding
        )
    assert result["status"] == "closed"
    closed.assert_awaited_once_with("whieda", ticket_id="11111111-1111-1111-1111-111111111111", closed_by="admin")
    to_user = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)]
    assert "Обращение #S-1042 закрыто" in to_user[0]["text"]


@pytest.mark.asyncio
async def test_only_the_admin_can_close(whieda_tenant, whieda_bot_binding, support_env):
    closed = AsyncMock()
    with patch("app.telegram.support.answer_callback_query", AsyncMock()), patch("app.telegram.support.close_ticket", closed):
        result = await process_core_telegram_update(
            whieda_tenant, _callback("svc:close:11111111-1111-1111-1111-111111111111", user=USER), "t10", binding=whieda_bot_binding
        )
    assert result["status"] == "forbidden"
    closed.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_without_ticket_is_untouched(whieda_tenant, whieda_bot_binding, support_env, quiet_linking):
    advisor = AsyncMock(return_value={"ok": True, "route": "advisor"})
    with patch("app.telegram.support.get_open_ticket_for_user", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.handle_advisor_query", advisor
    ), patch("app.telegram.processor.handle_onboarding", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.handle_navigation_text", AsyncMock(return_value=None)
    ):
        result = await process_core_telegram_update(whieda_tenant, _message("Сколько стоит матрас?"), "t11", binding=whieda_bot_binding)
    assert result["route"] == "advisor"
    advisor.assert_awaited_once()
