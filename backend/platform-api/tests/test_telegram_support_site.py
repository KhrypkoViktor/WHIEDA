"""«Поддержка» as a ticket of kind «site» in its own forum (owner, 26.09.2026).

The menu button / «/support» / the word «поддержка» open a ticket that goes to
the owner (PLATFORM_BILLING_OWNER_TELEGRAM_ID), not to the Gemini administrator.
In the «site» forum the owner sees who writes: the topic is named after the
partner and the first message carries the name, the @username (or a tg://user
link) and the partner's site. The «services» forum (Gemini) keeps hiding names.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.processor import process_core_telegram_update
from app.telegram.support import SUPPORT_INVITE_TEXT, SUPPORT_SITE_CALLBACK, is_support_forum_traffic

OWNER = 688931415
KARINA = 2101187096
USER = 60001
SITE_FORUM = -1004000000001
SERVICES_FORUM = -1001234567890
BINDING = "whieda-test-binding"
SITE = {"ref_code": "olga", "url": "https://olga.wwc.best/"}


def _message(text: str, *, user: int = USER, reply_to: int | None = None, username: str | None = "olga", message_id: int = 11) -> dict:
    sender = {"id": user, "first_name": "Ольга", "last_name": "Самцова"}
    if username:
        sender["username"] = username
    message = {"message_id": message_id, "text": text, "chat": {"id": user, "type": "private"}, "from": sender}
    if reply_to is not None:
        message["reply_to_message"] = {"message_id": reply_to, "from": {"id": 999, "is_bot": True}}
    return {"message": message}


def _callback(data: str, *, user: int = USER, chat: dict | None = None) -> dict:
    return {
        "callback_query": {
            "id": "cb-1",
            "data": data,
            "from": {"id": user, "first_name": "Ольга", "last_name": "Самцова", "username": "olga"},
            "message": {"message_id": 5, "chat": chat or {"id": user, "type": "private"}},
        }
    }


def _forum_message(text: str, *, chat: int, user: int, thread_id: int | None = 77, message_id: int = 300) -> dict:
    message = {
        "message_id": message_id,
        "text": text,
        "chat": {"id": chat, "type": "supergroup", "title": "WWC сайты", "is_forum": True},
        "from": {"id": user, "first_name": "Виктор", "is_bot": False},
    }
    if thread_id is not None:
        message["is_topic_message"] = True
        message["message_thread_id"] = thread_id
    return {"message": message}


def _site_ticket(**over) -> dict:
    base = {
        "ticket_id": "22222222-2222-2222-2222-222222222222",
        "ticket_no": 1042,
        "channel_code": "site",
        "offer_code": None,
        "offer_title": None,
        "user_telegram_user_id": USER,
        "user_chat_id": USER,
        "user_display": "Ольга Самцова (@olga)",
        "admin_telegram_user_id": OWNER,
        "status": "open",
        "created": True,
    }
    return {**base, **over}


def _gemini_ticket(**over) -> dict:
    return _site_ticket(**{"ticket_id": "11111111-1111-1111-1111-111111111111", "ticket_no": 1043, "channel_code": "gemini", "admin_telegram_user_id": KARINA, **over})


@pytest.fixture
def two_admins(monkeypatch: pytest.MonkeyPatch):
    from app.settings import get_settings

    monkeypatch.setenv("PLATFORM_SUPPORT_ADMIN_TELEGRAM_ID", str(KARINA))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def quiet_linking():
    with patch("app.telegram.processor.link_lead_actor_by_username", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.fill_lead_actor_user_id", AsyncMock(return_value=None)
    ):
        yield


def _forums(*, site: bool, services: bool):
    async def fake(tenant_id: str, *, binding_id: str, kind: str = "services"):
        if kind == "site" and site:
            return {"chat_id": SITE_FORUM, "binding_id": binding_id, "kind": "site"}
        if kind == "services" and services:
            return {"chat_id": SERVICES_FORUM, "binding_id": binding_id, "kind": "services"}
        return None

    return AsyncMock(side_effect=fake)


@pytest.fixture
def no_forums():
    with patch("app.telegram.support.get_forum", _forums(site=False, services=False)), patch(
        "app.telegram.support.ensure_service_topics", AsyncMock(return_value=None)
    ), patch("app.telegram.support.partner_site_for_telegram_user", AsyncMock(return_value=None)):
        yield


@pytest.fixture
def both_forums():
    with patch("app.telegram.support.get_forum", _forums(site=True, services=True)), patch(
        "app.telegram.support.ensure_service_topics", AsyncMock(return_value=None)
    ), patch("app.telegram.support.partner_site_for_telegram_user", AsyncMock(return_value=SITE)):
        yield


# ----------------------------------------------------------------------------
# Opening: menu button, /support, the word — a «site» ticket for the owner
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_support_command_opens_a_site_ticket_addressed_to_the_owner(whieda_tenant, whieda_bot_binding, two_admins, no_forums, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 501})
    open_ticket = AsyncMock(return_value=_site_ticket())
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.open_or_reuse_ticket", open_ticket), patch(
        "app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    ):
        result = await process_core_telegram_update(whieda_tenant, _message("/support"), "s1", binding=whieda_bot_binding)
    assert result["status"] == "ticket_opened" and result["ticket"] == "#S-1042"
    assert open_ticket.await_args.kwargs["channel_code"] == "site"
    assert open_ticket.await_args.kwargs["admin_telegram_user_id"] == OWNER
    to_user = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)]
    assert len(to_user) == 1 and SUPPORT_INVITE_TEXT in to_user[0]["text"]
    assert "WhatsApp" in SUPPORT_INVITE_TEXT and "ответ придёт в этот чат" in SUPPORT_INVITE_TEXT
    # Fallback without a «site» forum: the owner's private chat, with the name.
    to_owner = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(OWNER)]
    assert len(to_owner) == 1 and "#S-1042" in to_owner[0]["text"] and "Ольга Самцова (@olga)" in to_owner[0]["text"]
    assert "Reply" in to_owner[0]["text"]
    assert not [c for c in send.await_args_list if c.kwargs["chat_id"] == str(KARINA)]


@pytest.mark.asyncio
async def test_the_word_and_the_cabinet_button_open_the_same_site_ticket(whieda_tenant, whieda_bot_binding, two_admins, no_forums, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 501})
    open_ticket = AsyncMock(return_value=_site_ticket())
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.open_or_reuse_ticket", open_ticket), patch(
        "app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    ), patch("app.telegram.support.answer_callback_query", AsyncMock()):
        by_word = await process_core_telegram_update(whieda_tenant, _message("Поддержка"), "s2a", binding=whieda_bot_binding)
        by_button = await process_core_telegram_update(whieda_tenant, _callback(SUPPORT_SITE_CALLBACK), "s2b", binding=whieda_bot_binding)
    assert by_word["status"] == "ticket_opened" and by_button["status"] == "ticket_opened"
    assert SUPPORT_SITE_CALLBACK == "svc:support:site"
    assert [c.kwargs["channel_code"] for c in open_ticket.await_args_list] == ["site", "site"]


@pytest.mark.asyncio
async def test_services_support_button_still_opens_a_gemini_ticket_without_names(whieda_tenant, whieda_bot_binding, two_admins, both_forums):
    send = AsyncMock(return_value={"ok": True, "message_id": 501})
    open_ticket = AsyncMock(return_value=_gemini_ticket())
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.open_or_reuse_ticket", open_ticket), patch(
        "app.telegram.support.answer_callback_query", AsyncMock()
    ), patch("app.telegram.support.create_forum_topic", AsyncMock(return_value={"ok": True, "message_thread_id": 90})), patch(
        "app.telegram.support.attach_forum_topic", AsyncMock(return_value=_gemini_ticket(forum_chat_id=SERVICES_FORUM, forum_thread_id=90))
    ), patch("app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})) as record:
        result = await process_core_telegram_update(whieda_tenant, _callback("svc:support:gemini"), "s3", binding=whieda_bot_binding)
    assert result["status"] == "ticket_opened"
    assert open_ticket.await_args.kwargs["channel_code"] == "gemini" and open_ticket.await_args.kwargs["admin_telegram_user_id"] == KARINA
    to_forum = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(SERVICES_FORUM)]
    assert len(to_forum) == 1 and to_forum[0]["message_thread_id"] == 90
    assert to_forum[0]["text"].startswith("Клиент WWC · Заявка #S-1043 · Вопрос по Gemini")
    assert "Ольга" not in to_forum[0]["text"] and "@olga" not in to_forum[0]["text"] and "wwc.best" not in to_forum[0]["text"]
    assert record.await_args.kwargs["delivered_chat_id"] == SERVICES_FORUM


# ----------------------------------------------------------------------------
# The «site» forum: topic named after the partner, first message with contacts
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_site_ticket_opens_a_topic_named_after_the_partner_in_the_site_forum(whieda_tenant, whieda_bot_binding, two_admins, both_forums, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 501})
    create_topic = AsyncMock(return_value={"ok": True, "message_thread_id": 77})
    attach = AsyncMock(return_value=_site_ticket(forum_chat_id=SITE_FORUM, forum_thread_id=77))
    record = AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.open_or_reuse_ticket", AsyncMock(return_value=_site_ticket())), patch(
        "app.telegram.support.create_forum_topic", create_topic
    ), patch("app.telegram.support.attach_forum_topic", attach), patch("app.telegram.support.record_relayed_message", record):
        result = await process_core_telegram_update(whieda_tenant, _message("поддержка"), "s4", binding=whieda_bot_binding)
    assert result["status"] == "ticket_opened"
    assert create_topic.await_args.kwargs["chat_id"] == str(SITE_FORUM)
    assert create_topic.await_args.kwargs["name"] == "#S-1042 · Ольга Самцова (@olga) · olga"
    assert attach.await_args.kwargs == {"ticket_id": "22222222-2222-2222-2222-222222222222", "forum_chat_id": SITE_FORUM, "forum_thread_id": 77}
    to_forum = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(SITE_FORUM)]
    assert len(to_forum) == 1 and to_forum[0]["message_thread_id"] == 77
    first = to_forum[0]["text"]
    assert "Ольга Самцова (@olga)" in first and "https://olga.wwc.best/" in first and "Пишите в эту тему" in first
    # Nothing in the owner's private chat and nothing in the services forum.
    assert not [c for c in send.await_args_list if c.kwargs["chat_id"] in {str(OWNER), str(SERVICES_FORUM), str(KARINA)}]
    assert record.await_args.kwargs["delivered_chat_id"] == SITE_FORUM


@pytest.mark.asyncio
async def test_partner_without_username_is_linked_by_id_in_the_first_message(whieda_tenant, whieda_bot_binding, two_admins, both_forums, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 501})
    ticket = _site_ticket(user_display="Ольга Самцова")
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.open_or_reuse_ticket", AsyncMock(return_value=ticket)), patch(
        "app.telegram.support.create_forum_topic", AsyncMock(return_value={"ok": True, "message_thread_id": 77})
    ), patch("app.telegram.support.attach_forum_topic", AsyncMock(return_value={**ticket, "forum_chat_id": SITE_FORUM, "forum_thread_id": 77})), patch(
        "app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    ):
        await process_core_telegram_update(whieda_tenant, _message("поддержка", username=None), "s5", binding=whieda_bot_binding)
    first = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(SITE_FORUM)][0]["text"]
    assert f'<a href="tg://user?id={USER}">Ольга Самцова</a>' in first


def test_delivery_keeps_user_mention_links_and_escapes_other_markup():
    from app.telegram.delivery import format_telegram_html

    kept = format_telegram_html('<a href="tg://user?id=60001">Ольга</a> и <a href="https://olga.wwc.best/">сайт</a>')
    assert kept == '<a href="tg://user?id=60001">Ольга</a> и <a href="https://olga.wwc.best/">сайт</a>'
    assert format_telegram_html('<a href="javascript:alert(1)">x</a>') == '&lt;a href="javascript:alert(1)"&gt;x&lt;/a&gt;'
    assert format_telegram_html("a < b <b>c</b>") == "a &lt; b <b>c</b>"


# ----------------------------------------------------------------------------
# Registration: «/forum site» in the new group; plain «/forum» stays services
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_registers_the_site_forum_with_slash_forum_site(whieda_tenant, whieda_bot_binding, two_admins, no_forums):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    register = AsyncMock(return_value={"chat_id": SITE_FORUM, "kind": "site"})
    listing = AsyncMock(return_value=[])
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.register_forum", register), patch(
        "app.telegram.support.list_open_tickets_for_admin", listing
    ), patch("app.telegram.support.ensure_service_topics", AsyncMock()) as topics:
        result = await process_core_telegram_update(
            whieda_tenant, _forum_message("/forum site", chat=SITE_FORUM, user=OWNER, thread_id=None), "r1", binding=whieda_bot_binding
        )
    assert result["status"] == "registered" and result["kind"] == "site"
    assert register.await_args.kwargs["chat_id"] == SITE_FORUM and register.await_args.kwargs["kind"] == "site"
    assert register.await_args.kwargs["binding_id"] == BINDING
    topics.assert_not_awaited()  # «Бонусы»/«Отчёты» belong to the Gemini forum only
    assert listing.await_args.kwargs["admin_telegram_user_id"] == OWNER and listing.await_args.kwargs["forum_kind"] == "site"
    assert "сайт" in send.await_args.kwargs["text"].lower()


@pytest.mark.asyncio
async def test_plain_slash_forum_still_registers_the_services_forum_for_the_administrator(whieda_tenant, whieda_bot_binding, two_admins, no_forums):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    register = AsyncMock(return_value={"chat_id": SERVICES_FORUM, "kind": "services"})
    listing = AsyncMock(return_value=[])
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.register_forum", register), patch(
        "app.telegram.support.list_open_tickets_for_admin", listing
    ), patch("app.telegram.support.ensure_service_topics", AsyncMock()) as topics:
        result = await process_core_telegram_update(
            whieda_tenant, _forum_message("/forum", chat=SERVICES_FORUM, user=KARINA, thread_id=None), "r2", binding=whieda_bot_binding
        )
    assert result["status"] == "registered" and result["kind"] == "services"
    assert register.await_args.kwargs["kind"] == "services"
    topics.assert_awaited_once()
    assert listing.await_args.kwargs["admin_telegram_user_id"] == KARINA and listing.await_args.kwargs["forum_kind"] == "services"


def test_forum_site_command_in_a_group_is_support_forum_traffic():
    update = {"message": {"message_id": 1, "text": "/forum site", "chat": {"id": SITE_FORUM, "type": "supergroup"}, "from": {"id": OWNER}}}
    assert is_support_forum_traffic(update)
    assert is_support_forum_traffic({"message": {**update["message"], "text": "/forum@WHIEDA_Advisor_bot site"}})
    assert not is_support_forum_traffic({"message": {**update["message"], "text": "/forum whatever"}})


# ----------------------------------------------------------------------------
# Relay both ways inside the site forum; the owner sees the name on every line
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partner_text_lands_in_the_site_topic_with_their_name(whieda_tenant, whieda_bot_binding, two_admins, both_forums, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 601})
    with patch("app.telegram.support.send_telegram_text", send), patch(
        "app.telegram.support.get_open_ticket_for_user", AsyncMock(return_value=_site_ticket(forum_chat_id=SITE_FORUM, forum_thread_id=77))
    ), patch("app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})), patch(
        "app.telegram.processor.handle_advisor_query", AsyncMock()
    ), patch("app.telegram.processor.handle_onboarding", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.handle_navigation_text", AsyncMock(return_value=None)
    ):
        result = await process_core_telegram_update(whieda_tenant, _message("Поменяйте WhatsApp на +7 900"), "l1", binding=whieda_bot_binding)
    assert result["direction"] == "user_to_admin"
    kwargs = send.await_args.kwargs
    assert kwargs["chat_id"] == str(SITE_FORUM) and kwargs["message_thread_id"] == 77
    assert kwargs["text"] == "#S-1042 · Ольга Самцова (@olga)\nПоменяйте WhatsApp на +7 900"


@pytest.mark.asyncio
async def test_owner_answer_in_the_site_topic_reaches_the_partner(whieda_tenant, whieda_bot_binding, two_admins, both_forums):
    send = AsyncMock(return_value={"ok": True, "message_id": 701})
    find = AsyncMock(return_value=_site_ticket(forum_chat_id=SITE_FORUM, forum_thread_id=77))
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.find_ticket_by_forum_thread", find), patch(
        "app.telegram.support.set_message_reaction", AsyncMock(return_value={"ok": True})
    ), patch("app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})):
        result = await process_core_telegram_update(
            whieda_tenant, _forum_message("Готово, WhatsApp обновил", chat=SITE_FORUM, user=OWNER), "l2", binding=whieda_bot_binding
        )
    assert result["direction"] == "admin_to_user"
    find.assert_awaited_once_with("whieda", forum_chat_id=SITE_FORUM, forum_thread_id=77)
    to_user = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)]
    assert to_user[0]["text"] == "Ответ команды WWC по обращению #S-1042:\nГотово, WhatsApp обновил"


@pytest.mark.asyncio
async def test_owner_private_reply_reaches_the_partner_when_there_is_no_site_forum(whieda_tenant, whieda_bot_binding, two_admins, no_forums, quiet_linking):
    """Fallback mode: the owner is not the Gemini administrator, yet a Reply on a
    «site» header in the owner's private chat must route back to the partner."""
    send = AsyncMock(return_value={"ok": True, "message_id": 702})
    find = AsyncMock(return_value=_site_ticket())
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.find_ticket_by_admin_message", find), patch(
        "app.telegram.support.record_relayed_message", AsyncMock(return_value={"duplicate": False, "message_id": "m"})
    ):
        result = await process_core_telegram_update(
            whieda_tenant, _message("Сделаю сегодня", user=OWNER, username="sunraysword", reply_to=501), "l3", binding=whieda_bot_binding
        )
    assert result["route"] == "support_relay" and result["direction"] == "admin_to_user"
    find.assert_awaited_once_with("whieda", admin_chat_id=OWNER, message_id=501)
    to_user = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)]
    assert to_user[0]["text"] == "Ответ команды WWC по обращению #S-1042:\nСделаю сегодня"
    to_owner = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(OWNER)]
    assert to_owner[0]["text"] == "→ отправлено: #S-1042 · Ольга Самцова (@olga)"


