"""Домашка как переписка (V32, 13.10.2026) на настоящем PostgreSQL.

  * миграция повторяется без ошибок, новые таблицы изолированы по тенанту;
  * одна нить на урок и ученика: вопрос, ответ с замечанием к фото и видео, «вернули» →
    исправления в ту же нить, «принято» закрывает её; комментарий решения — сообщение;
  * почта преподавателя: непросмотренное, карточка, «проверено / не проверено»;
  * уведомления: автору — по витрине (у ALMIRA выключено), ученику — ботом витрины;
  * медиа нити: ученику и преподавателю, работам группы — по works_public;
  * работы группы с анкетой; анкета чистит Instagram.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, SQL_DIR, temporary_database

V32 = "platform_academy_threads_v32.sql"
ADMIN, SASHA, MASHA, PETYA = 1, 7001, 9001, 9002
STUDENT_IMG, PETYA_IMG, AUTHOR_VIDEO, AUTHOR_IMG = (str(uuid.uuid4()) for _ in range(4))

SEED = f"""
insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) values
  ('almira', 'whieda', 'Александра Пармон', '7001'),
  ('masha', 'whieda', 'Мария', '9001'),
  ('petya', 'whieda', 'Пётр', '9002');
update lead_actors set telegram_user_id = 7001 where actor_id = 'almira';
update lead_actors set telegram_user_id = 9001 where actor_id = 'masha';
update lead_actors set telegram_user_id = 9002 where actor_id = 'petya';

insert into academy_courses (tenant_id, slug, title, access_rule, author_actor_id, status, works_public, review_days)
values ('whieda', 'kompoziciya', 'Композиция', 'purchase', 'almira', 'published', true, 7);
insert into academy_lessons (tenant_id, course_id, slug, position, title, body_html)
select 'whieda', course_id, 'urok-1', 1, 'Урок 1', '<p>1</p>' from academy_courses where slug = 'kompoziciya';
insert into academy_assignments (tenant_id, lesson_id, prompt_md, prompt_html, required)
select 'whieda', lesson_id, 'Фото работы', '<p>Фото работы</p>', true from academy_lessons where slug = 'urok-1';
insert into academy_access (tenant_id, course_id, telegram_user_id, source)
select 'whieda', course_id, u, 'key' from academy_courses, unnest(array[9001, 9002]) as u where slug = 'kompoziciya';

alter table tenant_bot_bindings
  add column if not exists bot_token_ref text,
  add column if not exists bot_username text;
insert into tenant_bot_bindings (binding_id, tenant_id, webhook_secret_ref, bot_token_ref, bot_username, status)
values ('almira-academy-bot', 'whieda', 'env:ALMIRA_SECRET', 'env:ALMIRA_TOKEN', 'ALMIRA_I_bot', 'active');
insert into academy_showcases (tenant_id, showcase_id, hosts, owner_actor_id, brand_name, academy_title,
                               telegram_binding_id, author_notify)
values ('whieda', 'almira', array['academy.almira.by'], 'almira', 'ALMIRA', 'Онлайн-академия ALMIRA',
        'almira-academy-bot', false);

insert into academy_media (tenant_id, media_id, owner_actor_id, owner_telegram_user_id, kind, original_name, mime,
                           size_bytes, storage_key, status, variants) values
  ('whieda', '{STUDENT_IMG}', 'masha', 9001, 'image', 'rabota.jpg', 'image/jpeg', 1000,
   'whieda/academy/{STUDENT_IMG}/original.jpg', 'ready', '{{}}'),
  ('whieda', '{PETYA_IMG}', 'petya', 9002, 'image', 'rabota2.jpg', 'image/jpeg', 1000,
   'whieda/academy/{PETYA_IMG}/original.jpg', 'ready', '{{}}'),
  ('whieda', '{AUTHOR_VIDEO}', 'almira', 7001, 'video', 'razbor.mp4', 'video/mp4', 5000,
   'whieda/academy/{AUTHOR_VIDEO}/original.mp4', 'processing', '{{}}'),
  ('whieda', '{AUTHOR_IMG}', 'almira', 7001, 'image', 'eskiz.png', 'image/png', 1000,
   'whieda/academy/{AUTHOR_IMG}/original.png', 'ready', '{{}}');
