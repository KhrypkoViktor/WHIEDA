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


@pytest.fixture(autouse=True)
def no_forum():
    with patch("app.telegram.support.get_forum", AsyncMock(return_value=None)):
        yield


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
    assert buttons == ["Gemini Pro 4 490 ₽", "Gemini Pro 3 990 ₽", "Поддержка", "Как это работает?"]


@pytest.mark.asyncio
async def test_cabinet_button_opens_the_card_and_how_it_works_explains_without_names(whieda_tenant, whieda_bot_binding, support_env):
    from app.telegram.support import HOW_IT_WORKS_TEXT

    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.answer_callback_query", AsyncMock()):
        card = await process_core_telegram_update(whieda_tenant, _callback("svc:card:gemini"), "t1c", binding=whieda_bot_binding)
        how = await process_core_telegram_update(whieda_tenant, _callback("svc:how:gemini"), "t1d", binding=whieda_bot_binding)
    assert card["route"] == "services" and send.await_args_list[0].kwargs["text"] == SERVICES_TEXT
    assert how["status"] == "how_it_works"
    text = send.await_args_list[1].kwargs["text"]
    assert text == HOW_IT_WORKS_TEXT and "Карина" not in text and "@" not in text
    assert "номер заявки" in text and "закрывает заявку" in text


@pytest.mark.asyncio
async def test_start_gemini_from_the_site_button_shows_the_card(whieda_tenant, whieda_bot_binding, support_env, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.support.send_telegram_text", send):
        result = await process_core_telegram_update(whieda_tenant, _message("/start gemini"), "t1b", binding=whieda_bot_binding)
    assert result["route"] == "services"
    assert send.await_args.kwargs["text"] == SERVICES_TEXT


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
    assert "Клиент WWC · Заявка #S-1042 · Заказ: Gemini Pro, лицензия на 18 месяцев — 4 490 ₽" in to_admin[0]["text"]
    # The administrator never sees the person: no name, no @username, no link.
    assert "Ольга" not in to_admin[0]["text"] and "@olga" not in to_admin[0]["text"]
    assert "Reply" in to_admin[0]["text"]
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
    assert kwargs["text"] == "Клиент WWC · Заявка #S-1042\nКогда активируете?"
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
    assert "→ отправлено: Клиент WWC · Заявка #S-1042" == to_admin[0]["text"]


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
    assert "Reply" in text and "Заявка #S-1042" in text and "Заявка #S-1043" in text
    assert "Ольга" not in text and "Иван" not in text


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
    ), patch("app.telegram.support.close_ticket", closed), patch(
        "app.telegram.support.get_ticket", AsyncMock(return_value=_ticket())
    ):
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
    with patch("app.telegram.support.answer_callback_query", AsyncMock()), patch(
        "app.telegram.support.close_ticket", closed
    ), patch("app.telegram.support.get_ticket", AsyncMock(return_value=_ticket())):
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


def test_services_command_works_on_the_production_minimal_profile(monkeypatch: pytest.MonkeyPatch):
    """Owner signed «сервисы» off for production on 15.09.2026: the text command
    opens the services card on every UI profile, including `minimal`."""
    from app.settings import get_settings
    from app.telegram.support import is_services_request

    monkeypatch.setenv("PLATFORM_TELEGRAM_UI_PROFILE", "minimal")
    get_settings.cache_clear()
    try:
        assert is_services_request("сервисы") and is_services_request("/services")
        assert not is_services_request("оплата")
    finally:
        get_settings.cache_clear()


# ----------------------------------------------------------------------------
# Forum group: one topic per ticket (owner, 15.09.2026)
# ----------------------------------------------------------------------------

FORUM = -1001234567890
KARINA = 2101187096


def _forum_message(text: str, *, user: int = KARINA, thread_id: int | None = 77, message_id: int = 300, is_forum: bool = True, is_bot: bool = False) -> dict:
    message = {
        "message_id": message_id,
        "text": text,
        "chat": {"id": FORUM, "type": "supergroup", "title": "WWC поддержка", "is_forum": is_forum},
        "from": {"id": user, "first_name": "Карина", "is_bot": is_bot},
    }
    if thread_id is not None:
        message["is_topic_message"] = True
        message["message_thread_id"] = thread_id
    return {"message": message}


