"""Пост в канал через одобрение владельца на настоящей базе (11.10.2026).

Черновик в личке бота → «Опубликовать» → копия в Telegram-канал и тот же пост в
чаты Max; двойное нажатие не публикует дважды; «Отменить» и чужое нажатие — без поста.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import temporary_database

OWNER = 688931415
CHANNEL = -1002885760214
MAX_CHANNEL = -79967242454401
MAX_COURSE = -79965253305729


def _callback(data: str, message_id: int, sender: int = OWNER) -> dict:
    return {"id": f"cb{message_id}{data}", "data": data, "from": {"id": sender},
            "message": {"message_id": message_id, "chat": {"id": OWNER, "type": "private"},
                        "text": "📢 WWC теперь и в Max", "entities": [{"type": "bold", "offset": 3, "length": 3}]}}


@pytest.mark.integration
def test_owner_approves_a_draft_and_it_reaches_telegram_and_max_once(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    with temporary_database("whieda_channel_draft") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(
                "insert into max_chats (tenant_id, chat_id, title, crosspost) values ('whieda', %s, 'канал', true), ('whieda', %s, 'поток', true)",
                (MAX_CHANNEL, MAX_COURSE),
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.max import crosspost
            from app.telegram import channel_posts

            calls: list[tuple[str, dict]] = []

            async def fake_tg(method, bot_token, payload):
                calls.append((method, payload))
                return {"ok": True, "result": {"message_id": 321} if method == "copyMessage" else {}}

            send_max = AsyncMock(return_value={"ok": True, "mid": "m1"})
            with patch.object(channel_posts, "_tg", side_effect=fake_tg), patch.object(crosspost, "send_max_message", send_max):
                # Чужой не может опубликовать.
                stranger = await channel_posts.handle_draft_callback(_callback(channel_posts.APPROVE, 10, sender=5), tenant_id="whieda", bot_token="t")
                assert stranger["status"] == "not_owner" and [c[0] for c in calls] == ["answerCallbackQuery"]

                # «Опубликовать»: копия черновика в канал, кнопки сняты, тот же пост — в Max-канал (не в группу потока).
                calls.clear()
                done = await channel_posts.handle_draft_callback(_callback(channel_posts.APPROVE, 10), tenant_id="whieda", bot_token="t")
                assert done == {"ok": True, "status": "published", "message_id": 321}
                methods = [c[0] for c in calls]
                assert methods == ["editMessageReplyMarkup", "copyMessage", "answerCallbackQuery", "sendMessage"]
                assert calls[1][1] == {"chat_id": CHANNEL, "from_chat_id": OWNER, "message_id": 10}
                assert send_max.await_count == 1
                assert send_max.await_args.kwargs["chat_id"] == MAX_CHANNEL
                assert send_max.await_args.kwargs["text"] == "📢 <b>WWC</b> теперь и в Max"
                assert "https://t.me/Whieda_world_club/321" in calls[-1][1]["text"] and "Max: 1 из 1" in calls[-1][1]["text"]

                # Второе нажатие по тому же черновику — ничего не публикует.
                calls.clear()
                again = await channel_posts.handle_draft_callback(_callback(channel_posts.APPROVE, 10), tenant_id="whieda", bot_token="t")
                assert again["status"] == "already_done" and [c[0] for c in calls] == ["answerCallbackQuery"]
                assert send_max.await_count == 1

                # «Отменить» — кнопки сняты, в канал ничего.
                calls.clear()
                cancelled = await channel_posts.handle_draft_callback(_callback(channel_posts.CANCEL, 11), tenant_id="whieda", bot_token="t")
                assert cancelled["status"] == "cancelled" and "copyMessage" not in [c[0] for c in calls]

        db.run_with_app(proof)
