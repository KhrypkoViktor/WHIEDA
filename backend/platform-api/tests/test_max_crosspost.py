"""Посты Telegram-канала → Max (V24, 08.10.2026): разметка, события чатов, вход вебхука."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.max.crosspost import entities_to_html, post_link, post_payload
from app.max.update_parser import parse_max_update


def test_bold_italic_link_and_custom_emoji_become_max_html():
    text = "🔥 Эфир сегодня в 19:00 — регистрация"
    # 🔥 — свой смайлик владельца (custom_emoji, 2 единицы UTF-16): в Max остаётся обычный 🔥.
    entities = [
        {"type": "custom_emoji", "offset": 0, "length": 2, "custom_emoji_id": "5"},
        {"type": "bold", "offset": 3, "length": 5},
        {"type": "italic", "offset": 18, "length": 5},
        {"type": "text_link", "offset": 26, "length": 11, "url": "https://wwc.best/efir?a=1&b=2"},
    ]
    assert entities_to_html(text, entities) == (
        '🔥 <b>Эфир </b>сегодня в <i>19:00</i> — <a href="https://wwc.best/efir?a=1&amp;b=2">регистрация</a>'
    )


def test_nested_entities_and_html_in_text_are_safe():
    text = "Скидка <50%> для клуба"
    entities = [{"type": "bold", "offset": 0, "length": 22}, {"type": "italic", "offset": 7, "length": 5}]
    assert entities_to_html(text, entities) == "<b>Скидка <i>&lt;50%&gt;</i> для клуба</b>"
    assert entities_to_html("", entities) == ""
    assert entities_to_html("без разметки", [{"type": "spoiler", "offset": 0, "length": 3}]) == "без разметки"


def test_post_payload_takes_the_biggest_photo_and_marks_files_unsupported():
    photo = {"message_id": 7, "chat": {"id": -100, "username": "Whieda_world_club"}, "caption": "Новинка",
             "caption_entities": [{"type": "bold", "offset": 0, "length": 7}],
             "photo": [{"file_id": "small", "file_size": 10}, {"file_id": "big", "file_size": 900}]}
    assert post_payload(photo) == {"html": "<b>Новинка</b>", "media": {"kind": "image", "file_id": "big", "size": 900, "filename": None}}
    assert post_link(photo) == "https://t.me/Whieda_world_club/7"
    assert post_payload({"text": "x", "document": {"file_id": "d"}})["media"] == {"kind": "unsupported"}
    # Мост группы потока (V27) везёт файлы и голосовые вложением, канал — нет.
    assert post_payload({"document": {"file_id": "d", "file_size": 3, "file_name": "a.pdf"}}, rich=True)["media"] == {
        "kind": "file", "file_id": "d", "size": 3, "filename": "a.pdf"}
    assert post_payload({"voice": {"file_id": "v"}}, rich=True)["media"]["kind"] == "audio"
    assert post_payload({"sticker": {"file_id": "s"}}, rich=True)["media"] == {"kind": "unsupported"}
    assert post_payload({"text": "просто текст"})["media"] is None


def test_bot_added_and_removed_carry_the_chat():
    added = parse_max_update({"update_type": "bot_added", "chat_id": -79821679854977, "is_channel": False,
                              "user": {"user_id": 482284673, "first_name": "Виктор"}})
    assert (added.kind, added.chat_id, added.user_id, added.text) == ("chat_added", -79821679854977, 482284673, "chat")
    removed = parse_max_update({"update_type": "bot_removed", "chat_id": -5, "is_channel": True, "user": {"user_id": 1}})
    assert (removed.kind, removed.text) == ("chat_removed", "channel")
    assert parse_max_update({"update_type": "bot_added"}) is None


@pytest.mark.asyncio
async def test_webhook_sends_only_our_channel_posts_to_max(monkeypatch: pytest.MonkeyPatch):
    from fastapi import BackgroundTasks

    from app.telegram import routes

    binding = type("B", (), {"tenant": type("T", (), {"tenant_id": "whieda"})(), "bot_token": "t", "binding_id": "b"})()
    request = AsyncMock()
    request.headers = {"x-telegram-bot-api-secret-token": "s"}
    with patch.object(routes, "resolve_bot_binding_context", AsyncMock(return_value=binding)), \
         patch.object(routes, "verify_webhook_secret", lambda *a: None), \
         patch.object(routes, "get_trace_id", lambda r: "trace"), \
         patch.object(routes, "_process_telegram_update", AsyncMock()) as regular:
        for update, expected in [
            ({"update_id": 1, "channel_post": {"message_id": 3, "chat": {"id": -1002885760214}, "text": "пост"}}, 1),
            ({"update_id": 2, "channel_post": {"message_id": 4, "chat": {"id": -1009}, "text": "чужой канал"}}, 0),
            ({"update_id": 3, "edited_channel_post": {"message_id": 3, "chat": {"id": -1002885760214}, "text": "правка"}}, 0),
        ]:
            request.json = AsyncMock(return_value=update)
            tasks = BackgroundTasks()
            assert await routes.telegram_webhook("whieda-advisor-bot", request, tasks) == {"ok": True}
            assert [t.func.__name__ for t in tasks.tasks] == ["_crosspost_to_max"] * expected
        regular.assert_not_awaited()
