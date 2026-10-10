"""Посты Telegram-канала → чаты Max на настоящей базе (V24, 08.10.2026)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import temporary_database

CHANNEL = -1002885760214
GROUP = -79821679854977


def _post(message_id: int, **body) -> dict:
    return {"message_id": message_id, "chat": {"id": CHANNEL, "type": "channel", "username": "Whieda_world_club"}, **body}


@pytest.mark.integration
def test_channel_posts_reach_max_once_and_albums_go_as_one_message(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", "688931415")
    with temporary_database("whieda_max_crosspost") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, tenant_connection
            from app.max import crosspost
            from app.max.processor import process_max_event
            from app.max.update_parser import parse_max_update
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})
            send = AsyncMock(return_value={"ok": True})
            upload = AsyncMock(side_effect=lambda kind, body, **kw: {"type": kind, "payload": {"token": f"tok-{body.decode()}"}})
            download = AsyncMock(side_effect=lambda token, file_id: file_id.encode())
            owner_note = AsyncMock(return_value={"ok": True})
            binding = type("B", (), {"bot_token": "tg"})()
            with patch.object(crosspost, "send_max_message", send), patch.object(crosspost, "upload_max_media", upload), \
                 patch.object(crosspost, "download_telegram_file", download), \
                 patch("app.max.client.get_chat", AsyncMock(return_value={"title": "WWC Official 📣", "type": "chat"})), \
                 patch("app.telegram.delivery.send_telegram_text", owner_note), \
                 patch("app.telegram.bindings.resolve_bot_binding_context", AsyncMock(return_value=binding)):
                # Чатов Max ещё нет — пост записан, но отправлять некуда.
                none = await crosspost.crosspost_channel_post("whieda", _post(1, text="первый"), bot_token="tg")
                assert none["status"] == "no_max_chats" and not send.await_args_list

                # Бота добавили в группу Max: чат запомнен, владельцу — строка в Telegram.
                event = parse_max_update({"update_type": "bot_added", "chat_id": GROUP, "user": {"user_id": 482284673}})
                added = await process_max_event(tenant, event, "t")
                assert added["added"] is True
                assert "WWC Official 📣" in owner_note.await_args.kwargs["text"]
                assert await crosspost.target_chats("whieda") == [GROUP]

                # Текстовый пост с разметкой — в группу Max один раз, даже если Telegram прислал его дважды.
                post = _post(2, text="Эфир сегодня", entities=[{"type": "bold", "offset": 0, "length": 4}])
                first = await crosspost.crosspost_channel_post("whieda", post, bot_token="tg")
                again = await crosspost.crosspost_channel_post("whieda", post, bot_token="tg")
                assert first["status"] == "sent" and again["status"] == "duplicate"
                assert send.await_count == 1
                assert send.await_args.kwargs == {"chat_id": GROUP, "text": "<b>Эфир</b> сегодня", "attachments": [], "html_format": True}

                # Альбом из двух фото — одно сообщение с двумя картинками и подписью первого.
                send.reset_mock()
                album = [
                    _post(10, media_group_id="g1", caption="Новинки октября", photo=[{"file_id": "p1", "file_size": 5}]),
                    _post(11, media_group_id="g1", photo=[{"file_id": "p2", "file_size": 5}]),
                ]
                results = await asyncio.gather(*(crosspost.crosspost_channel_post("whieda", p, bot_token="tg", wait=0.3) for p in album))
                assert sorted(r["status"] for r in results) == ["claimed_elsewhere", "sent"]
                assert send.await_count == 1
                kwargs = send.await_args.kwargs
                assert kwargs["text"] == "Новинки октября"
                assert kwargs["attachments"] == [{"type": "image", "payload": {"token": "tok-p1"}}, {"type": "image", "payload": {"token": "tok-p2"}}]

                # Видео больше 20 МБ Telegram боту не отдаёт: текст и ссылка на пост в канале.
                send.reset_mock()
                await crosspost.crosspost_channel_post("whieda", _post(12, caption="Запись эфира", video={"file_id": "v", "file_size": 90_000_000}), bot_token="tg")
                assert send.await_args.kwargs["attachments"] == []
                assert 'href="https://t.me/Whieda_world_club/12"' in send.await_args.kwargs["text"]

                # Бота убрали — туда больше не шлём.
                await process_max_event(tenant, parse_max_update({"update_type": "bot_removed", "chat_id": GROUP, "user": {"user_id": 1}}), "t")
                assert await crosspost.target_chats("whieda") == []

            async with tenant_connection("whieda") as conn:
                rows = await fetch_all(conn, "select source_message_id, status from channel_crossposts order by source_message_id")
            assert [(int(r["source_message_id"]), r["status"]) for r in rows] == [
                (1, "skipped"), (2, "sent"), (10, "sent"), (11, "sent"), (12, "sent"),
            ]

        db.run_with_app(proof)