@pytest.mark.asyncio
async def test_owner_private_reply_unrelated_to_a_ticket_is_left_to_the_rest_of_the_bot(whieda_tenant, whieda_bot_binding, two_admins, no_forums, quiet_linking):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    advisor = AsyncMock(return_value={"ok": True, "route": "advisor"})
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.find_ticket_by_admin_message", AsyncMock(return_value=None)), patch(
        "app.telegram.support.list_open_tickets_for_admin", AsyncMock(return_value=[])
    ), patch("app.telegram.support.get_open_ticket_for_user", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.handle_advisor_query", advisor
    ), patch("app.telegram.processor.handle_onboarding", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.handle_navigation_text", AsyncMock(return_value=None)
    ):
        result = await process_core_telegram_update(
            whieda_tenant, _message("а это что за отчёт?", user=OWNER, username="sunraysword", reply_to=42), "l4", binding=whieda_bot_binding
        )
    assert result["route"] == "advisor"
    assert not [c for c in send.await_args_list if "не относится к обращению" in c.kwargs["text"]]


# ----------------------------------------------------------------------------
# Closing a site ticket
# ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_closing_from_the_site_topic_renames_it_with_the_partner_and_points_to_the_menu(whieda_tenant, whieda_bot_binding, two_admins, both_forums):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    open_ticket = _site_ticket(forum_chat_id=SITE_FORUM, forum_thread_id=77)
    edit_topic, close_topic = AsyncMock(return_value={"ok": True}), AsyncMock(return_value={"ok": True})
    callback = _callback("svc:close:22222222-2222-2222-2222-222222222222", user=OWNER, chat={"id": SITE_FORUM, "type": "supergroup"})
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.answer_callback_query", AsyncMock()), patch(
        "app.telegram.support.get_ticket", AsyncMock(return_value=open_ticket)
    ), patch("app.telegram.support.close_ticket", AsyncMock(return_value={**open_ticket, "status": "closed"})), patch(
        "app.telegram.support.edit_forum_topic", edit_topic
    ), patch("app.telegram.support.close_forum_topic", close_topic):
        result = await process_core_telegram_update(whieda_tenant, callback, "c1", binding=whieda_bot_binding)
    assert result["status"] == "closed"
    to_user = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(USER)][0]["text"]
    assert "Обращение #S-1042 закрыто" in to_user and "«Поддержка»" in to_user and "Сервисы" not in to_user
    assert edit_topic.await_args.kwargs["name"] == "✅ #S-1042 · Ольга Самцова (@olga) · olga"
    assert close_topic.await_args.kwargs["message_thread_id"] == 77


