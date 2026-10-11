"""Встречи клуба: расписание и напоминание за 2 часа на настоящей базе (11.10.2026)."""

from __future__ import annotations

import datetime as dt
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import temporary_database

UTC = dt.timezone.utc


def test_reminders_are_due_two_hours_before_moscow_time():
    from app.telegram.club_meetings import due_meetings

    # Вторник 14.10.2026: встреча 10:30 МСК = 07:30 UTC, напоминание с 05:30 UTC в течение 45 минут.
    assert due_meetings(dt.datetime(2026, 10, 14 - 1, 5, 29, tzinfo=UTC)) == []
    [(meeting, day)] = due_meetings(dt.datetime(2026, 10, 13, 5, 30, tzinfo=UTC))
    assert meeting.key == "tue" and day == dt.date(2026, 10, 13)
    assert due_meetings(dt.datetime(2026, 10, 13, 6, 15, tzinfo=UTC)) == []
    # Воскресенье 18.10.2026: 14:00 МСК → напоминание в 12:00 МСК = 09:00 UTC.
    assert [m.key for m, _ in due_meetings(dt.datetime(2026, 10, 18, 9, 10, tzinfo=UTC))] == ["sun"]
    # Не в свой день — ничего.
    assert due_meetings(dt.datetime(2026, 10, 15, 5, 30, tzinfo=UTC)) == []


def test_texts_offer_a_choice_and_ask_for_the_camera():
    from app.telegram.club_meetings import MEETINGS, reminder_text, schedule_text

    schedule = schedule_text()
    assert "выбирайте сами" in schedule and "вторник, 10:30 по Москве" in schedule and "воскресенье, 14:00 по Москве" in schedule
    assert schedule.count("Войти в Zoom") == 2 and "85887153238" in schedule and "81097590923" in schedule
    reminder = reminder_text(MEETINGS[0])
    assert reminder.startswith("⏰ <b>Сегодня встреча клуба в 10:30 по Москве</b>")
    assert "Приходите" in reminder and "камеру" in reminder and "1,5 часа" in reminder and "81097590923" in reminder


@pytest.mark.integration
def test_reminder_goes_once_to_telegram_and_max_and_only_from_production(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLATFORM_MAX_BOT_TOKEN", "max-proof")
    with temporary_database("whieda_club_meetings") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, (*__import__("tests.postgres_testkit", fromlist=["MIGRATIONS"]).MIGRATIONS, "platform_service_sales_v10.sql"))
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.settings import get_settings
            from app.telegram import club_meetings

            telegram = AsyncMock(return_value={"ok": True, "result": {"message_id": 77}})
            send_max = AsyncMock(return_value={"ok": True, "mid": "mid.club.r1"})
            binding = type("B", (), {"bot_token": "t"})()
            tuesday = dt.datetime(2026, 10, 13, 5, 31, tzinfo=UTC)
            with patch("app.telegram.delivery._call_telegram", telegram), patch("app.max.client.send_max_message", send_max), \
                 patch("app.telegram.bindings.resolve_bot_binding_context", AsyncMock(return_value=binding)):
                # Staging делит базу с боем — не напоминает.
                monkeypatch.setenv("ENVIRONMENT", "staging")
                get_settings.cache_clear()
                assert await club_meetings.club_meetings_step(tuesday) == 0
                monkeypatch.setenv("ENVIRONMENT", "production")
                get_settings.cache_clear()
                assert await club_meetings.club_meetings_step(tuesday) == 1
                assert await club_meetings.club_meetings_step(tuesday + dt.timedelta(minutes=5)) == 0
                assert telegram.await_count == 1 and send_max.await_count == 1
                assert telegram.await_args.args[1]["chat_id"] == -1004338290116
                assert send_max.await_args.kwargs["chat_id"] == -79980845696385
                assert "10:30" in send_max.await_args.kwargs["text"]
            get_settings.cache_clear()

        db.run_with_app(proof)
