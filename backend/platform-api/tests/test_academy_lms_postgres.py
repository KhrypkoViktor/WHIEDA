"""Academy v2 «LMS» on real PostgreSQL (V19, 02.10.2026).

  * the migration runs twice; old access rows start when they were granted;
  * new tables are tenant-isolated (RLS) and the API role reads them;
  * one active homework per (lesson, student); a returned one can be resubmitted.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

V19 = "platform_academy_lms_v19.sql"

SEED_COURSE = """
insert into academy_courses (tenant_id, slug, title, access_rule, status)
values ('whieda', 'kurs', 'Курс', 'purchase', 'published');
insert into academy_lessons (tenant_id, course_id, slug, module_title, position, title, body_html)
select 'whieda', course_id, 'urok-1', 'Неделя 1', 1, 'Урок 1', '<p>1</p>' from academy_courses where slug = 'kurs';
insert into academy_access (tenant_id, course_id, telegram_user_id, source, granted_at)
select 'whieda', course_id, 9001, 'key', timestamptz '2026-09-01 10:00+00' from academy_courses where slug = 'kurs';
"""


@pytest.mark.integration
def test_v19_migration_twice_backfills_start_and_isolates_tenants():
    assert V19 in MIGRATIONS
    before_v19 = [name for name in MIGRATIONS if name != V19]
    with temporary_database("whieda_academy_v19") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, before_v19)
            conn.execute(SEED_COURSE)
            sql = (db_sql_dir() / V19).read_text(encoding="utf-8")
            assert "$" not in sql  # боевые правки идут через n8n, он режет знак доллара
            conn.execute(sql)
            conn.execute(sql)

            started = conn.execute(
                "select started_at = granted_at, expires_at from academy_access where telegram_user_id = 9001"
            ).fetchone()
            assert started == (True, None)

            columns = {
                (row[0], row[1])
                for row in conn.execute(
                    """
                    select table_name, column_name from information_schema.columns
                    where table_schema = 'public' and table_name like 'academy_%%'
                    """
                ).fetchall()
            }
            for expected in [
                ("academy_courses", "description_html"),
                ("academy_courses", "description_md"),
                ("academy_courses", "cover_media_id"),
                ("academy_courses", "price_currency"),
                ("academy_courses", "kind"),
                ("academy_modules", "unlock"),
                ("academy_lessons", "module_id"),
                ("academy_lessons", "kind"),
                ("academy_lessons", "live_at"),
                ("academy_lessons", "live_url"),
                ("academy_lessons", "unlock"),
                ("academy_lessons", "files"),
                ("academy_lessons", "body_md"),
                ("academy_assignments", "prompt_html"),
                ("academy_assignments", "required"),
                ("academy_submissions", "status"),
                ("academy_submissions", "media"),
                ("academy_access", "started_at"),
                ("academy_access", "expires_at"),
                ("academy_media", "variants"),
                ("academy_media", "status"),
            ]:
                assert expected in columns, expected

            defaults = conn.execute(
                """
                select c.price_currency, c.kind, l.kind, l.files, l.unlock
                from academy_courses c join academy_lessons l on l.course_id = c.course_id
                """
            ).fetchone()
            assert defaults == ("WUSD", "course", "lesson", [], None)

            rls = dict(
                conn.execute(
                    """
                    select relname, relrowsecurity from pg_class
                    where relname in ('academy_modules', 'academy_assignments', 'academy_submissions', 'academy_media')
                    """
                ).fetchall()
            )
            assert rls == {
                "academy_modules": True,
                "academy_assignments": True,
                "academy_submissions": True,
                "academy_media": True,
            }

            course_id = conn.execute("select course_id from academy_courses where slug = 'kurs'").fetchone()[0]
            lesson_id = conn.execute("select lesson_id from academy_lessons where slug = 'urok-1'").fetchone()[0]
            # Правило открытия — только известные типы.
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "insert into academy_modules (tenant_id, course_id, position, title, unlock)"
                    " values ('whieda', %s, 1, 'М', '{\"type\": \"someday\"}')",
                    (course_id,),
                )
            conn.execute(
                "insert into academy_modules (tenant_id, course_id, position, title, unlock)"
                " values ('whieda', %s, 1, 'Неделя 1', '{\"type\": \"days_after_start\", \"days\": 7}')",
                (course_id,),
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("update academy_lessons set kind = 'webinar' where lesson_id = %s", (lesson_id,))

            # Одна активная домашка на (урок, ученик); после «вернули» — новая строка.
            conn.execute(
                "insert into academy_submissions (tenant_id, lesson_id, telegram_user_id, text)"
                " values ('whieda', %s, 9001, 'первая')",
                (lesson_id,),
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "insert into academy_submissions (tenant_id, lesson_id, telegram_user_id, text)"
                    " values ('whieda', %s, 9001, 'вторая')",
                    (lesson_id,),
                )
            conn.execute("update academy_submissions set status = 'returned' where telegram_user_id = 9001")
            conn.execute(
                "insert into academy_submissions (tenant_id, lesson_id, telegram_user_id, text)"
                " values ('whieda', %s, 9001, 'вторая')",
                (lesson_id,),
            )
            conn.execute(
                "insert into academy_media (tenant_id, owner_telegram_user_id, kind, original_name, mime, size_bytes,"
                " storage_key) values ('whieda', 9001, 'image', 'a.png', 'image/png', 10, 'whieda/academy/x/original.png')"
            )
            db.grant_api_role(conn)

        # Роль API видит строки только своего тенанта.
        with psycopg.connect(db.api_dsn, autocommit=True) as api:
            api.execute("select set_config('app.tenant_id', 'other', false)")
            for table in ("academy_modules", "academy_submissions", "academy_media"):
                assert api.execute(f"select count(*) from {table}").fetchone() == (0,), table
            api.execute("select set_config('app.tenant_id', 'whieda', false)")
            assert api.execute("select count(*) from academy_submissions").fetchone() == (2,)
            assert api.execute("select count(*) from academy_media").fetchone() == (1,)


def db_sql_dir():
    from tests.postgres_testkit import SQL_DIR

    return SQL_DIR


# ---- shared seed for the service-level proofs -------------------------------------------------

SECRET = "m" * 40
ADMIN, AUTHOR, STUDENT, STRANGER = 1, 7001, 9001, 9002
IMAGE_ID, FILE_ID, VIDEO_ID = (str(uuid.uuid4()) for _ in range(3))

PEOPLE = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id, telegram_username) values
  ('almira', 'whieda', 'Альмира', '7001', 'almira_art'),
  ('masha', 'whieda', 'Мария', '9001', 'masha_s');
update lead_actors set telegram_user_id = 7001 where actor_id = 'almira';
update lead_actors set telegram_user_id = 9001 where actor_id = 'masha';
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, enabled)
values ('almira', 'whieda', 'almira', 'named', true);
-- Размещение автора в одноразовой базе: проверяется только право публиковать и выдавать ключи.
insert into academy_shelf (tenant_id, actor_id, paid_until, status)
values ('whieda', 'almira', now() + interval '60 days', 'active');
"""


