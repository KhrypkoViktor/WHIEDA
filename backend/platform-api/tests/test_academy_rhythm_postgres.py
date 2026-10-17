"""Ритм «Онлайн-старта» на настоящей PostgreSQL (V36, 17.10.2026).

  * миграция дважды, academy_daily_checkins изолирована по тенанту;
  * в ритме — активный доступ к курсу и владелец; отозванный доступ и чужой — нет;
  * галочки по дням: три — день засчитан, снятая — нет; серия растёт, пропуск — «начнём заново»;
  * нажатие в боте правит ту же карточку, урок дня — следующий непройденный;
  * утро: одна карточка на человека в день в platform_outbox, worker шлёт её с кнопками;
  * воскресенье: табло в группу курса один раз, имена из анкеты или Telegram.
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, SQL_DIR, temporary_database

V36 = "platform_academy_rhythm_v36.sql"
OWNER, MARINA, SERGEY, REVOKED, STRANGER = 1, 9201, 9202, 9203, 9204
SUNDAY = date(2026, 10, 18)

PEOPLE = f"""
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id) values
  ('marina', 'whieda', 'Марина Иванова', {MARINA}, '{MARINA}'),
  ('sergey', 'whieda', 'Serg', {SERGEY}, '{SERGEY}'),
  ('owner', 'whieda', 'Виктор Хрипко', {OWNER}, '{OWNER}');
