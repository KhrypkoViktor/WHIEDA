"""Messages without text for «Добавить из Telegram»: users_shared and a forwarded contact.

The parser, the durable inbox compaction (it keeps only a few fields of an update) and the road of
such an update through the router to the processor — an update that was lost on the way once already
(photos without a caption, 23.09.2026).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram import processor, routes
from app.telegram.bindings import binding_context_scope
from app.telegram.inbox import compact_telegram_payload
from app.telegram.update_parser import (
    SharedContact,
    SharedUser,
    SharedUsers,
    parse_shared_message,
    parse_telegram_message,
)


def _update(message: dict, *, chat_type: str = "private", user_id: int = 5, update_id: int = 100) -> dict:
    base = {"message_id": 7, "chat": {"id": user_id, "type": chat_type}, "from": {"id": user_id, "username": "igor"}}
    return {"update_id": update_id, "message": {**base, **message}}


USERS_SHARED = {
    "request_id": 4821,
    "users": [
        {"user_id": 111, "first_name": "Татьяна", "last_name": "Козлова", "username": "tanya_k"},
        {"user_id": 222, "first_name": "Иван"},
    ],
}
CONTACT = {"phone_number": "79000000000", "first_name": "Татьяна", "last_name": "Козлова", "user_id": 111,
           "vcard": "BEGIN:VCARD\nTEL:79000000000\nEND:VCARD"}


def test_users_shared_is_parsed_with_names_and_addresses():
    msg = parse_shared_message(_update({"users_shared": USERS_SHARED}))
    assert msg is not None and msg.text == "" and msg.attachment_id is None and msg.shared_contact is None
    assert (msg.chat_id, msg.user_id, msg.chat_type, msg.message_id) == (5, 5, "private", 7)
    assert msg.users_shared == SharedUsers(
        request_id=4821,
        users=(
            SharedUser(user_id=111, first_name="Татьяна", last_name="Козлова", username="tanya_k"),
            SharedUser(user_id=222, first_name="Иван", last_name="", username=None),
        ),
    )


def test_users_shared_old_bot_api_form_gives_ids_only():
    msg = parse_shared_message(_update({"users_shared": {"request_id": 4821, "user_ids": [111, "222", "x", None]}}))
    assert msg is not None
    assert [user.user_id for user in msg.users_shared.users] == [111, 222]
    assert msg.users_shared.users[0].first_name == ""


def test_contact_is_parsed_without_the_vcard():
    msg = parse_shared_message(_update({"contact": CONTACT}))
    assert msg is not None and msg.users_shared is None and msg.text == ""
    assert msg.shared_contact == SharedContact(
        phone_number="79000000000", first_name="Татьяна", last_name="Козлова", user_id=111
    )
    own = parse_shared_message(_update({"contact": {"phone_number": "79000000001", "first_name": "Я"}}))
    assert own.shared_contact.user_id is None


@pytest.mark.parametrize(
    "message",
    [
        {"users_shared": {"request_id": "x", "users": [{"user_id": 1}]}},   # no number of the request
        {"users_shared": {"request_id": 4821, "users": []}},                # nobody chosen
        {"users_shared": {"request_id": 4821, "users": [{"first_name": "Без id"}]}},
        {"users_shared": "x"},
        {"contact": {"first_name": "Без номера"}},
        {"contact": {"phone_number": "  ", "first_name": "Пусто"}},
        {"contact": "x"},
        {},
    ],
)
def test_nothing_usable_is_none(message):
    assert parse_shared_message(_update(message)) is None


def test_texts_and_files_are_not_shared_messages():
    assert parse_shared_message(_update({"text": "привет", "contact": CONTACT})) is None
    assert parse_shared_message(_update({"caption": "подпись", "contact": CONTACT})) is None
    assert parse_shared_message(_update({"photo": [{"file_id": "f"}], "contact": CONTACT})) is None
    assert parse_shared_message({"update_id": 1}) is None
    # no chat or no sender: not a message the bot can answer
    assert parse_shared_message({"update_id": 1, "message": {"message_id": 1, "contact": CONTACT}}) is None


def test_the_ordinary_parser_still_drops_them_other_flows_do_not_see_them():
    """Показ, поддержка, заявки, витрины разбирают сообщения этой функцией: для них такое сообщение — «ничего»,
    как было. Забирает его только обработчик CRM по отдельному разбору."""
    assert parse_telegram_message(_update({"users_shared": USERS_SHARED})) is None
    assert parse_telegram_message(_update({"contact": CONTACT})) is None


def test_group_flags_and_sender_survive():
    msg = parse_shared_message(_update({"contact": CONTACT}, chat_type="supergroup"))
    assert msg.chat_type == "supergroup"
    assert msg.username == "igor"


def test_durable_inbox_keeps_the_picked_people_and_never_a_phone_number():
    payload = compact_telegram_payload(_update({"users_shared": USERS_SHARED}))
    assert payload["message"]["users_shared"] == {
        "request_id": 4821,
        "users": [
            {"user_id": 111, "first_name": "Татьяна", "last_name": "Козлова", "username": "tanya_k"},
            {"user_id": 222, "first_name": "Иван", "last_name": None, "username": None},
        ],
    }
    # a forwarded contact is a phone number: the queue never stores one (test_telegram_durable_inbox), so with
    # the durable inbox on it does not reach the CRM — the in-process path, the default, handles it
    compact_contact = compact_telegram_payload(_update({"contact": CONTACT}))
    assert "contact" not in compact_contact["message"] and "79000000000" not in str(compact_contact)
    assert parse_shared_message(compact_contact) is None
    # …и разобранное из сжатого обновления — те же поля, что из целого
    whole, compact = (
        parse_shared_message(_update({"users_shared": USERS_SHARED})),
        parse_shared_message(compact_telegram_payload(_update({"users_shared": USERS_SHARED}))),
    )
    assert whole.users_shared.request_id == compact.users_shared.request_id == 4821
    assert [u.user_id for u in whole.users_shared.users] == [u.user_id for u in compact.users_shared.users]
    assert whole.shared_contact is None and compact.shared_contact is None
    assert compact.users_shared.users[0].username == "tanya_k"


def test_ordinary_messages_compact_as_before():
    payload = compact_telegram_payload(_update({"text": "привет"}))
    assert "users_shared" not in payload["message"] and "contact" not in payload["message"]


# ---- the road: router → processor --------------------------------------------------------


@pytest.mark.asyncio
async def test_router_lets_a_shared_message_through_to_the_processor(whieda_bot_binding):
    update = _update({"users_shared": USERS_SHARED})
    process = AsyncMock(return_value={"ok": True})
    with patch.object(routes, "process_core_telegram_update", process), patch.object(
        routes, "try_handle_admin_login", AsyncMock(return_value=None)
    ), patch.object(routes, "try_handle_content_access", AsyncMock(return_value=None)):
        await routes._process_telegram_update_body(whieda_bot_binding, update, "t1")
    process.assert_awaited_once()
    assert process.await_args.args[1] is update


@pytest.mark.asyncio
async def test_router_still_drops_a_message_nobody_parses(whieda_bot_binding):
    process = AsyncMock(return_value={"ok": True})
    update = _update({"sticker": {"file_id": ""}})  # nothing usable: as before, never reaches the processor
    with patch.object(routes, "process_core_telegram_update", process):
        await routes._process_telegram_update_body(whieda_bot_binding, update, "t1")
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_router_ignores_a_shared_message_in_a_group(whieda_bot_binding):
    process = AsyncMock(return_value={"ok": True})
    with patch.object(routes, "process_core_telegram_update", process):
        await routes._process_telegram_update_body(
            whieda_bot_binding, _update({"contact": CONTACT}, chat_type="supergroup"), "t1"
        )
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_processor_hands_the_shared_message_to_the_crm_and_ignores_it_when_nobody_takes_it(
    whieda_tenant, whieda_bot_binding
):
    update = _update({"users_shared": USERS_SHARED})
    taken = AsyncMock(return_value={"ok": True, "route": "crm_add_tg"})
    with binding_context_scope(whieda_bot_binding), patch.object(processor, "try_handle_crm_shared", taken):
        result = await processor._process_core_telegram_update_scoped(whieda_tenant, update, "t1")
    assert result == {"ok": True, "route": "crm_add_tg"}
    assert taken.await_args.args[1].users_shared.request_id == 4821

    nobody = AsyncMock(return_value=None)
    with binding_context_scope(whieda_bot_binding), patch.object(processor, "try_handle_crm_shared", nobody):
        result = await processor._process_core_telegram_update_scoped(whieda_tenant, update, "t1")
    assert result == {"ok": True, "route": "ignored"}  # exactly what a textless message got before


@pytest.mark.asyncio
async def test_processor_never_offers_the_crm_a_message_with_text_or_a_sticker(whieda_tenant, whieda_bot_binding):
    taken = AsyncMock(return_value={"ok": True})
    with binding_context_scope(whieda_bot_binding), patch.object(processor, "try_handle_crm_shared", taken):
        result = await processor._process_core_telegram_update_scoped(
            whieda_tenant, _update({"sticker": {"file_id": ""}, "contact": {}}), "t1"
        )
    assert result == {"ok": True, "route": "ignored"}
    taken.assert_not_awaited()