def _forum_ticket(**over) -> dict:
    return _ticket(**{"forum_chat_id": FORUM, "forum_thread_id": 77, **over})


@pytest.fixture
def forum_env(monkeypatch: pytest.MonkeyPatch):
    from app.settings import get_settings

    monkeypatch.setenv("PLATFORM_SUPPORT_ADMIN_TELEGRAM_ID", str(KARINA))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(ADMIN))
    get_settings.cache_clear()
    with patch("app.telegram.support.get_forum", AsyncMock(return_value={"chat_id": FORUM, "binding_id": "whieda-test-binding"})):
        yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_owner_registers_the_forum_group_with_slash_forum(whieda_tenant, whieda_bot_binding, forum_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    register = AsyncMock(return_value={"chat_id": FORUM})
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.register_forum", register), patch(
        "app.telegram.support.list_open_tickets_for_admin", AsyncMock(return_value=[])
    ):
        result = await process_core_telegram_update(
            whieda_tenant, _forum_message("/forum", user=ADMIN, thread_id=None), "f1", binding=whieda_bot_binding
        )
    assert result["status"] == "registered"
    assert register.await_args.kwargs["chat_id"] == FORUM and register.await_args.kwargs["binding_id"] == "whieda-test-binding"
    assert "подключена" in send.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_registration_moves_open_tickets_into_topics_with_history(whieda_tenant, whieda_bot_binding, forum_env):
    """A ticket opened before the group existed (Samtsova, 15.09) gets its topic
    on /forum, with the conversation so far replayed."""
    send = AsyncMock(return_value={"ok": True, "message_id": 900})
    create_topic = AsyncMock(return_value={"ok": True, "message_thread_id": 78})
    open_ticket = _ticket(admin_telegram_user_id=KARINA)
    history = [
        {"direction": "user_to_admin", "text": "Хочу за 4490", "telegram_file_id": None},
        {"direction": "admin_to_user", "text": "Добрый день", "telegram_file_id": None},
    ]
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.register_forum", AsyncMock(return_value={"chat_id": FORUM})
    ), patch("app.telegram.support.list_open_tickets_for_admin", AsyncMock(return_value=[open_ticket])), patch(
        "app.telegram.support.create_forum_topic", create_topic
    ), patch("app.telegram.support.attach_forum_topic", AsyncMock(return_value=_forum_ticket(forum_thread_id=78, admin_telegram_user_id=KARINA))), patch(
        "app.telegram.support.list_ticket_messages", AsyncMock(return_value=history)
    ), patch("app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})):
        result = await process_core_telegram_update(
            whieda_tenant, _forum_message("/forum", user=ADMIN, thread_id=None), "f1b", binding=whieda_bot_binding
        )
    assert result["status"] == "registered" and result["moved"] == 1
    assert create_topic.await_args.kwargs["name"] == "#S-1042 · Gemini Pro, лицензия на 18 месяцев"
    replay = [c.kwargs for c in send.await_args_list if c.kwargs.get("message_thread_id") == 78][0]
    assert replay["text"].startswith("Клиент WWC · Заявка #S-1042 · Заказ: Gemini Pro, лицензия на 18 месяцев")
    assert "Клиент: Хочу за 4490" in replay["text"] and "Администратор: Добрый день" in replay["text"]
    assert "Ольга" not in replay["text"]
    assert "перенесены в темы: 1" in send.await_args_list[-1].kwargs["text"]


@pytest.mark.asyncio
async def test_registration_explains_missing_manage_topics_right(whieda_tenant, whieda_bot_binding, forum_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.register_forum", AsyncMock(return_value={"chat_id": FORUM})
    ), patch("app.telegram.support.list_open_tickets_for_admin", AsyncMock(return_value=[_ticket(admin_telegram_user_id=KARINA)])), patch(
        "app.telegram.support.create_forum_topic", AsyncMock(return_value={"ok": False, "description": "Bad Request: not enough rights to create a topic"})
    ):
        result = await process_core_telegram_update(
            whieda_tenant, _forum_message("/forum", user=ADMIN, thread_id=None), "f1c", binding=whieda_bot_binding
        )
    assert result["status"] == "registered" and result["moved"] == 0
    text = send.await_args_list[-1].kwargs["text"]
    assert "Управление темами" in text and "/forum ещё раз" in text


@pytest.mark.asyncio
async def test_stranger_cannot_register_the_forum(whieda_tenant, whieda_bot_binding, forum_env):
    register = AsyncMock()
    with patch("app.telegram.support.register_forum", register):
        result = await process_core_telegram_update(
            whieda_tenant, _forum_message("/forum", user=USER, thread_id=None), "f2", binding=whieda_bot_binding
        )
    assert result["status"] == "forbidden"
    register.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_ticket_opens_a_topic_and_header_goes_into_it(whieda_tenant, whieda_bot_binding, forum_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 501})
    create_topic = AsyncMock(return_value={"ok": True, "message_thread_id": 77})
    attach = AsyncMock(return_value=_forum_ticket())
    record = AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.answer_callback_query", AsyncMock()
    ), patch("app.telegram.support.open_or_reuse_ticket", AsyncMock(return_value=_ticket())), patch(
        "app.telegram.support.create_forum_topic", create_topic
    ), patch("app.telegram.support.attach_forum_topic", attach), patch("app.telegram.support.record_relayed_message", record):
        result = await process_core_telegram_update(whieda_tenant, _callback("svc:confirm:gemini_18m"), "f3", binding=whieda_bot_binding)
    assert result["status"] == "ticket_opened"
    assert create_topic.await_args.kwargs["chat_id"] == str(FORUM)
    assert create_topic.await_args.kwargs["name"] == "#S-1042 · Gemini Pro, лицензия на 18 месяцев"
    assert attach.await_args.kwargs == {"ticket_id": "11111111-1111-1111-1111-111111111111", "forum_chat_id": FORUM, "forum_thread_id": 77}
    to_forum = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(FORUM)]
    assert len(to_forum) == 1 and to_forum[0]["message_thread_id"] == 77
    assert to_forum[0]["text"].startswith("Клиент WWC · Заявка #S-1042 · Заказ: Gemini Pro, лицензия на 18 месяцев — 4 490 ₽")
    assert "Пишите в эту тему" in to_forum[0]["text"] and "Ольга" not in to_forum[0]["text"]
    # Nothing goes to the administrator's private chat.
    assert not [c for c in send.await_args_list if c.kwargs["chat_id"] == str(KARINA)]
    assert record.await_args.kwargs["delivered_chat_id"] == FORUM