def _env(monkeypatch, media_dir) -> None:
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", str(ADMIN))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(ADMIN))
    monkeypatch.setenv("PLATFORM_MEDIA_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("PLATFORM_ACADEMY_MEDIA_DIR", str(media_dir))
    monkeypatch.delenv("PLATFORM_ACADEMY_MEDIA_VIA_API", raising=False)
    monkeypatch.delenv("PLATFORM_ACADEMY_NOTIFY_BINDING", raising=False)
    monkeypatch.delenv("PLATFORM_SCHEDULED_NOTIFY_BINDINGS", raising=False)


def _seed_course(conn: psycopg.Connection, now: datetime) -> dict[str, str]:
    """«Акварель»: неделя 1 открыта (a, b; у a — видео, файл, картинка), неделя 2 после
    недели 1 (c — с обязательной домашкой), неделя 3 по дате (d), неделя 4 через 7 дней (e)."""
    course_id = conn.execute(
        """
        insert into academy_courses (tenant_id, slug, title, subtitle, access_rule, author_actor_id, status,
                                     price_wusd_minor, price_currency, description_html)
        values ('whieda', 'akvarel', 'Акварель за 6 недель', 'Для начинающих', 'purchase', 'almira',
                'published', 25000, 'BYN', '<p>О курсе</p><script>alert(1)</script>')
        returning course_id
        """
    ).fetchone()[0]
    rules = [
        ("Неделя 1", {"type": "open"}),
        ("Неделя 2", {"type": "after_prev"}),
        ("Неделя 3", {"type": "date", "at": (now + timedelta(days=10)).isoformat()}),
        ("Неделя 4", {"type": "days_after_start", "days": 7}),
    ]
    modules = []
    for position, (title, rule) in enumerate(rules, start=1):
        modules.append(
            conn.execute(
                "insert into academy_modules (tenant_id, course_id, position, title, unlock)"
                " values ('whieda', %s, %s, %s, %s::jsonb) returning module_id",
                (course_id, position, title, json.dumps(rule)),
            ).fetchone()[0]
        )
    for media_id, kind, mime, name, key, variants in (
        (IMAGE_ID, "image", "image/png", "схема.png", f"whieda/academy/{IMAGE_ID}/original.png", {}),
        (FILE_ID, "file", "application/pdf", "Тетрадь.pdf", f"whieda/academy/{FILE_ID}/original.pdf", {}),
        (VIDEO_ID, "video", "video/mp4", "урок1.mp4", f"whieda/academy/{VIDEO_ID}/original.mp4",
         {"mp4_720": f"whieda/academy/{VIDEO_ID}/720.mp4", "poster": f"whieda/academy/{VIDEO_ID}/poster.jpg",
          "duration_sec": 61}),
    ):
        conn.execute(
            "insert into academy_media (tenant_id, media_id, owner_actor_id, owner_telegram_user_id, kind,"
            " original_name, mime, size_bytes, storage_key, status, variants)"
            " values ('whieda', %s, 'almira', 7001, %s, %s, %s, 1234, %s, 'ready', %s::jsonb)",
            (media_id, kind, name, mime, key, json.dumps(variants)),
        )
    lessons = [
        ("a", 0, "Кисти и бумага", f'<p>Смотрите схему</p><img src="media:{IMAGE_ID}"><script>alert(1)</script>',
         {"media_id": VIDEO_ID}, [FILE_ID]),
        ("b", 0, "Первый мазок", "<p>b</p>", None, []),
        ("c", 1, "Заливка", "<p>c</p>", None, []),
        ("d", 2, "Пейзаж", "<p>d</p>", None, []),
        ("e", 3, "Портрет", "<p>e</p>", None, []),
    ]
    ids = {}
    for position, (slug, module_index, title, body, video, files) in enumerate(lessons, start=1):
        ids[slug] = conn.execute(
            """
            insert into academy_lessons (tenant_id, course_id, module_id, slug, position, title, body_html, video, files)
            values ('whieda', %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb) returning lesson_id
            """,
            (course_id, modules[module_index], slug, position, title, body,
             json.dumps(video) if video else None, json.dumps(files)),
        ).fetchone()[0]
    conn.execute(
        "insert into academy_assignments (tenant_id, lesson_id, prompt_md, prompt_html, required)"
        " values ('whieda', %s, 'Пришлите фото заливки', '<p>Пришлите фото заливки</p>', true)",
        (ids["c"],),
    )
    conn.execute(
        "insert into academy_access (tenant_id, course_id, telegram_user_id, source, payment_ref, started_at)"
        " values ('whieda', %s, %s, 'key', 'k1', %s)",
        (course_id, STUDENT, now - timedelta(days=1)),
    )
    return {"course_id": str(course_id), **{slug: str(lesson_id) for slug, lesson_id in ids.items()}}


@pytest.mark.integration
def test_student_sees_modules_locks_and_lesson_media(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    now = datetime.now(timezone.utc)
    with temporary_database("whieda_academy_student") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(PEOPLE)
            ids = _seed_course(conn, now)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy.media_service import media_url
            from app.academy.media import MediaError
            from app.academy.service import (
                AcademyError,
                course_outline,
                lesson_detail,
                list_courses,
                load_viewer,
                set_lesson_done,
            )
            from app.db import tenant_connection

            student = await load_viewer("whieda", STUDENT)
            stranger = await load_viewer("whieda", STRANGER)
            author = await load_viewer("whieda", AUTHOR)

            # 1. Каталог: цена для показа, автор, прогресс.
            [course] = await list_courses("whieda", student)
            assert course["price"] == {"amount": 250, "currency": "BYN"}
            assert course["author_name"] == "Альмира"
            assert (course["locked"], course["lessons_total"], course["lessons_done"]) == (False, 5, 0)
            assert course["cover_url"] is None
            [locked_course] = await list_courses("whieda", stranger)
            assert (locked_course["locked"], locked_course["lock_reason"]) == (True, "purchase_required")
            assert locked_course["author_contact"]["telegram"] == "almira_art"

            # 2. Без доступа: программа видна, все уроки под замком «доступ по ключу».
            outline = await course_outline("whieda", "akvarel", stranger, allow_locked=True)
            assert outline["course"]["locked"] and outline["course"]["lock_reason"] == "purchase_required"
            assert {(row["locked"], row["lock_reason"]) for row in outline["lessons"]} == {(True, "purchase")}
            assert outline["course"]["next_lesson"] == "a"  # старый сайт ведёт на замок урока, а не «курс пройден»
            with pytest.raises(AcademyError) as no_key:
                await course_outline("whieda", "akvarel", stranger)  # бот: замок курса, как раньше
            assert no_key.value.code == "purchase_required"
            with pytest.raises(AcademyError) as no_key_lesson:
                await lesson_detail("whieda", "akvarel", "a", stranger)
            assert no_key_lesson.value.status == 403 and no_key_lesson.value.code == "purchase_required"
            assert no_key_lesson.value.extra["lock_reason"] == "purchase"

            # 3. Ученик: модули, замки с причинами, следующий шаг.
            outline = await course_outline("whieda", "akvarel", student, allow_locked=True)
            assert [m["title"] for m in outline["modules"]] == ["Неделя 1", "Неделя 2", "Неделя 3", "Неделя 4"]
            locks = {row["slug"]: (row["locked"], row["lock_reason"]) for row in outline["lessons"]}
            date_reason = locks["d"][1]
            assert locks["a"] == (False, None) and locks["b"] == (False, None)
            assert locks["c"] == (True, "after_prev")
            assert locks["d"][0] and date_reason.startswith("date:")
            assert locks["e"] == (True, "days:7")
            e_row = next(row for row in outline["lessons"] if row["slug"] == "e")
            assert e_row["opens_at"] is not None
            c_row = next(row for row in outline["lessons"] if row["slug"] == "c")
            assert c_row["assignment_status"] == "not_submitted"
            assert next(row for row in outline["lessons"] if row["slug"] == "a")["assignment_status"] is None
            assert outline["course"]["next_lesson"] == "a"
            assert [row["number"] for row in outline["lessons"]] == [1, 2, 3, 4, 5]

            with pytest.raises(AcademyError) as closed:
                await lesson_detail("whieda", "akvarel", "c", student)
            assert (closed.value.status, closed.value.code, closed.value.extra["lock_reason"]) == (
                403, "lesson_locked", "after_prev",
            )
            with pytest.raises(AcademyError) as closed_done:
                await set_lesson_done("whieda", "akvarel", "d", student, done=True, source="site")
            assert closed_done.value.code == "lesson_locked"

            # 4. Урок с медиа: тело очищено, картинка/видео/файл — подписанные ссылки ученика.
            detail = await lesson_detail("whieda", "akvarel", "a", student)
            body = detail["lesson"]["body_html"]
            assert "<script" not in body and "media:" not in body
            assert f"/academy-media/whieda/academy/{IMAGE_ID}/original.png?u={STUDENT}&amp;e=" in body
            video = detail["lesson"]["video"]
            assert video["provider"] == "file" and video["status"] == "ready" and video["duration_sec"] == 61
            assert video["id"].startswith(f"/academy-media/whieda/academy/{VIDEO_ID}/720.mp4?u={STUDENT}&e=")
            assert video["poster"].startswith(f"/academy-media/whieda/academy/{VIDEO_ID}/poster.jpg?u={STUDENT}")
            [attachment] = detail["lesson"]["files"]
            assert (attachment["name"], attachment["size"]) == ("Тетрадь.pdf", 1234)
            assert attachment["url"].startswith(f"/academy-media/whieda/academy/{FILE_ID}/original.pdf?u={STUDENT}")
            assert detail["lesson"]["assignment"] is None and detail["lesson"]["my_submission"] is None
            assert detail["next"]["slug"] == "b" and detail["prev"] is None

            # 5. Неделя 1 пройдена → неделя 2 открылась, у урока c — домашка.
            await set_lesson_done("whieda", "akvarel", "a", student, done=True, source="site")
            progress = await set_lesson_done("whieda", "akvarel", "b", student, done=True, source="bot")
            assert (progress["lessons_done"], progress["next_lesson"]["slug"]) == (2, "c")
            detail_c = await lesson_detail("whieda", "akvarel", "c", student)
            assert detail_c["lesson"]["assignment"] == {"prompt_html": "<p>Пришлите фото заливки</p>", "required": True}

            # 6. Ссылка на медиа по id: ученик с доступом — да, чужой — нет, автор — да.
            link = await media_url("whieda", FILE_ID, student)
            assert link["url"].startswith(f"/academy-media/whieda/academy/{FILE_ID}/original.pdf?u={STUDENT}")
            with pytest.raises(MediaError) as foreign:
                await media_url("whieda", FILE_ID, stranger)
            assert foreign.value.status == 403
            assert (await media_url("whieda", VIDEO_ID, author))["poster_url"].startswith("/academy-media/")

            # 7. Автор видит свой курс целиком: без замков доступа и расписания.
            authored = await course_outline("whieda", "akvarel", author, allow_locked=True)
            assert not authored["course"]["locked"]
            assert {row["locked"] for row in authored["lessons"]} == {False}

            # 8. Истёкший доступ — снова замок «по ключу».
            async with tenant_connection("whieda") as conn:
                await conn.execute(
                    "update academy_access set expires_at = now() - interval '1 minute' where telegram_user_id = %s",
                    (STUDENT,),
                )
            [expired] = await list_courses("whieda", student)
            assert (expired["locked"], expired["lock_reason"]) == (True, "purchase_required")

        db.run_with_app(proof)
