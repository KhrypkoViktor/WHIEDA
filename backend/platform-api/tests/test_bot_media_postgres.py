"""Хранилище медиа бота на настоящей схеме PostgreSQL (V45, 30.10.2026).

Проверяет то, чего не видят юнит-тесты: миграция (повторный прогон), ограничения таблицы (ключ, вид, уникальность,
«нужен хотя бы один идентификатор»), RLS по арендатору, запись и замена файла (токен Max сбрасывается), токен Max
с защитой от гонки и замены, отправка по ключу на настоящих строках и команды владельца от разбора до строки.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import SQL_DIR, temporary_database

OWNER = 7001
V45 = SQL_DIR / "platform_bot_media_v45.sql"


@pytest.mark.integration
def test_bot_media_table_constraints_and_migration_on_live_schema():
    with temporary_database("whieda_bot_media") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, twice=True)
            # V45 безопасна при повторном прогоне: ни ошибки, ни второй политики, ни второго ограничения.
            conn.execute(V45.read_text(encoding="utf-8"))
            conn.execute(V45.read_text(encoding="utf-8"))

            columns = {
                row[0]: (row[1], row[2])
                for row in conn.execute(
                    "select column_name, data_type, is_nullable from information_schema.columns where table_name = 'bot_media'"
                ).fetchall()
            }
            assert columns == {
                "media_id": ("bigint", "NO"),
                "tenant_id": ("text", "NO"),
                "key": ("text", "NO"),
                "kind": ("text", "NO"),
                "telegram_file_id": ("text", "YES"),
                "max_token": ("text", "YES"),
                "caption": ("text", "YES"),
                "source_note": ("text", "YES"),
                "created_by": ("text", "YES"),
                "created_at": ("timestamp with time zone", "NO"),
                "updated_at": ("timestamp with time zone", "NO"),
            }
            assert conn.execute("select relrowsecurity from pg_class where relname = 'bot_media'").fetchone() == (True,)
            assert conn.execute("select count(*) from pg_policies where tablename = 'bot_media'").fetchone() == (1,)
            assert conn.execute(
                "select count(*) from pg_constraint where conrelid = 'bot_media'::regclass and contype = 'u'"
            ).fetchone() == (1,)

            conn.execute(
                "insert into bot_media (tenant_id, key, kind, telegram_file_id) values ('whieda', 'system_tour', 'video', 'F1')"
            )
            row = conn.execute(
                "select max_token, caption, source_note, created_by, created_at is not null, updated_at is not null from bot_media"
            ).fetchone()
            assert row == (None, None, None, None, True, True)

            # Ключ: латиница, цифры, «_» и «-», 1–64 знака, строчные.
            for good in ("a", "a-b_c9", "k" * 64, "0"):
                conn.execute("insert into bot_media (tenant_id, key, kind, telegram_file_id) values ('whieda', %s, 'photo', 'F')", (good,))
            for bad in ("", "K", "System_Tour", "a b", "a.b", "тур", "a/b", "k" * 65, "a\n"):
                with pytest.raises(psycopg.errors.CheckViolation):
                    conn.execute("insert into bot_media (tenant_id, key, kind, telegram_file_id) values ('whieda', %s, 'photo', 'F')", (bad,))
            # Вид — шесть значений.
            for kind in ("video", "voice", "video_note", "photo", "audio", "document"):
                conn.execute(
                    "insert into bot_media (tenant_id, key, kind, telegram_file_id) values ('whieda', %s, %s, 'F')", (f"k_{kind}", kind)
                )
            for kind in ("gif", "Video", "", "sticker"):
                with pytest.raises(psycopg.errors.CheckViolation):
                    conn.execute("insert into bot_media (tenant_id, key, kind, telegram_file_id) values ('whieda', 'bad_kind', %s, 'F')", (kind,))
            with pytest.raises(psycopg.errors.NotNullViolation):
                conn.execute("insert into bot_media (tenant_id, key, kind, telegram_file_id) values ('whieda', 'x', null, 'F')")
            with pytest.raises(psycopg.errors.NotNullViolation):
                conn.execute("insert into bot_media (tenant_id, key, kind, telegram_file_id) values (null, 'x', 'video', 'F')")
            # Уникальность в арендаторе; у другого арендатора тот же ключ можно.
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute("insert into bot_media (tenant_id, key, kind, telegram_file_id) values ('whieda', 'system_tour', 'video', 'F2')")
            conn.execute("insert into bot_media (tenant_id, key, kind, telegram_file_id) values ('other', 'system_tour', 'video', 'F3')")
            # Нужен хотя бы один идентификатор файла.
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("insert into bot_media (tenant_id, key, kind) values ('whieda', 'no_file', 'video')")
            conn.execute("insert into bot_media (tenant_id, key, kind, max_token) values ('whieda', 'max_only', 'video', 'T')")


@pytest.mark.integration
def test_bot_media_service_commands_sending_and_rls_on_live_schema(monkeypatch):
    with temporary_database("whieda_bot_media") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, twice=True)
            db.grant_api_role(conn)

        monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
        monkeypatch.delenv("PLATFORM_SUPER_ADMIN_TELEGRAM_IDS", raising=False)

        async def proof() -> None:
            from app.bot_media import service
            from app.db import fetch_one, tenant_connection
            from app.telegram import bot_media_admin
            from app.telegram.update_parser import parse_telegram_message

            tenant = SimpleNamespace(tenant_id="whieda")
            said: list[str] = []

            async def deliver(chat_id: int, text: str) -> None:
                said.append(text)

            async def upload(caption: str, attachment: dict | None = None, user_id: int = OWNER) -> dict | None:
                message = {"message_id": 11, "chat": {"id": user_id, "type": "private"}, "from": {"id": user_id}}
                message.update({"caption": caption, **attachment} if attachment else {"text": caption})
                msg = parse_telegram_message({"update_id": 1, "message": message})
                return await bot_media_admin.try_handle_bot_media_message(tenant, msg, trace_id="p")

            async def one(query: str, params: tuple = (), tenant_id: str = "whieda") -> dict | None:
                async with tenant_connection(tenant_id) as conn:
                    return await fetch_one(conn, query, params)

            with patch.object(bot_media_admin, "_deliver", deliver):
                # 1. Пусто.
                assert (await upload("медиа"))["status"] == "listed" and said[-1] == bot_media_admin.EMPTY_LIST

                # 2. Загрузка видео: строка в базе — вид, file_id, автор, откуда.
                assert (await upload("медиа system_tour", {"video": {"file_id": "VID-1"}}))["status"] == "saved"
                assert said[-1] == "Сохранено: system_tour (video)"
                row = await one(
                    "select kind, telegram_file_id, max_token, caption, source_note, created_by from bot_media where key = 'system_tour'"
                )
                assert row == {
                    "kind": "video", "telegram_file_id": "VID-1", "max_token": None, "caption": None,
                    "source_note": f"bot=unknown telegram:{OWNER}:11", "created_by": f"telegram:{OWNER}",
                }

                # 3. Остальные виды вложений: кружок, голосовое, фото (самый крупный размер), файл.
                for key, attachment, kind, file_id in (
                    ("note", {"video_note": {"file_id": "N-1"}}, "video_note", "N-1"),
                    ("voice", {"voice": {"file_id": "VO-1"}}, "voice", "VO-1"),
                    ("pic", {"photo": [{"file_id": "P-s"}, {"file_id": "P-b"}]}, "photo", "P-b"),
                    ("doc", {"document": {"file_id": "D-1"}}, "document", "D-1"),
                ):
                    assert (await upload(f"медиа {key}", attachment))["kind"] == kind
                    assert await one("select kind, telegram_file_id from bot_media where key = %s", (key,)) == {"kind": kind, "telegram_file_id": file_id}

                # 4. Токен Max: пишется один раз, только для того же файла; дата строки не двигается.
                before = await one("select created_at, updated_at from bot_media where key = 'system_tour'")
                async with tenant_connection("whieda") as conn:
                    assert await service.set_max_token(conn, "whieda", "system_tour", "TOK-1", telegram_file_id="OTHER") is False
                    assert await service.set_max_token(conn, "whieda", "system_tour", "TOK-1", telegram_file_id="VID-1") is True
                    assert await service.set_max_token(conn, "whieda", "system_tour", "TOK-2", telegram_file_id="VID-1") is False
                after = await one("select max_token, updated_at from bot_media where key = 'system_tour'")
                assert after["max_token"] == "TOK-1" and after["updated_at"] == before["updated_at"]

                # 5. Замена файла тем же ключом: новый file_id и вид, токен Max сброшен, подпись по умолчанию осталась, дата двинулась.
                async with tenant_connection("whieda") as conn:
                    await conn.execute("update bot_media set caption = 'Подпись по умолчанию' where key = 'system_tour'")
                assert (await upload("медиа SYSTEM_TOUR", {"video_note": {"file_id": "NEW-1"}}))["status"] == "replaced"
                assert said[-1] == "Сохранено: system_tour (video_note)\nПрежний файл заменён."
                replaced = await one(
                    "select kind, telegram_file_id, max_token, caption, created_at, updated_at from bot_media where key = 'system_tour'"
                )
                assert (replaced["kind"], replaced["telegram_file_id"], replaced["max_token"], replaced["caption"]) == (
                    "video_note", "NEW-1", None, "Подпись по умолчанию",
                )
                assert replaced["created_at"] == before["created_at"] and replaced["updated_at"] > before["updated_at"]
                assert (await one("select count(*) as n from bot_media"))["n"] == 5

                # 6. Список: по ключам, с отметкой про Max.
                await upload("медиа")
                lines = said[-1].splitlines()
                assert [line.split(" · ")[0] for line in lines[1:]] == ["doc", "note", "pic", "system_tour", "voice"]
                assert all("нет, загрузится при первой отправке" in line for line in lines[1:])

                # 7. Отправка по ключу на настоящей строке: Telegram — по file_id с подписью строки.
                binding = SimpleNamespace(bot_token="tg-token")
                tg_media = AsyncMock(return_value={"ok": True, "message_id": 1})
                tg_text = AsyncMock(return_value={"ok": True})
                with patch.object(service, "send_telegram_media", tg_media), patch.object(service, "send_telegram_text", tg_text):
                    sent = await service.send_media(None, "whieda", "pic", channel="telegram", chat_id=42, binding=binding)
                    assert sent["ok"] and sent["kind"] == "photo"
                    tg_media.assert_awaited_once_with(42, "photo", "P-b", bot_token="tg-token", caption="", reply_markup=None)
                    tg_media.reset_mock()
                    note = await service.send_media(None, "whieda", "system_tour", channel="telegram", chat_id=42, binding=binding, reply_markup={"inline_keyboard": [[]]})
                    # у кружка подписи нет: подпись строки (она есть) идёт отдельным сообщением следом
                    assert note["ok"] and note["text_sent"] is True
                    assert tg_media.await_args.args[:3] == (42, "video_note", "NEW-1") and tg_media.await_args.kwargs["caption"] is None
                    assert tg_text.await_args.kwargs["text"] == "Подпись по умолчанию"

                # 8. Max: ленивая загрузка на настоящей строке — токен ложится в таблицу, второй раз скачивания нет.
                download = AsyncMock(return_value=(b"BODY", "file_5.mp4"))
                upload_max = AsyncMock(return_value={"type": "video", "payload": {"token": "MAX-TOK"}})
                max_send = AsyncMock(return_value={"ok": True, "mid": "m1"})
                with patch.object(service, "download_telegram_file", download), patch.object(service, "upload_max_media", upload_max), \
                        patch.object(service, "send_max_attachment", max_send):
                    first = await service.send_media(None, "whieda", "system_tour", channel="max", user_id=5, binding=binding)
                    second = await service.send_media(None, "whieda", "system_tour", channel="max", user_id=6, binding=binding)
                assert first["ok"] and second["ok"]
                download.assert_awaited_once_with("NEW-1", bot_token="tg-token")
                upload_max.assert_awaited_once_with("video", b"BODY", filename="file_5.mp4")
                assert [c.kwargs["token"] for c in max_send.await_args_list] == ["MAX-TOK", "MAX-TOK"]
                assert (await one("select max_token from bot_media where key = 'system_tour'"))["max_token"] == "MAX-TOK"
                # то же внутри чужой открытой транзакции вызывающего: подключение переиспользуется
                async with tenant_connection("whieda") as conn:
                    with patch.object(service, "send_max_attachment", max_send):
                        again = await service.send_media(conn, "whieda", "system_tour", channel="max", user_id=7)
                assert again["ok"]
                missing = await service.send_media(None, "whieda", "nope", channel="max", user_id=7)
                assert missing == {"ok": False, "status": "missing", "key": "nope"}

                # 9. Удаление.
                assert (await upload("медиа удалить voice"))["status"] == "deleted" and said[-1] == "Удалено: voice"
                assert (await upload("медиа удалить voice"))["status"] == "not_found"
                assert (await one("select count(*) as n from bot_media"))["n"] == 4

                # 10. Не владелец — тишина, строк не прибавилось.
                assert await upload("медиа hack", {"video": {"file_id": "X"}}, user_id=1) is None
                assert (await one("select count(*) as n from bot_media"))["n"] == 4

            # 11. Арендаторы не смешиваются: чужой не видит строки whieda и не пишет за них.
            async with tenant_connection("other") as conn:
                assert await service.list_media(conn, "other") == []
                assert await service.list_media(conn, "whieda") == []          # RLS режет и при чужом параметре
                assert await service.get_media(conn, "whieda", "system_tour") is None
                assert await service.delete_media(conn, "whieda", "system_tour") is False
                mine, replaced_flag = await service.save_media(conn, "other", "system_tour", "video", "OTHER-F")
                assert mine["tenant_id"] == "other" and replaced_flag is False   # тот же ключ у другого арендатора — своя строка
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with tenant_connection("other") as conn:
                    await fetch_one(
                        conn,
                        "insert into bot_media (tenant_id, key, kind, telegram_file_id) values ('whieda', 'x', 'video', 'F') returning media_id",
                    )
            assert (await one("select telegram_file_id from bot_media where key = 'system_tour'"))["telegram_file_id"] == "NEW-1"

            # 12. Сервис отказывает до базы: плохой ключ, вид, пустой file_id.
            async with tenant_connection("whieda") as conn:
                for args in (("Плохой", "video", "F"), ("k", "gif", "F"), ("k", "video", " ")):
                    with pytest.raises(ValueError):
                        await service.save_media(conn, "whieda", *args)

        try:
            db.run_with_app(proof)
        finally:
            from app.settings import get_settings

            get_settings.cache_clear()

        # Подключение без арендатора (set_config не вызван) не видит ни одной строки.
        with psycopg.connect(db.api_dsn, autocommit=True) as conn:
            assert conn.execute("select count(*) from bot_media").fetchone()[0] == 0
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            assert conn.execute("select count(*) from bot_media").fetchone()[0] == 5


def test_migration_file_has_no_dollar_sign_and_follows_the_sibling_layout():
    text = V45.read_text(encoding="utf-8")
    assert "$" not in text, "боевые правки идут из контейнера Core: знака доллара в файле быть не должно"
    for needle in (
        "create table if not exists bot_media",
        "set local lock_timeout = '5s'",
        "alter table bot_media enable row level security",
        "drop policy if exists bot_media_tenant_isolation on bot_media",
        "platform_current_tenant_id()",
        "unique (tenant_id, key)",
    ):
        assert needle in text, needle
    assert text.lstrip().startswith("--") and "begin;" in text and text.rstrip().endswith("commit;")