@pytest.mark.asyncio
async def test_owner_closes_a_fallback_site_ticket_from_private_chat_but_a_stranger_cannot(whieda_tenant, whieda_bot_binding, two_admins, no_forums):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    closed = AsyncMock(return_value=_site_ticket(status="closed"))
    with patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.answer_callback_query", AsyncMock()), patch(
        "app.telegram.support.get_ticket", AsyncMock(return_value=_site_ticket())
    ), patch("app.telegram.support.close_ticket", closed):
        stranger = await process_core_telegram_update(whieda_tenant, _callback("svc:close:22222222-2222-2222-2222-222222222222", user=USER), "c2", binding=whieda_bot_binding)
        owner = await process_core_telegram_update(whieda_tenant, _callback("svc:close:22222222-2222-2222-2222-222222222222", user=OWNER), "c3", binding=whieda_bot_binding)
    assert stranger["status"] == "forbidden" and owner["status"] == "closed"
    closed.assert_awaited_once()


# ----------------------------------------------------------------------------
# Cabinet: the «Поддержка» button is the ticket, not a link to a private chat
# ----------------------------------------------------------------------------

def test_cabinet_support_button_opens_the_site_ticket_on_both_profiles():
    from app.telegram.referral_bonus import _dashboard_keyboard

    for minimal in (True, False):
        rows = _dashboard_keyboard(bot_username="WHIEDA_bot", invite_code="x", site_url="https://olga.wwc.best/", has_site=True, minimal=minimal)["inline_keyboard"]
        assert rows[-1][0] == {"text": "Поддержка", "callback_data": SUPPORT_SITE_CALLBACK}
        assert not [b for row in rows for b in row if b.get("url") == "https://t.me/sunraysword"]