@pytest.mark.asyncio
async def test_topic_failure_falls_back_to_private_admin_chat(whieda_tenant, whieda_bot_binding, forum_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 501})
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.answer_callback_query", AsyncMock()
    ), patch("app.telegram.support.open_or_reuse_ticket", AsyncMock(return_value=_ticket(admin_telegram_user_id=KARINA))), patch(
        "app.telegram.support.create_forum_topic", AsyncMock(return_value={"ok": False, "status_code": 400})
    ), patch("app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})):
        result = await process_core_telegram_update(whieda_tenant, _callback("svc:confirm:gemini_18m"), "f4", binding=whieda_bot_binding)
    assert result["status"] == "ticket_opened"
    to_admin = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(KARINA)]
    assert len(to_admin) == 1 and "Reply" in to_admin[0]["text"]


@pytest.mark.asyncio
async def test_client_text_lands_in_the_ticket_topic(whieda_tenant, whieda_bot_binding, forum_env, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 601})
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.get_open_ticket_for_user", AsyncMock(return_value=_forum_ticket())
    ), patch("app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})), patch(
        "app.telegram.processor.handle_advisor_query", AsyncMock()
    ), patch("app.telegram.processor.handle_onboarding", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.handle_navigation_text", AsyncMock(return_value=None)
    ):
        result = await process_core_telegram_update(whieda_tenant, _message("Когда активируете?"), "f5", binding=whieda_bot_binding)
    assert result["direction"] == "user_to_admin"
    kwargs = send.await_args.kwargs
    assert kwargs["chat_id"] == str(FORUM) and kwargs["message_thread_id"] == 77
    assert kwargs["text"] == "Клиент WWC · Заявка #S-1042\nКогда активируете?"