"""


def _env(monkeypatch, media_dir) -> None:
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", str(ADMIN))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(ADMIN))
    monkeypatch.setenv("PLATFORM_MEDIA_SIGNING_SECRET", "s" * 40)
    monkeypatch.setenv("PLATFORM_ACADEMY_MEDIA_DIR", str(media_dir))
    monkeypatch.setenv("PLATFORM_TELEGRAM_BOT_USERNAME", "WHIEDA_Advisor_bot")
    monkeypatch.setenv("PLATFORM_ACADEMY_SITE_BASE", "https://wwc.best")
    monkeypatch.delenv("PLATFORM_ACADEMY_NOTIFY_BINDING", raising=False)
    monkeypatch.setenv("PLATFORM_SCHEDULED_NOTIFY_BINDINGS", "whieda-advisor-bot")


@pytest.mark.integration
def test_v32_twice_and_tenant_isolation():
    assert V32 in MIGRATIONS
    sql = (SQL_DIR / V32).read_text(encoding="utf-8")
    assert "$" not in sql
    with temporary_database("whieda_threads_v32") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(sql)
            conn.execute(SEED)
            conn.execute(
                "insert into academy_profiles (tenant_id, telegram_user_id, name) values ('whieda', 9001, 'Мария')"
            )
            sid = conn.execute(
                "insert into academy_submissions (tenant_id, lesson_id, telegram_user_id, text)"
                " select 'whieda', lesson_id, 9001, 'x' from academy_lessons where slug = 'urok-1' returning submission_id"
            ).fetchone()[0]
            conn.execute(
                "insert into academy_submission_messages (tenant_id, submission_id, sender, telegram_user_id, text)"
                " values ('whieda', %s, 'author', 7001, 'ok')",
                (sid,),
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "insert into academy_submission_messages (tenant_id, submission_id, sender, telegram_user_id, text)"
                    " values ('whieda', %s, 'robot', 7001, 'ok')",
                    (sid,),
                )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("update academy_courses set review_days = 99 where slug = 'kompoziciya'")
            db.grant_api_role(conn)
        with psycopg.connect(db.api_dsn, autocommit=True) as api:
            api.execute("select set_config('app.tenant_id', 'other', false)")
            for table in ("academy_submission_messages", "academy_profiles"):
                assert api.execute(f"select count(*) from {table}").fetchone() == (0,), table
            api.execute("select set_config('app.tenant_id', 'whieda', false)")
            assert api.execute("select count(*) from academy_submission_messages").fetchone() == (1,)


@pytest.mark.integration
def test_thread_inbox_notifications_works_and_profile(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    with temporary_database("whieda_threads") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy import showcases as sc
            from app.academy.homework import list_submissions, review_submission, submit_homework
            from app.academy.media_service import load_media, media_access
            from app.academy.service import AcademyError, lesson_detail, load_viewer
            from app.academy.threads import (
                author_thread,
                get_profile,
                lesson_works,
                post_author_message,
                post_student_message,
                save_profile,
                set_seen,
            )
            from app.db import fetch_all, tenant_connection

            sc._reset_cache()
            sasha = await load_viewer("whieda", SASHA)
            masha = await load_viewer("whieda", MASHA)
            petya = await load_viewer("whieda", PETYA)

            async def outbox() -> list[dict]:
                async with tenant_connection("whieda") as conn:
                    return await fetch_all(conn, "select event_type, payload from platform_outbox order by outbox_id")

            # 1. Анкета: имя обязательно, Instagram чистится до имени.
            saved = await save_profile(
                "whieda", masha,
                {"name": " Мария ", "city": "Минск", "instagram": "https://www.instagram.com/masha_art/?hl=ru"},
            )
            assert saved["profile"]["name"] == "Мария" and saved["profile"]["instagram"] == "masha_art"
            with pytest.raises(AcademyError) as no_name:
                await save_profile("whieda", petya, {"name": ""})
            assert no_name.value.code == "name_required"
            assert (await get_profile("whieda", petya))["profile"]["filled"] is False

            # 2. Сдача: нить пустая, автору ALMIRA уведомления нет (витрина author_notify=false).
            sent = await submit_homework("whieda", "kompoziciya", "urok-1", masha, text="Готово", media_ids=[STUDENT_IMG])
            sid = sent["submission"]["submission_id"]
            assert sent["submission"]["status"] == "submitted" and sent["submission"]["messages"] == []
            assert [row["event_type"] for row in await outbox()] == []

            # 3. Вопрос ученика — сообщение нити; отправителю видно, непрочитанного у него нет.
            asked = await post_student_message("whieda", "kompoziciya", "urok-1", masha, text="А фон?", media_ids=[])
            assert [m["sender"] for m in asked["submission"]["messages"]] == ["student"]
            assert asked["submission"]["unread"] == 0

            # 4. Почта преподавателя: непросмотренное, анкета в строке, карточка помечает просмотренной.
            [row] = await list_submissions("whieda", sasha, status="submitted")
            assert row["unread"] is True and row["student"]["name"] == "Мария" and row["student"]["instagram"] == "masha_art"
            card = await author_thread("whieda", sid, sasha)
            assert card["submission"]["seen_at"] and card["submission"]["student"]["city"] == "Минск"
            assert card["submission"]["prompt_html"] == "<p>Фото работы</p>"
            [row] = await list_submissions("whieda", sasha, status="submitted")
            assert row["unread"] is False
            await set_seen("whieda", sid, sasha, seen=False)
            [row] = await list_submissions("whieda", sasha, status="submitted")
            assert row["unread"] is True
            with pytest.raises(AcademyError) as stranger:
                await author_thread("whieda", sid, petya)
            assert stranger.value.code == "not_author"

            # 5. Ответ преподавателя: замечание к фото и видео в обработке; ученице — уведомление ботом ALMIRA.
            replied = await post_author_message(
                "whieda", sid, sasha, text="Линию тоньше", media_ids=[AUTHOR_VIDEO], about_media_id=STUDENT_IMG
            )
            last = replied["submission"]["messages"][-1]
            assert last["sender"] == "author" and last["about_media_id"] == STUDENT_IMG
            assert last["media"][0]["kind"] == "video" and last["media"][0]["status"] == "processing"
            [note] = await outbox()
            assert note["event_type"] == "academy_hw_message"
            assert note["payload"]["binding_id"] == "almira-academy-bot" and note["payload"]["chat_id"] == "9001"
            assert note["payload"]["site_button"]["url"] == "https://academy.almira.by/academy/?course=kompoziciya&lesson=urok-1"
            with pytest.raises(AcademyError) as foreign:
                await post_author_message("whieda", sid, sasha, text="?", media_ids=[], about_media_id=PETYA_IMG)
            assert foreign.value.code == "bad_media"
            with pytest.raises(AcademyError) as not_mine:
                await post_author_message("whieda", sid, sasha, text="?", media_ids=[PETYA_IMG])
            assert not_mine.value.code == "bad_media"

            # 6. Ученица видит непрочитанный ответ; открытие урока гасит счётчик; срок ответа и работы группы.
            detail = await lesson_detail("whieda", "kompoziciya", "urok-1", masha)
            assert detail["lesson"]["my_submission"]["unread"] == 1
            assert detail["lesson"]["assignment"]["review_days"] == 7 and detail["lesson"]["works_public"] is True
            detail = await lesson_detail("whieda", "kompoziciya", "urok-1", masha)
            assert detail["lesson"]["my_submission"]["unread"] == 0

            # 7. Медиа нити: видео преподавателя — ученице; фото Маши — Пете, пока работы группы открыты.
            async with tenant_connection("whieda") as conn:
                rows = await load_media(conn, "whieda", [AUTHOR_VIDEO, STUDENT_IMG])
                assert await media_access(conn, "whieda", rows[AUTHOR_VIDEO], masha) is True
                assert await media_access(conn, "whieda", rows[AUTHOR_VIDEO], petya) is True
                assert await media_access(conn, "whieda", rows[STUDENT_IMG], petya) is True
                async with conn.cursor() as cur:
                    await cur.execute("update academy_courses set works_public = false where slug = 'kompoziciya'")
                assert await media_access(conn, "whieda", rows[STUDENT_IMG], petya) is False
                assert await media_access(conn, "whieda", rows[STUDENT_IMG], sasha) is True
                async with conn.cursor() as cur:
                    await cur.execute("update academy_courses set works_public = true where slug = 'kompoziciya'")

            # 8. «Вернули» с комментарием — сообщение; исправления идут в ту же нить, статус снова «на проверке».
            await review_submission("whieda", sid, sasha, status="returned", comment="Переделай фон")
            card = await author_thread("whieda", sid, sasha)
            assert card["submission"]["status"] == "returned"
            assert card["submission"]["messages"][-1]["text"] == "Переделай фон"
            fixed = await submit_homework("whieda", "kompoziciya", "urok-1", masha, text="Исправила", media_ids=[])
            assert fixed["submission"]["submission_id"] == sid and fixed["submission"]["status"] == "submitted"
            assert fixed["submission"]["messages"][-1]["text"] == "Исправила"
            async with tenant_connection("whieda") as conn:
                count = await fetch_all(
                    conn, "select submission_id from academy_submissions where telegram_user_id = 9001"
                )
            assert len(count) == 1

            # 9. «Принято» закрывает нить.
            await review_submission("whieda", sid, sasha, status="accepted", comment="Молодец")
            with pytest.raises(AcademyError) as closed:
                await post_student_message("whieda", "kompoziciya", "urok-1", masha, text="ещё", media_ids=[])
            assert closed.value.code == "already_accepted"
            with pytest.raises(AcademyError):
                await post_author_message("whieda", sid, sasha, text="ещё", media_ids=[])
            events = [row["event_type"] for row in await outbox()]
            assert events.count("academy_hw_reviewed") == 2 and events.count("academy_hw_submitted") == 0

            # 10. Работы группы: свежие первыми, имя и Instagram из анкеты, ответы преподавателя видны.
            await submit_homework("whieda", "kompoziciya", "urok-1", petya, text="Моя", media_ids=[PETYA_IMG])
            works = await lesson_works("whieda", "kompoziciya", "urok-1", petya)
            assert [w["student"]["name"] for w in works["works"]] == ["Пётр", "Мария"]
            mashas = works["works"][1]
            assert mashas["student"]["instagram"] == "masha_art" and mashas["student"]["is_me"] is False
            assert mashas["status"] == "accepted" and mashas["media"][0]["url"]
            assert [m["text"] for m in mashas["messages"] if m["sender"] == "author"] == ["Линию тоньше", "Переделай фон", "Молодец"]
            assert works["works"][0]["student"]["is_me"] is True
            async with tenant_connection("whieda") as conn:
                async with conn.cursor() as cur:
                    await cur.execute("update academy_courses set works_public = false where slug = 'kompoziciya'")
            with pytest.raises(AcademyError) as private:
                await lesson_works("whieda", "kompoziciya", "urok-1", petya)
            assert private.value.code == "works_private"
            assert len((await lesson_works("whieda", "kompoziciya", "urok-1", sasha))["works"]) == 2

        db.run_with_app(proof)
