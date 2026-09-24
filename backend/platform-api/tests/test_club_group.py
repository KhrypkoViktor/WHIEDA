"""Группа клуба: личная ссылка после оплаты и приветствие при вступлении (24.09.2026)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.telegram import club_group as cg


def _binding():
    return SimpleNamespace(bot_token="t")


def test_greeting_uses_username_when_present():
    text, entities = cg.club_greeting({"id": 1, "username": "LanaVesta", "first_name": "Светлана"})
    assert text.startswith("@LanaVesta, привет! Пройдись по закрепам")
    assert cg.CLUB_PINS_URL in text and entities == []


def test_greeting_mentions_by_id_without_username():
    text, entities = cg.club_greeting({"id": 6248242577, "first_name": "Татьяна"})
    assert text.startswith("Татьяна, привет!")
    assert entities == [{"type": "text_mention", "offset": 0, "length": 7, "user": {"id": 6248242577}}]


def test_join_in_club_group_greets_every_human():
    call = AsyncMock(return_value={"ok": True})
    update = {"message": {"chat": {"id": cg.CLUB_CHAT_ID}, "new_chat_members": [
        {"id": 1, "username": "a"}, {"id": 2, "is_bot": True, "username": "bot"}]}}
    with patch.object(cg, "_call_telegram", call), patch.object(cg, "current_bot_binding", _binding):
        result = asyncio.run(cg.try_handle_club_join(update, trace_id="t"))
    assert result["greeted"] == 1
    assert call.await_args.args[0] == "sendMessage"
    assert call.await_args.args[1]["chat_id"] == cg.CLUB_CHAT_ID


def test_other_chats_and_plain_messages_are_not_touched():
    other = {"message": {"chat": {"id": -100123}, "new_chat_members": [{"id": 1}]}}
    plain = {"message": {"chat": {"id": cg.CLUB_CHAT_ID}, "text": "привет"}}
    assert asyncio.run(cg.try_handle_club_join(other, trace_id="t")) is None
    assert asyncio.run(cg.try_handle_club_join(plain, trace_id="t")) is None


def test_new_member_gets_one_time_link_and_channel():
    call = AsyncMock(side_effect=[
        {"ok": True, "result": {"status": "left"}},
        {"ok": True, "result": {"invite_link": "https://t.me/+one"}},
    ])
    send = AsyncMock(return_value={"ok": True})
    with patch.object(cg, "_call_telegram", call), patch.object(cg, "send_telegram_text", send), patch.object(
        cg, "current_bot_binding", _binding
    ):
        assert asyncio.run(cg.invite_to_club(7, 7, "svelaya")) == "invited"
    assert call.await_args_list[1].args[1]["member_limit"] == 1
    text = send.await_args.kwargs["text"]
    assert "https://t.me/+one" in text and cg.NEWS_CHANNEL_URL in text


def test_existing_member_gets_only_channel():
    call = AsyncMock(return_value={"ok": True, "result": {"status": "member"}})
    send = AsyncMock(return_value={"ok": True})
    with patch.object(cg, "_call_telegram", call), patch.object(cg, "send_telegram_text", send), patch.object(
        cg, "current_bot_binding", _binding
    ):
        assert asyncio.run(cg.invite_to_club(7, 7, "petrovna")) == "member"
    assert call.await_count == 1
    assert cg.NEWS_CHANNEL_URL in send.await_args.kwargs["text"]