@pytest.mark.asyncio
async def test_admin_text_in_the_topic_goes_to_the_client_with_a_reaction_receipt(whieda_tenant, whieda_bot_binding, forum_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 701})
    react = AsyncMock(return_value={"ok": True})
    find = AsyncMock(return_value=_forum_ticket())
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.find_ticket_by_forum_thread", find
    ), patch("app.telegram.support.set_message_reaction", react), patch(
        "app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    ):
        result = await process_core_telegram_update(whieda_tenant, _forum_message("Активирую сегодня"), "f6", binding=whieda_bot_binding)
    assert result["direction"] == "admin_to_user"
    find.assert_awaited_once_with("whieda", forum_chat_id=FORUM, forum_thread_id=77)
    to_user = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)]
    assert to_user[0]["text"] == "Ответ администратора по заявке #S-1042:\nАктивирую сегодня"
    # No «→ отправлено» line in the topic — the 👍 reaction is the receipt.
    assert not [c for c in send.await_args_list if c.kwargs["chat_id"] == str(FORUM)]
    assert react.await_args.kwargs["message_id"] == 300 and react.await_args.kwargs["chat_id"] == str(FORUM)


@pytest.mark.asyncio
async def test_messages_outside_ticket_topics_and_from_bots_are_ignored(whieda_tenant, whieda_bot_binding, forum_env):
    find = AsyncMock(return_value=None)
    with patch("app.telegram.support.find_ticket_by_forum_thread", find), patch("app.telegram.support.send_telegram_text", AsyncMock()):
        general = await process_core_telegram_update(whieda_tenant, _forum_message("привет всем", thread_id=None), "f7", binding=whieda_bot_binding)
        unknown = await process_core_telegram_update(whieda_tenant, _forum_message("что-то", thread_id=5), "f8", binding=whieda_bot_binding)
        from_bot = await process_core_telegram_update(whieda_tenant, _forum_message("эхо", is_bot=True), "f9", binding=whieda_bot_binding)
    assert general["route"] == "ignored_group_message"
    assert unknown["route"] == "ignored_group_message"
    assert from_bot["route"] == "ignored_group_message"


@pytest.mark.asyncio
async def test_closing_from_the_topic_closes_the_ticket_and_the_topic(whieda_tenant, whieda_bot_binding, forum_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    closed = AsyncMock(return_value=_forum_ticket(status="closed"))
    edit_topic, close_topic = AsyncMock(return_value={"ok": True}), AsyncMock(return_value={"ok": True})
    callback = _callback("svc:close:11111111-1111-1111-1111-111111111111", user=KARINA)
    callback["callback_query"]["message"]["chat"] = {"id": FORUM, "type": "supergroup"}
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.answer_callback_query", AsyncMock()
    ), patch("app.telegram.support.get_ticket", AsyncMock(return_value=_forum_ticket())), patch(
        "app.telegram.support.close_ticket", closed
    ), patch("app.telegram.support.edit_forum_topic", edit_topic), patch("app.telegram.support.close_forum_topic", close_topic):
        result = await process_core_telegram_update(whieda_tenant, callback, "f10", binding=whieda_bot_binding)
    assert result["status"] == "closed"
    to_user = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)]
    assert "Обращение #S-1042 закрыто" in to_user[0]["text"]
    assert edit_topic.await_args.kwargs["name"] == "✅ #S-1042 · Gemini Pro, лицензия на 18 месяцев"
    assert close_topic.await_args.kwargs == {"chat_id": str(FORUM), "message_thread_id": 77, "bot_token": close_topic.await_args.kwargs["bot_token"]}


@pytest.mark.asyncio
async def test_admin_private_message_never_reaches_a_forum_ticket(whieda_tenant, whieda_bot_binding, forum_env, quiet_linking):
    """Forum tickets are excluded from the private-chat «only open ticket» routing
    (the storage filter), so a stray private note from the administrator is
    not relayed to anyone."""
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    advisor = AsyncMock(return_value={"ok": True, "route": "advisor"})
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.list_open_tickets_for_admin", AsyncMock(return_value=[])
    ), patch("app.telegram.processor.handle_advisor_query", advisor), patch(
        "app.telegram.processor.handle_onboarding", AsyncMock(return_value=None)
    ), patch("app.telegram.processor.handle_navigation_text", AsyncMock(return_value=None)), patch(
        "app.telegram.support.get_open_ticket_for_user", AsyncMock(return_value=None)
    ):
        result = await process_core_telegram_update(whieda_tenant, _message("сколько стоит лицензия", user=KARINA), "f11", binding=whieda_bot_binding)
    assert result["route"] == "advisor"
    assert not [c for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)]