insert into academy_profiles (tenant_id, telegram_user_id, name) values ('whieda', {SERGEY}, 'Сергей Петров');
insert into academy_courses (tenant_id, slug, title, access_rule, status)
values ('whieda', 'online-start-4w', 'Онлайн-старт', 'purchase', 'published');
insert into academy_lessons (tenant_id, course_id, slug, position, title, body_html, est_minutes)
select 'whieda', course_id, 'start', 1, 'Самое сложное — начать', '<p>1</p>', 5 from academy_courses where slug = 'online-start-4w'
union all
select 'whieda', course_id, 'profil', 2, 'Профиль за вечер', '<p>2</p>', 12 from academy_courses where slug = 'online-start-4w';
insert into academy_access (tenant_id, course_id, telegram_user_id, source, revoked_at)
select 'whieda', course_id, {MARINA}, 'purchase', null::timestamptz from academy_courses where slug = 'online-start-4w'
union all
select 'whieda', course_id, {SERGEY}, 'gift', null from academy_courses where slug = 'online-start-4w'
union all
select 'whieda', course_id, {REVOKED}, 'purchase', now() from academy_courses where slug = 'online-start-4w';
"""


def _env(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", str(OWNER))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    for name in ("PLATFORM_RHYTHM_COURSE", "PLATFORM_RHYTHM_TIMEZONE", "PLATFORM_RHYTHM_WEEK1_START",
                 "PLATFORM_SCHEDULED_NOTIFY_BINDINGS", "PLATFORM_MAX_BOT_TOKEN"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.integration
def test_v36_twice_and_checkins_are_tenant_isolated():
    assert V36 in MIGRATIONS
    assert "$" not in (SQL_DIR / V36).read_text(encoding="utf-8")  # n8n режет знак доллара
    with temporary_database("whieda_rhythm_v36") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, MIGRATIONS, twice=True)
            conn.execute(
                "insert into academy_daily_checkins (tenant_id, telegram_user_id, day, post) values ('whieda', 1, %s, true)",
                (SUNDAY,),
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "insert into academy_daily_checkins (tenant_id, telegram_user_id, day) values ('whieda', 1, %s)",
                    (SUNDAY,),
                )
            assert conn.execute("select relrowsecurity from pg_class where relname = 'academy_daily_checkins'").fetchone() == (True,)
            db.grant_api_role(conn)
        with psycopg.connect(db.api_dsn, autocommit=True) as api:
            api.execute("select set_config('app.tenant_id', 'other', false)")
            assert api.execute("select count(*) from academy_daily_checkins").fetchone() == (0,)
            api.execute("select set_config('app.tenant_id', 'whieda', false)")
            assert api.execute("select post, person, five, done_at from academy_daily_checkins").fetchone() == (True, False, False, None)


@pytest.mark.integration
def test_marks_streak_card_morning_queue_and_sunday_board(monkeypatch, whieda_bot_binding, whieda_tenant):
    _env(monkeypatch)
    with temporary_database("whieda_rhythm") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, (*MIGRATIONS, "platform_service_sales_v10.sql"))
            conn.execute(PEOPLE)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy import rhythm
            from app.settings import get_settings
            from app.jobs.worker import process_due_notifications
            from app.telegram import rhythm as bot_rhythm
            from app.telegram.bindings import binding_context_scope
            from app.telegram.update_parser import TelegramCallbackQuery

            course = "online-start-4w"
            assert await rhythm.rhythm_members("whieda", course) == [OWNER, MARINA, SERGEY]
            assert await rhythm.rhythm_members("whieda", "net-kursa") == []
            assert await rhythm.is_member("whieda", course, SERGEY) and await rhythm.is_member("whieda", course, OWNER)
            assert not await rhythm.is_member("whieda", course, REVOKED)
            assert not await rhythm.is_member("whieda", course, STRANGER)

            async def full_day(uid: int, day: date) -> dict:
                row = {}
                for field in rhythm.FIELDS:
                    row = await rhythm.toggle_mark("whieda", uid, day, field)
                return row

            mon = SUNDAY - timedelta(days=6)
            # Марина: вся неделя. Сергей: пн–вт, пропуск в среду, чт–вс.
            for n in range(7):
                await full_day(MARINA, mon + timedelta(days=n))
            for n in (0, 1, 3, 4, 5, 6):
                await full_day(SERGEY, mon + timedelta(days=n))
            # Две галочки — день не засчитан; третья — засчитан; снятая — снова нет.
            wed = mon + timedelta(days=2)
            assert (await rhythm.toggle_mark("whieda", SERGEY, wed, "post"))["done_at"] is None
            assert (await rhythm.toggle_mark("whieda", SERGEY, wed, "five"))["done_at"] is None
            state = await rhythm.card_state("whieda", SERGEY, wed, wed)
            assert state == {"marks": {"post": True, "person": False, "five": True}, "streak": 2, "broken": False}
            assert (await rhythm.toggle_mark("whieda", SERGEY, wed, "five"))["five"] is False

            # Четверг у Сергея засчитан после пропуска среды: серия заново с одного дня.
            thu = wed + timedelta(days=1)
            assert await rhythm.card_state("whieda", SERGEY, thu, thu) == {
                "marks": {"post": True, "person": True, "five": True}, "streak": 1, "broken": False,
            }
            # Утро четверга, если бы четверга ещё не было: серия 0 после засчитанных дней — «начнём заново».
            await full_day(STRANGER, mon)
            assert await rhythm.card_state("whieda", STRANGER, thu, thu) == {
                "marks": {"post": False, "person": False, "five": False}, "streak": 0, "broken": True,
            }
            # Ни одного засчитанного дня — «серия пока не началась».
            assert (await rhythm.card_state("whieda", OWNER, thu, thu))["broken"] is False
            assert (await rhythm.card_state("whieda", MARINA, SUNDAY, SUNDAY))["streak"] == 7
            assert (await rhythm.card_state("whieda", SERGEY, SUNDAY, SUNDAY))["streak"] == 4

            with binding_context_scope(whieda_bot_binding), \
                 patch.object(bot_rhythm, "with_site_login", AsyncMock(side_effect=lambda url, **_: url + "#wwc-login=t")), \
                 patch.object(bot_rhythm, "answer_callback_query", AsyncMock(return_value={"ok": True})) as answer, \
                 patch.object(bot_rhythm, "_call_telegram", AsyncMock(return_value={"ok": True})) as call, \
                 patch.object(rhythm, "local_today", lambda now=None: SUNDAY + timedelta(days=1)):
                # Понедельник следующей недели: у Марины серия 7 и пустая карточка, урок дня — первый.
                monday = SUNDAY + timedelta(days=1)
                text, markup = await bot_rhythm.build_card("whieda", MARINA, monday, today=monday, sign=False)
                assert text == (
                    "Сегодня, 19 октября. Серия: 7 дней подряд.\n"
                    "☐ Пост или видео\n"
                    "☐ Один человек: написать, позвонить, пригласить\n"
                    "☐ Пятёрка: комментарий и сохранение у четверых\n"
                    "📖 Урок дня: «Самое сложное — начать», 5 минут"
                )
                assert markup["inline_keyboard"][1][0]["url"] == "https://wwc.best/academy/?course=online-start-4w&lesson=start"
                # Нажатия в боте: три галочки — серия 8, сообщение то же.
                for code in "phf":
                    callback = TelegramCallbackQuery(
                        chat_id=MARINA, user_id=MARINA, callback_query_id="cb", data=f"rh:{code}:20261019",
                        chat_type="private", raw={"callback_query": {"message": {"message_id": 41}}},
                    )
                    result = await bot_rhythm.try_handle_rhythm_callback(whieda_tenant, callback, trace_id="t")
                assert result["status"] == "day_done" and answer.await_args.kwargs["text"] == "День засчитан ✅"
                payload = call.await_args.args[1]
                assert payload["message_id"] == 41
                assert payload["text"].startswith("Сегодня, 19 октября. Серия: 8 дней подряд.\n✅ Пост или видео")
                assert payload["reply_markup"]["inline_keyboard"][1][0]["url"].endswith("lesson=start#wwc-login=t")
                # Чужой — ничего не записано.
                stranger = TelegramCallbackQuery(
                    chat_id=REVOKED, user_id=REVOKED, callback_query_id="cb", data="rh:p:20261019",
                    chat_type="private", raw={},
                )
                assert (await bot_rhythm.try_handle_rhythm_callback(whieda_tenant, stranger, trace_id="t"))["status"] == "not_member"
                assert (await rhythm.card_state("whieda", REVOKED, monday, monday))["marks"]["post"] is False

                # Утро: карточка каждому в ритме, повторный проход — без дублей.
                bindings = {"whieda": whieda_bot_binding}
                assert await bot_rhythm.enqueue_rhythm_cards(bindings, monday) == 3
                assert await bot_rhythm.enqueue_rhythm_cards(bindings, monday) == 0
                from app.db import fetch_all, tenant_connection

                async with tenant_connection("whieda") as conn:
                    rows = await fetch_all(
                        conn,
                        "select idempotency_key, status, payload from platform_outbox where event_type = %s order by idempotency_key",
                        (bot_rhythm.EVENT_TYPE,),
                    )
                assert [row["idempotency_key"] for row in rows] == [
                    f"rhythm_card:{course}:{uid}:2026-10-19" for uid in (OWNER, MARINA, SERGEY)
                ]
                marina = rows[1]["payload"]
                assert marina["chat_id"] == str(MARINA) and marina["site_login_user_id"] == MARINA
                assert marina["binding_id"] == whieda_bot_binding.binding_id
                assert marina["text"].startswith("Сегодня, 19 октября. Серия: 8 дней подряд.\n✅ Пост")
                assert "Серия: 4 дня подряд" in rows[2]["payload"]["text"]

                # Отправка — общий worker: галочки остаются, урок дня входит на сайт.
                send = AsyncMock(return_value={"ok": True, "message_id": 5})
                with patch("app.jobs.worker.send_telegram_text", send), \
                     patch("app.telegram.site_login.with_site_login", AsyncMock(side_effect=lambda url, **_: url + "#wwc-login=w")):
                    assert await process_due_notifications(bindings) == 3
                sent = {call_.kwargs["chat_id"]: call_.kwargs for call_ in send.await_args_list}
                keyboard = sent[str(SERGEY)]["reply_markup"]["inline_keyboard"]
                assert [b["callback_data"] for b in keyboard[0]] == ["rh:p:20261019", "rh:h:20261019", "rh:f:20261019"]
                assert keyboard[1][0]["url"].endswith("lesson=start#wwc-login=w")

            # Воскресенье: табло в группу курса один раз; без отметок за неделю — ничего.
            telegram = AsyncMock(return_value={"ok": True, "result": {"message_id": 99}})
            with patch("app.telegram.delivery._call_telegram", telegram):
                posted = await bot_rhythm.publish_board("whieda", SUNDAY, bot_token="t", once=True)
                again = await bot_rhythm.publish_board("whieda", SUNDAY, bot_token="t", once=True)
                empty = await bot_rhythm.publish_board("whieda", SUNDAY + timedelta(days=14), bot_token="t", once=True)
            assert posted["status"] == "posted" and again["status"] == "already_posted" and empty["status"] == "empty"
            assert telegram.await_count == 1
            sent = telegram.await_args.args[1]
            assert sent["chat_id"] == -1004497305833
            assert sent["text"] == "Неделя 1. Кто в ритме:\nМарина — 7 из 7, серия 7\nСергей — 6 из 7, серия 4"
            # Неделя считается от первой отметки или от PLATFORM_RHYTHM_WEEK1_START.
            week, people = await rhythm.board_people("whieda", course, SUNDAY + timedelta(days=7))
            assert week == 2 and rhythm.board_text(week, people) == "Неделя 2. Кто в ритме:\nМарина — 1 из 7"
            monkeypatch.setenv("PLATFORM_RHYTHM_WEEK1_START", "2026-10-19")
            get_settings.cache_clear()
            assert (await rhythm.board_people("whieda", course, SUNDAY + timedelta(days=7)))[0] == 1

        db.run_with_app(proof)
