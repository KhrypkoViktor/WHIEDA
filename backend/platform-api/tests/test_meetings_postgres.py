"""Встречи WWC на настоящей схеме PostgreSQL (V43, 30.10.2026).

Проверяет то, чего не видят юнит-тесты: миграция (повторный прогон), ограничения таблицы, индекс, RLS
по арендатору, время в UTC, запросы сервиса (ближайшие, дубль, отмена, «прошла», ссылка), команды владельца
от разбора до строки в базе и публичные колонки без ссылки входа.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import psycopg
import pytest

from tests.postgres_testkit import SQL_DIR, temporary_database

UTC = dt.timezone.utc
OWNER = 7001
# Пятница 30.10.2026, 15:00 по Москве.
NOW = dt.datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
V43 = SQL_DIR / "platform_meetings_v43.sql"


@pytest.mark.integration
def test_meetings_table_constraints_and_migration_on_live_schema():
    with temporary_database("whieda_meetings") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, twice=True)
            # V43 безопасна при повторном прогоне: ни ошибки, ни второй политики, ни второго индекса.
            conn.execute(V43.read_text(encoding="utf-8"))
            conn.execute(V43.read_text(encoding="utf-8"))

            columns = {
                row[0]: (row[1], row[2], row[3])
                for row in conn.execute(
                    "select column_name, data_type, is_nullable, column_default from information_schema.columns "
                    "where table_name = 'meetings'"
                ).fetchall()
            }
            assert {name: (kind, nullable) for name, (kind, nullable, _) in columns.items()} == {
                "meeting_id": ("bigint", "NO"),
                "tenant_id": ("text", "NO"),
                "kind": ("text", "NO"),
                "title": ("text", "YES"),
                "starts_at": ("timestamp with time zone", "NO"),
                "duration_min": ("integer", "NO"),
                "join_url": ("text", "YES"),
                "hosts": ("ARRAY", "NO"),
                "status": ("text", "NO"),
                "recording_url": ("text", "YES"),
                "notes": ("text", "YES"),
                "created_by": ("text", "YES"),
                "created_at": ("timestamp with time zone", "NO"),
                "updated_at": ("timestamp with time zone", "NO"),
            }
            assert conn.execute(
                "select indexdef from pg_indexes where tablename = 'meetings' and indexname = 'idx_meetings_tenant_starts'"
            ).fetchone()[0].endswith("(tenant_id, starts_at)")
            assert conn.execute("select relrowsecurity from pg_class where relname = 'meetings'").fetchone() == (True,)
            assert conn.execute("select count(*) from pg_policies where tablename = 'meetings'").fetchone() == (1,)

            # Значения по умолчанию.
            conn.execute(
                "insert into meetings (tenant_id, kind, starts_at) values ('whieda', 'live', timestamptz '2026-11-05 19:30+03')"
            )
            row = conn.execute(
                "select duration_min, hosts, status, join_url, created_at is not null, updated_at is not null, "
                "starts_at at time zone 'UTC' from meetings"
            ).fetchone()
            assert row == (60, [], "planned", None, True, True, dt.datetime(2026, 11, 5, 16, 30))

            for bad in (
                "values ('whieda', 'webinar', now())",                       # вид только live / consult
                "values ('whieda', null, now())",
                "values (null, 'live', now())",
                "values ('whieda', 'live', null)",
            ):
                with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.NotNullViolation)):
                    conn.execute(f"insert into meetings (tenant_id, kind, starts_at) {bad}")
            for status in ("planned2", "Planned", "", "finished"):
                with pytest.raises(psycopg.errors.CheckViolation):
                    conn.execute(
                        "insert into meetings (tenant_id, kind, starts_at, status) values ('whieda', 'live', now(), %s)",
                        (status,),
                    )
            for minutes in (0, -1, 1441):
                with pytest.raises(psycopg.errors.CheckViolation):
                    conn.execute(
                        "insert into meetings (tenant_id, kind, starts_at, duration_min) values ('whieda', 'live', now(), %s)",
                        (minutes,),
                    )
            for status in ("planned", "done", "cancelled"):
                conn.execute(
                    "insert into meetings (tenant_id, kind, starts_at, status) values ('whieda', 'consult', now(), %s)",
                    (status,),
                )
            assert conn.execute("select count(*) from meetings").fetchone() == (4,)


@pytest.mark.integration
def test_meetings_service_commands_rls_and_public_view_on_live_schema(monkeypatch):
    with temporary_database("whieda_meetings") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, twice=True)
            db.grant_api_role(conn)

        monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
        monkeypatch.delenv("PLATFORM_SUPER_ADMIN_TELEGRAM_IDS", raising=False)

        async def proof() -> None:
            from types import SimpleNamespace

            from app.db import fetch_all, fetch_one, tenant_connection
            from app.meetings import service
            from app.meetings.service import MeetingError
            from app.telegram import meetings_admin
            from app.telegram.update_parser import TelegramMessage

            tenant = SimpleNamespace(tenant_id="whieda")
            sent: list[str] = []

            async def deliver(chat_id: int, text: str) -> None:
                sent.append(text)

            async def say(text: str, user_id: int = OWNER) -> dict | None:
                msg = TelegramMessage(
                    chat_id=user_id, user_id=user_id, message_id=1, text=text, chat_type="private", file_id=None, raw={}
                )
                return await meetings_admin.try_handle_meetings_admin_message(tenant, msg, trace_id="p", now=NOW)

            async def one(query: str, params: tuple = (), tenant_id: str = "whieda") -> dict | None:
                async with tenant_connection(tenant_id) as conn:
                    return await fetch_one(conn, query, params)

            async def many(query: str, params: tuple = (), tenant_id: str = "whieda") -> list[dict]:
                async with tenant_connection(tenant_id) as conn:
                    return list(await fetch_all(conn, query, params))

            with patch.object(meetings_admin, "_deliver", deliver):
                # 1. Пусто.
                assert (await say("встречи"))["status"] == "listed"
                assert sent[-1] == "Ближайших встреч нет. Создать: встреча 05.11 19:30 или встречи создать 4"

                # 2. «встреча 05.11 19:30» — строка в базе: UTC, живая, название по умолчанию, автор.
                assert (await say("встреча 05.11 19:30"))["meeting_id"] == 1
                row = await one(
                    "select kind, title, status, duration_min, join_url, hosts, created_by, "
                    "starts_at at time zone 'UTC' as utc from meetings where meeting_id = 1"
                )
                assert row == {
                    "kind": "live", "title": "Живая встреча", "status": "planned", "duration_min": 60,
                    "join_url": None, "hosts": [], "created_by": f"telegram:{OWNER}",
                    "utc": dt.datetime(2026, 11, 5, 16, 30),
                }
                assert sent[-1].startswith("Создана встреча #1 · чт 05.11 19:30 МСК · Живая встреча · ссылка нет")

                # 3. Со ссылкой и названием; кавычки и SQL в названии — просто текст.
                assert (await say("встреча 10.11 19:30 https://zoom.us/j/2 Разбор: О'Нил \"кавычки\"; drop table meetings;--"))[
                    "meeting_id"
                ] == 2
                assert await one("select title, join_url from meetings where meeting_id = 2") == {
                    "title": "Разбор: О'Нил \"кавычки\"; drop table meetings;--", "join_url": "https://zoom.us/j/2",
                }

                # 4. Тот же момент второй раз — не дубль.
                assert (await say("встреча 05.11 19:30 Ещё раз"))["status"] == "duplicate"
                assert (await one("select count(*) as n from meetings"))["n"] == 2

                # 5. Слоты: 03.11 и 12.11 новые, 05.11 и 10.11 уже есть (и ручная, и с названием).
                result = await say("встречи создать 4")
                assert result["created"] == [3, 4]
                assert sent[-1] == (
                    "Создано 2: #3 вт 03.11 19:30 · #4 чт 12.11 19:30 (МСК).\n"
                    "Уже были, пропустил: #1 чт 05.11 19:30 · #2 вт 10.11 19:30."
                )
                again = await say("встречи создать 4")
                assert again["status"] == "slots_exist" and (await one("select count(*) as n from meetings"))["n"] == 4

                # 6. Список по времени с отметкой о ссылке.
                await say("встречи")
                assert sent[-1].splitlines() == [
                    "Ближайшие встречи:",
                    "#3 · вт 03.11 19:30 МСК · Живая встреча · ссылка нет",
                    "#1 · чт 05.11 19:30 МСК · Живая встреча · ссылка нет",
                    "#2 · вт 10.11 19:30 МСК · Разбор: О'Нил \"кавычки\"; drop table meetings;-- · ссылка есть",
                    "#4 · чт 12.11 19:30 МСК · Живая встреча · ссылка нет",
                ]

                # 7. Ссылка: задать и заменить; updated_at двигается.
                before = await one("select created_at, updated_at from meetings where meeting_id = 1")
                assert (await say("ссылка встречи 1 https://zoom.us/j/111"))["status"] == "url_set"
                assert (await say("ссылка встречи 1 https://zoom.us/j/222"))["status"] == "url_set"
                after = await one("select join_url, updated_at from meetings where meeting_id = 1")
                assert after["join_url"] == "https://zoom.us/j/222" and after["updated_at"] > before["updated_at"]
                assert all("zoom.us" not in text for text in sent)

                # 8. Отмена: пропала из списка, освободила слот для «встречи создать».
                assert (await say("отменить встречу 3"))["status"] == "cancelled"
                assert (await say("отменить встречу 3"))["status"] == "bad_status"
                await say("встречи")
                assert "#3" not in sent[-1]
                recreated = await say("встречи создать 1")
                assert recreated["created"] == [5]
                assert (await one("select status from meetings where meeting_id = 3"))["status"] == "cancelled"
                assert (await one("select status from meetings where meeting_id = 5"))["status"] == "planned"

                # 9. «Прошла»: пропала из списка; ссылку у прошедшей не меняем.
                assert (await say("встреча прошла 4"))["status"] == "done"
                assert (await say("ссылка встречи 4 https://zoom.us/j/9"))["status"] == "bad_status"
                assert (await one("select join_url from meetings where meeting_id = 4"))["join_url"] is None
                await say("встречи")
                assert "#4" not in sent[-1]

                # 10. Номера, которых нет, и чужие; большой номер не ломает запрос.
                for text in ("отменить встречу 999", "встреча прошла 999999999", "ссылка встречи 888 https://zoom.us/j/1"):
                    assert (await say(text))["status"] == "not_found", text

                # 11. Не владелец — тишина, строк не прибавилось.
                count = (await one("select count(*) as n from meetings"))["n"]
                assert await say("встреча 20.11 19:30", user_id=1) is None
                assert (await one("select count(*) as n from meetings"))["n"] == count

            # 12. Сервис: ведущие, длительность, ближайшие и виды; встреча, которая идёт, ещё в списке.
            async with tenant_connection("whieda") as conn:
                consult = await service.create_meeting(
                    conn, "whieda", starts_at=dt.datetime(2026, 11, 20, 18, 0, tzinfo=service.MSK), kind="consult",
                    title="  Консультация  эксперта ", duration_min=90, hosts=["Виктор", " Ольга ", ""],
                    created_by="telegram:1",
                )
                ongoing = await service.create_meeting(
                    conn, "whieda", starts_at=dt.datetime(2026, 10, 30, 14, 30, tzinfo=service.MSK), title="Идёт сейчас"
                )
                ended = await service.create_meeting(
                    conn, "whieda", starts_at=dt.datetime(2026, 10, 30, 13, 0, tzinfo=service.MSK), title="Закончилась"
                )
            assert (consult["title"], consult["duration_min"], consult["hosts"]) == ("Консультация эксперта", 90, ["Виктор", "Ольга"])
            async with tenant_connection("whieda") as conn:
                live = await service.list_upcoming(conn, "whieda", "live", 10, now=NOW)
            # Идёт сейчас, 03.11 (#5), 05.11 (#1), 10.11 (#2); закончившаяся, отменённая (#3) и прошедшая (#4) — нет.
            assert [r["meeting_id"] for r in live] == [ongoing["meeting_id"], 5, 1, 2]
            assert ended["meeting_id"] not in [r["meeting_id"] for r in live]
            async with tenant_connection("whieda") as conn:
                both = await service.list_upcoming(conn, "whieda", None, 50, now=NOW)
                only_consult = await service.list_upcoming(conn, "whieda", "consult", 50, now=NOW)
                limited = await service.list_upcoming(conn, "whieda", None, 2, now=NOW)
                with pytest.raises(MeetingError) as bad_duration:
                    await service.create_meeting(conn, "whieda", starts_at=NOW, duration_min=0)
                with pytest.raises(MeetingError) as bad_url:
                    await service.create_meeting(conn, "whieda", starts_at=NOW, join_url="zoom.us/j/1")
            assert consult["meeting_id"] in [r["meeting_id"] for r in both] and len(limited) == 2
            assert [r["meeting_id"] for r in only_consult] == [consult["meeting_id"]]
            assert (bad_duration.value.code, bad_url.value.code) == ("bad_duration", "bad_url")
            starts = [r["starts_at"] for r in both]
            assert starts == sorted(starts)

            # 13. Публичные колонки: ссылка входа из базы не читается; ответ — по белому списку.
            async with tenant_connection("whieda") as conn:
                public_rows = await service.list_public_upcoming(conn, "whieda", "live", 10, now=NOW)
            assert public_rows and all(set(r) == {"meeting_id", "kind", "title", "starts_at", "duration_min", "hosts"} for r in public_rows)
            with patch.object(service, "utcnow", lambda: NOW):
                public = await service.load_public_meetings("whieda", "live")
            assert public and all(set(item) == {"id", "title", "starts_at", "duration_min", "hosts"} for item in public)
            assert all(item["starts_at"].endswith("+03:00") for item in public)
            assert "zoom.us" not in repr(public)
            assert public[0] == {"id": ongoing["meeting_id"], "title": "Идёт сейчас",
                                 "starts_at": "2026-10-30T14:30:00+03:00", "duration_min": 60, "hosts": []}
            service.forget_public_meetings()

            # 14. Арендаторы не смешиваются.
            async with tenant_connection("other") as conn:
                assert await service.list_upcoming(conn, "other", None, 50, now=NOW) == []
                assert await service.list_upcoming(conn, "whieda", None, 50, now=NOW) == []   # RLS режет и при чужом параметре
                assert (await fetch_one(conn, "select count(*) as n from meetings")) == {"n": 0}
                with pytest.raises(MeetingError) as foreign:
                    await service.cancel_meeting(conn, "whieda", 1)
                assert foreign.value.code == "not_found"
                mine = await service.create_meeting(conn, "other", starts_at=dt.datetime(2026, 11, 5, 19, 30, tzinfo=service.MSK))
                assert mine["tenant_id"] == "other"   # тот же момент, что у whieda, — не дубль: арендаторы раздельны
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with tenant_connection("other") as conn:
                    await fetch_one(
                        conn,
                        "insert into meetings (tenant_id, kind, starts_at) values ('whieda', 'live', now()) returning meeting_id",
                    )
            assert (await one("select status from meetings where meeting_id = 1"))["status"] == "planned"
            assert (await one("select count(*) as n from meetings where tenant_id = 'other'", tenant_id="other"))["n"] == 1

        try:
            db.run_with_app(proof)
        finally:
            from app.settings import get_settings

            get_settings.cache_clear()

        # Подключение без арендатора (set_config не вызван) не видит ни одной строки.
        with psycopg.connect(db.api_dsn, autocommit=True) as conn:
            assert conn.execute("select count(*) from meetings").fetchone()[0] == 0
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            assert conn.execute("select count(*) from meetings").fetchone()[0] > 5
            # Название с SQL осталось текстом, таблица на месте.
            assert conn.execute("select count(*) from meetings where title like '%drop table%'").fetchone()[0] == 1


def test_migration_file_has_no_dollar_sign_and_follows_the_sibling_layout():
    text = V43.read_text(encoding="utf-8")
    assert "$" not in text, "боевые правки идут из контейнера Core: знака доллара в файле быть не должно"
    for needle in (
        "create table if not exists meetings",
        "create index if not exists idx_meetings_tenant_starts",
        "set local lock_timeout = '5s'",
        "alter table meetings enable row level security",
        "drop policy if exists meetings_tenant_isolation on meetings",
        "platform_current_tenant_id()",
    ):
        assert needle in text, needle
    assert text.lstrip().startswith("--") and "begin;" in text and text.rstrip().endswith("commit;")
