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


def _make_clip(path, *, seconds: int = 4, size: str = "1920x1080") -> bool:
    """A short test clip with sound (ffmpeg's own generators); False without ffmpeg."""
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        return False
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size={size}:rate=25",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            "-f", "mov", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return True


def _probe(path) -> dict:
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,codec_name",
         "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)["streams"][0]


@pytest.mark.integration
def test_media_upload_state_machine_and_transcode(monkeypatch, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    _env(monkeypatch, media_dir)
    clip = tmp_path / "clip.mov"
    have_ffmpeg = _make_clip(clip)
    with temporary_database("whieda_academy_media") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(PEOPLE)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy.media import MediaError, get_media_store
            from app.academy.media_service import complete_upload, init_upload, put_chunk, upload_status
            from app.academy.service import load_viewer
            from app.academy.transcode import EVENT_TYPE, claim_job, process_job
            from app.db import fetch_all, tenant_connection
            from app.jobs.worker import process_pending_outbox

            async def rows(sql: str, params: tuple = ()) -> list[dict]:
                async with tenant_connection("whieda") as conn:
                    return [dict(r) for r in await fetch_all(conn, sql, params)]

            student = await load_viewer("whieda", STUDENT)
            author = await load_viewer("whieda", AUTHOR)
            stranger = await load_viewer("whieda", STRANGER)
            store = get_media_store()

            # 1. Фото домашки тремя кусками, в любом порядке, с докачкой.
            png = b"\x89PNG\r\n\x1a\n" + bytes(range(12))
            init = await init_upload(
                "whieda", student, kind="image", name="фото.png", mime="image/png", size=len(png),
                allowed_kinds=("image",), chunk_size=8,
            )
            media_id = init["media_id"]
            assert (init["status"], init["chunk_size"], init["chunks_total"]) == ("uploading", 8, 3)
            with pytest.raises(MediaError) as no_video:
                await init_upload("whieda", student, kind="video", name="v.mp4", mime="video/mp4", size=10,
                                  allowed_kinds=("image",))
            assert (no_video.value.status, no_video.value.code) == (403, "kind_not_allowed")
            await put_chunk("whieda", student, media_id, 2, png[16:])
            await put_chunk("whieda", student, media_id, 0, png[:8])
            status = await upload_status("whieda", student, media_id)
            assert (status["status"], status["chunks_received"]) == ("uploading", [0, 2])
            with pytest.raises(MediaError) as wrong_size:
                await put_chunk("whieda", student, media_id, 1, png[8:15])
            assert wrong_size.value.code == "bad_chunk_size"
            with pytest.raises(MediaError) as foreign:
                await put_chunk("whieda", stranger, media_id, 1, png[8:16])
            assert foreign.value.status == 404
            with pytest.raises(MediaError) as gap:
                await complete_upload("whieda", student, media_id)
            assert (gap.value.code, gap.value.extra["missing"]) == ("upload_incomplete", [1])
            await put_chunk("whieda", student, media_id, 1, png[8:16])
            ready = await complete_upload("whieda", student, media_id)
            assert ready["status"] == "ready" and ready["url"].startswith(f"/academy-media/whieda/academy/{media_id}/")
            assert (await complete_upload("whieda", student, media_id))["status"] == "ready"  # повтор безопасен
            assert store.path(f"whieda/academy/{media_id}/original.png").read_bytes() == png
            with pytest.raises(MediaError) as closed:
                await put_chunk("whieda", student, media_id, 0, png[:8])
            assert closed.value.code == "not_uploading"

            # 2. «Картинка», которая на деле HTML, — не принимается.
            fake = b"<html><script>alert(1)</script>"
            bad = await init_upload("whieda", student, kind="image", name="x.png", mime="image/png", size=len(fake),
                                    allowed_kinds=("image",))
            await put_chunk("whieda", student, bad["media_id"], 0, fake)
            with pytest.raises(MediaError) as not_image:
                await complete_upload("whieda", student, bad["media_id"])
            assert not_image.value.code == "bad_image"
            assert (await upload_status("whieda", student, bad["media_id"]))["status"] == "failed"

            # 3. Видео автора: после «complete» — в очереди перекодирования, а не готово.
            data = clip.read_bytes() if have_ffmpeg else b"\x00" * 300_000
            video = await init_upload(
                "whieda", author, kind="video", name="Урок 1.MOV", mime="video/quicktime", size=len(data),
                allowed_kinds=("image", "file", "video"), chunk_size=64 * 1024,
            )
            for n in range(video["chunks_total"]):
                await put_chunk("whieda", author, video["media_id"], n, data[n * 64 * 1024:(n + 1) * 64 * 1024])
            queued = await complete_upload("whieda", author, video["media_id"])
            assert queued["status"] == "processing" and queued["url"] is None
            assert (await complete_upload("whieda", author, video["media_id"]))["status"] == "processing"
            jobs = await rows(
                "select status, payload, due_at is not null as due from platform_outbox where event_type = %s",
                (EVENT_TYPE,),
            )
            assert jobs == [{"status": "scheduled", "payload": {"media_id": video["media_id"]}, "due": True}]
            # Обычная очередь (и старые сборки на общей базе) задачу не трогает.
            await process_pending_outbox()
            assert [row["status"] for row in await rows("select status from platform_outbox")] == ["scheduled"]

            if have_ffmpeg:
                job = await claim_job()
                assert job is not None and job["payload"]["media_id"] == video["media_id"]
                assert await claim_job() is None  # одна задача за раз
                await process_job(job)
                [media] = await rows(
                    "select status, variants, error from academy_media where media_id = %s::uuid", (video["media_id"],)
                )
                assert media["status"] == "ready", media
                variants = media["variants"]
                mp4 = store.path(variants["mp4_720"])
                assert _probe(mp4) == {"width": 1280, "height": 720, "codec_name": "h264"}
                assert mp4.read_bytes()[4:8] == b"ftyp"
                assert store.path(variants["poster"]).read_bytes()[:3] == b"\xff\xd8\xff"
                assert 3 <= variants["duration_sec"] <= 5
                assert not store.exists(f"whieda/academy/{video['media_id']}/original.mov")  # исходник удалён
                assert [row["status"] for row in await rows("select status from platform_outbox")] == ["done"]
                done_video = await upload_status("whieda", author, video["media_id"])
                assert done_video["url"].startswith(f"/academy-media/{variants['mp4_720']}?u={AUTHOR}")
                assert done_video["poster_url"].startswith(f"/academy-media/{variants['poster']}?u={AUTHOR}")

                # 4. Битое видео — «не получилось», без повторов по кругу.
                junk = b"not a video at all" * 100
                broken = await init_upload("whieda", author, kind="video", name="broken.mp4", mime="video/mp4",
                                           size=len(junk), allowed_kinds=("video",))
                await put_chunk("whieda", author, broken["media_id"], 0, junk)
                await complete_upload("whieda", author, broken["media_id"])
                await process_job(await claim_job())
                failed = await upload_status("whieda", author, broken["media_id"])
                assert failed["status"] == "failed" and failed["error"]
                assert await claim_job() is None

        db.run_with_app(proof)


def _notify_binding():
    from app.telegram.bindings import BotBindingContext
    from app.tenancy import TenantContext

    return BotBindingContext(
        binding_id="whieda-advisor-bot",
        tenant=TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={}),
        bot_token_ref="env:TEST_TOKEN",
        webhook_secret_ref="env:TEST_SECRET",
        bot_username="test_bot",
        status="active",
        processing_mode="core",
        bot_token="test-token",
        webhook_secret="test-secret",
    )


@pytest.mark.integration
def test_homework_submit_return_resubmit_accept_and_notify(monkeypatch, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    _env(monkeypatch, media_dir)
    monkeypatch.setenv("PLATFORM_ACADEMY_NOTIFY_BINDING", "whieda-advisor-bot")
    now = datetime.now(timezone.utc)
    with temporary_database("whieda_academy_homework") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(PEOPLE)
            ids = _seed_course(conn, now)
            # Неделя 3 — после недели 2: проверяем, что домашка держит следующий модуль.
            conn.execute("update academy_modules set unlock = '{\"type\": \"after_prev\"}' where title = 'Неделя 3'")
            db.grant_api_role(conn)

        async def proof() -> None:
            from unittest.mock import AsyncMock, patch

            from app.academy.homework import list_submissions, review_submission, submit_homework
            from app.academy.media import MediaError
            from app.academy.media_service import complete_upload, init_upload, put_chunk
            from app.academy.service import AcademyError, course_outline, load_viewer, set_lesson_done
            from app.db import fetch_all, tenant_connection
            from app.jobs.worker import process_due_notifications

            async def rows(sql: str, params: tuple = ()) -> list[dict]:
                async with tenant_connection("whieda") as conn:
                    return [dict(r) for r in await fetch_all(conn, sql, params)]

            async def notes() -> list[dict]:
                return await rows(
                    "select event_type, status, payload from platform_outbox"
                    " where event_type like 'academy_hw_%%' order by outbox_id"
                )

            def locks(outline) -> dict:
                return {row["slug"]: (row["locked"], row["lock_reason"], row["assignment_status"]) for row in outline["lessons"]}

            student = await load_viewer("whieda", STUDENT)
            author = await load_viewer("whieda", AUTHOR)
            stranger = await load_viewer("whieda", STRANGER)

            async def photo(viewer) -> str:
                png = b"\x89PNG\r\n\x1a\n" + b"photo"
                media = await init_upload("whieda", viewer, kind="image", name="работа.png", mime="image/png",
                                          size=len(png), allowed_kinds=("image",))
                await put_chunk("whieda", viewer, media["media_id"], 0, png)
                await complete_upload("whieda", viewer, media["media_id"])
                return media["media_id"]

            await set_lesson_done("whieda", "akvarel", "a", student, done=True, source="site")
            await set_lesson_done("whieda", "akvarel", "b", student, done=True, source="site")
            work = await photo(student)

            # 1. Пустую сдачу, чужие и неготовые файлы — не принимаем.
            for text, media_ids, code in (
                ("", [], "empty_submission"),
                ("готово", [await photo(stranger)], "bad_media"),
                ("готово", [VIDEO_ID], "bad_media"),
            ):
                with pytest.raises(AcademyError) as refused:
                    await submit_homework("whieda", "akvarel", "c", student, text=text, media_ids=media_ids)
                assert refused.value.code == code
            with pytest.raises(AcademyError) as no_homework:
                await submit_homework("whieda", "akvarel", "a", student, text="x", media_ids=[])
            assert no_homework.value.code == "assignment_not_found"
            with pytest.raises(AcademyError) as locked:
                await submit_homework("whieda", "akvarel", "d", student, text="x", media_ids=[])
            assert locked.value.code == "lesson_locked"

            # 2. Сдача: урок отмечен, домашка на проверке, автору — уведомление.
            sent = await submit_homework("whieda", "akvarel", "c", student, text="Моя заливка", media_ids=[work])
            first_id = sent["submission"]["submission_id"]
            assert sent["submission"]["status"] == "submitted"
            assert sent["submission"]["media"][0]["url"].startswith(f"/academy-media/whieda/academy/{work}/")
            outline = await course_outline("whieda", "akvarel", student, allow_locked=True)
            assert locks(outline)["c"] == (False, None, "submitted")
            assert locks(outline)["d"] == (True, "after_prev", None)
            assert outline["course"]["next_lesson"] is None  # ждём проверки, дальше — замки
            [note] = await notes()
            assert (note["event_type"], note["status"]) == ("academy_hw_submitted", "scheduled")
            payload = note["payload"]
            assert payload["chat_id"] == "7001" and payload["binding_id"] == "whieda-advisor-bot"
            assert payload["text"].startswith("📝 Домашка от Мария — урок «Заливка»")
            assert payload["site_button"]["url"].endswith(f"/academy/author/?view=inbox&submission={first_id}")
            assert payload["site_button"]["telegram_user_id"] == AUTHOR
            # Правка до проверки — та же сдача, второго уведомления нет.
            edited = await submit_homework("whieda", "akvarel", "c", student, text="Моя заливка, v2", media_ids=[work])
            assert edited["submission"]["submission_id"] == first_id
            assert len(await notes()) == 1

            # 3. Входящие автора: имя ученика, урок, текст, фото по ссылке автора.
            [inbox] = await list_submissions("whieda", author, status="submitted")
            assert (inbox["student_name"], inbox["lesson_title"], inbox["course_slug"]) == ("Мария", "Заливка", "akvarel")
            assert inbox["text"] == "Моя заливка, v2"
            assert inbox["media"][0]["url"].startswith(f"/academy-media/whieda/academy/{work}/original.png?u={AUTHOR}")
            assert await list_submissions("whieda", author, status="submitted", course_slug="drugoy") == []
            with pytest.raises(AcademyError) as not_author:
                await list_submissions("whieda", stranger, status="submitted")
            assert not_author.value.code == "not_author"
            with pytest.raises(AcademyError) as foreign_review:
                await review_submission("whieda", first_id, stranger, status="accepted", comment="")
            assert foreign_review.value.code == "not_author"
            with pytest.raises(AcademyError) as no_comment:
                await review_submission("whieda", first_id, author, status="returned", comment=" ")
            assert no_comment.value.code == "comment_required"

            # 4. Вернули с комментарием → ученику уведомление; повторная проверка — нельзя.
            returned = await review_submission("whieda", first_id, author, status="returned", comment="Добавьте тени")
            assert returned["status"] == "returned"
            with pytest.raises(AcademyError) as twice:
                await review_submission("whieda", first_id, author, status="accepted", comment="")
            assert twice.value.code == "already_reviewed"
            back = (await notes())[-1]
            assert back["event_type"] == "academy_hw_reviewed" and back["payload"]["chat_id"] == "9001"
            assert back["payload"]["text"] == "↩️ Домашку вернули — урок «Заливка»:\nДобавьте тени"
            assert back["payload"]["site_button"]["url"].endswith("/academy/?course=akvarel&lesson=c")
            outline = await course_outline("whieda", "akvarel", student, allow_locked=True)
            assert locks(outline)["c"] == (False, None, "returned")
            assert outline["course"]["next_lesson"] == "c"

            # 5. Пересдача — новая строка; приняли → урок завершён, неделя 3 открылась.
            again = await submit_homework("whieda", "akvarel", "c", student, text="С тенями", media_ids=[work])
            second_id = again["submission"]["submission_id"]
            assert second_id != first_id
            assert len([n for n in await notes() if n["event_type"] == "academy_hw_submitted"]) == 2
            await review_submission("whieda", second_id, author, status="accepted", comment="Отлично")
            assert (await notes())[-1]["payload"]["text"].startswith("✅ Домашка принята — урок «Заливка»")
            with pytest.raises(AcademyError) as accepted_already:
                await submit_homework("whieda", "akvarel", "c", student, text="ещё", media_ids=[])
            assert accepted_already.value.code == "already_accepted"
            outline = await course_outline("whieda", "akvarel", student, allow_locked=True)
            assert locks(outline)["c"] == (False, None, "accepted")
            assert locks(outline)["d"] == (False, None, None)
            assert outline["course"]["lessons_done"] == 3
            assert await list_submissions("whieda", author, status="submitted") == []
            history = await list_submissions("whieda", author, status="all")
            assert [item["status"] for item in history] == ["accepted", "returned"]

            # 6. Воркер шлёт уведомления ботом из настройки, кнопка входит на сайт сразу.
            send = AsyncMock(return_value={"ok": True, "message_id": 1})
            login = AsyncMock(side_effect=lambda url, **kw: f"{url}#wwc-login=t{kw['telegram_user_id']}")
            with patch("app.jobs.worker.send_telegram_text", send), patch("app.jobs.worker.with_site_login", login):
                assert await process_due_notifications({"whieda-advisor-bot": _notify_binding()}) == 4
            first_call = send.await_args_list[0].kwargs
            assert first_call["chat_id"] == "7001"
            button = first_call["reply_markup"]["inline_keyboard"][0][0]
            assert button["text"] == "Открыть" and button["url"].endswith(f"submission={first_id}#wwc-login=t{AUTHOR}")
            assert {n["status"] for n in await notes()} == {"done"}

        db.run_with_app(proof)


@pytest.mark.integration
def test_author_builds_publishes_and_follows_a_course(monkeypatch, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    _env(monkeypatch, media_dir)
    with temporary_database("whieda_academy_author") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(PEOPLE)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy import author as authoring
            from app.academy.keys import issue_keys, redeem_key
            from app.academy.media_service import complete_upload, init_upload, put_chunk
            from app.academy.service import AcademyError, course_outline, list_courses, load_viewer, set_lesson_done
            from app.db import fetch_all, tenant_connection

            async def rows(sql: str, params: tuple = ()) -> list[dict]:
                async with tenant_connection("whieda") as conn:
                    return [dict(r) for r in await fetch_all(conn, sql, params)]

            async def upload(viewer, kind, name, mime, data) -> str:
                media = await init_upload("whieda", viewer, kind=kind, name=name, mime=mime, size=len(data),
                                          allowed_kinds=("image", "file", "video"))
                await put_chunk("whieda", viewer, media["media_id"], 0, data)
                await complete_upload("whieda", viewer, media["media_id"])
                return media["media_id"]

            almira = await load_viewer("whieda", AUTHOR)
            student = await load_viewer("whieda", STUDENT)
            stranger = await load_viewer("whieda", STRANGER)
            owner = await load_viewer("whieda", ADMIN)

            # 1. Роль автора: размещение в Академии (или владелец). Чужой — 403.
            with pytest.raises(AcademyError) as nobody:
                await authoring.author_courses("whieda", stranger)
            assert nobody.value.code == "not_author"
            mine = await authoring.author_courses("whieda", almira)
            assert mine["courses"] == [] and mine["shelf"]["active"] is True

            # 2. Курс: адрес из названия, черновик, доступ по ключу.
            course = (await authoring.create_course("whieda", almira, title="Акварель с нуля"))["course"]
            assert (course["slug"], course["status"], course["access_rule"]) == ("akvarel-s-nulya", "draft", "purchase")
            twin = (await authoring.create_course("whieda", almira, title="Акварель с нуля"))["course"]
            assert twin["slug"] == "akvarel-s-nulya-2"
            with pytest.raises(AcademyError) as taken:
                await authoring.create_course("whieda", almira, title="Ещё", slug="akvarel-s-nulya")
            assert taken.value.code == "slug_taken"
            slug = course["slug"]

            # 3. Описание в markdown (очищается), цена в BYN, обложка — только своя картинка.
            cover = await upload(almira, "image", "обложка.png", "image/png", b"\x89PNG\r\n\x1a\ncover")
            foreign = await upload(student, "image", "чужая.png", "image/png", b"\x89PNG\r\n\x1a\nmine")
            updated = (await authoring.update_course("whieda", almira, slug, {
                "subtitle": "6 недель",
                "description_md": "**Курс** для начинающих <script>alert(1)</script>",
                "price": 250, "currency": "BYN", "cover_media_id": cover,
            }))["course"]
            assert updated["description_html"] == "<p><strong>Курс</strong> для начинающих </p>"
            assert updated["price"] == {"amount": 250, "currency": "BYN"}
            assert updated["cover_url"].startswith(f"/academy-media/whieda/academy/{cover}/original.png?u={AUTHOR}")
            for patch_body, code in (
                ({"currency": "DOGE"}, "bad_currency"),
                ({"price": -1}, "bad_price"),
                ({"cover_media_id": foreign}, "bad_media"),
                ({"access_rule": "pro"}, "bad_access_rule"),  # PRO-курс заводит только владелец
                ({"status": "archived"}, "bad_status"),
                ({"title": ""}, "bad_title"),
            ):
                with pytest.raises(AcademyError) as refused:
                    await authoring.update_course("whieda", almira, slug, patch_body)
                assert refused.value.code == code, patch_body

            # 4. Модули с правилами открытия; уроки: видео (ещё перекодируется), файл, домашка.
            week1 = (await authoring.create_module("whieda", almira, slug, {"title": "Неделя 1"}))["module"]
            week2 = (await authoring.create_module("whieda", almira, slug, {
                "title": "Неделя 2", "unlock": {"type": "after_prev"},
            }))["module"]
            with pytest.raises(AcademyError) as bad_rule:
                await authoring.create_module("whieda", almira, slug, {"title": "X", "unlock": {"type": "someday"}})
            assert bad_rule.value.code == "bad_unlock"
            video = await upload(almira, "video", "урок.mp4", "video/mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
            workbook = await upload(almira, "file", "Тетрадь.pdf", "application/pdf", b"%PDF-1.4 workbook")
            intro = (await authoring.create_lesson("whieda", almira, slug, week1["module_id"], {
                "title": "Знакомство с красками",
                "body_md": f"Смотрите схему:\n\n![Схема](media:{cover})\n\n<img src=x onerror=alert(1)>",
                "video": {"media_id": video},
                "files": [workbook],
            }))["lesson"]
            assert intro["slug"] == "znakomstvo-s-kraskami" and intro["status"] == "published"
            assert "onerror" not in intro["body_html"] and f'src="media:{cover}"' in intro["body_html"]
            assert intro["video"] == {"media_id": video, "status": "processing"}
            practice = (await authoring.create_lesson("whieda", almira, slug, week2["module_id"], {
                "title": "Практика",
                "assignment": {"prompt_md": "Пришлите **фото**", "required": True},
            }))["lesson"]
            assert practice["assignment"] == {
                "prompt_md": "Пришлите **фото**", "prompt_html": "<p>Пришлите <strong>фото</strong></p>", "required": True,
            }
            for lesson_body, code in (
                ({"title": "Эфир", "kind": "live", "live_url": "http://zoom.example/x"}, "bad_live_url"),
                ({"title": "Чужое", "files": [foreign]}, "bad_media"),
                ({"title": "Чужое видео", "video": {"media_id": foreign}}, "bad_media"),
                ({"title": "Видео", "video": {"provider": "dailymotion", "id": "x"}}, "bad_video"),
            ):
                with pytest.raises(AcademyError) as refused:
                    await authoring.create_lesson("whieda", almira, slug, week1["module_id"], lesson_body)
                assert refused.value.code == code, lesson_body
            live = (await authoring.create_lesson("whieda", almira, slug, week1["module_id"], {
                "title": "Эфир недели", "kind": "live", "live_at": "2026-10-09T19:00", "live_url": "https://zoom.us/j/1",
                "video": {"provider": "youtube", "id": "dQw4w9WgXcQ"},
            }))["lesson"]
            assert (live["kind"], live["live_at"]) == ("live", "2026-10-09T16:00:00+00:00")

            structure = await authoring.author_course("whieda", almira, slug)
            assert [m["title"] for m in structure["modules"]] == ["Неделя 1", "Неделя 2"]
            assert [(l["title"], l["position"]) for m in structure["modules"] for l in m["lessons"]] == [
                ("Знакомство с красками", 1), ("Эфир недели", 2), ("Практика", 3),
            ]
            # Порядок: модули и уроки кнопками вверх/вниз (весь список целиком).
            await authoring.reorder_modules("whieda", almira, slug, [week2["module_id"], week1["module_id"]])
            moved = await authoring.author_course("whieda", almira, slug)
            assert [l["title"] for m in moved["modules"] for l in m["lessons"]][0] == "Практика"
            await authoring.reorder_modules("whieda", almira, slug, [week1["module_id"], week2["module_id"]])
            await authoring.reorder_lessons("whieda", almira, slug, week1["module_id"], [live["lesson_id"], intro["lesson_id"]])
            ordered = await authoring.author_course("whieda", almira, slug)
            assert [(l["title"], l["position"]) for m in ordered["modules"] for l in m["lessons"]] == [
                ("Эфир недели", 1), ("Знакомство с красками", 2), ("Практика", 3),
            ]
            with pytest.raises(AcademyError) as wrong_order:
                await authoring.reorder_lessons("whieda", almira, slug, week1["module_id"], [live["lesson_id"]])
            assert wrong_order.value.code == "bad_order"

            # 5. Черновик ученику не виден; публикация — при оплаченном размещении.
            assert [c["slug"] for c in await list_courses("whieda", student)] == []
            published = (await authoring.update_course("whieda", almira, slug, {"status": "published"}))["course"]
            assert published["status"] == "published"
            async with tenant_connection("whieda") as conn:
                await conn.execute("update academy_shelf set status = 'suspended'")
            with pytest.raises(AcademyError) as lapsed:
                await authoring.update_course("whieda", almira, twin["slug"], {"status": "published"})
            assert lapsed.value.code == "shelf_inactive"
            # Черновик править можно и без размещения; владелец публикует сам.
            await authoring.update_course("whieda", almira, twin["slug"], {"subtitle": "черновик"})
            assert (await authoring.update_course("whieda", owner, twin["slug"], {"status": "published"}))["course"]["status"] == "published"
            async with tenant_connection("whieda") as conn:
                await conn.execute("update academy_shelf set status = 'active'")

            # 6. Ключ автора → ученик видит модули с замком и проходит урок.
            issued = await issue_keys("whieda", slug, "almira", 1)
            assert (await redeem_key("whieda", issued.codes[0], STUDENT)).status == "opened"
            outline = await course_outline("whieda", slug, student, allow_locked=True)
            assert outline["course"]["price"] == {"amount": 250, "currency": "BYN"}
            assert outline["course"]["description_html"] == "<p><strong>Курс</strong> для начинающих </p>"
            locks = {row["title"]: (row["locked"], row["lock_reason"]) for row in outline["lessons"]}
            assert locks == {
                "Эфир недели": (False, None), "Знакомство с красками": (False, None), "Практика": (True, "after_prev"),
            }

            # 7. Правило урока поверх модуля, удаление: модуль с уроками — нет, урок — в архив.
            await authoring.update_lesson("whieda", almira, slug, practice["lesson_id"], {"unlock": {"type": "open"}})
            outline = await course_outline("whieda", slug, student, allow_locked=True)
            assert next(r for r in outline["lessons"] if r["title"] == "Практика")["locked"] is False
            with pytest.raises(AcademyError) as not_empty:
                await authoring.delete_module("whieda", almira, slug, week2["module_id"])
            assert not_empty.value.code == "module_not_empty"
            await authoring.delete_lesson("whieda", almira, slug, live["lesson_id"])
            empty = (await authoring.create_module("whieda", almira, slug, {"title": "Пустой"}))["module"]
            await authoring.delete_module("whieda", almira, slug, empty["module_id"])
            outline = await course_outline("whieda", slug, student, allow_locked=True)
            assert [(r["title"], r["position"]) for r in outline["lessons"]] == [
                ("Знакомство с красками", 1), ("Практика", 2),
            ]
            assert await rows("select status from academy_lessons where title = 'Эфир недели'") == [{"status": "archived"}]

            # 8. Ученики: прогресс, последняя активность, домашки на проверке.
            await set_lesson_done("whieda", slug, "znakomstvo-s-kraskami", student, done=True, source="site")
            [learner] = await authoring.course_students("whieda", almira, slug)
            assert (learner["name"], learner["username"], learner["progress_pct"]) == ("Мария", "masha_s", 50)
            assert (learner["lessons_complete"], learner["lessons_total"], learner["submissions_pending"]) == (1, 2, 0)
            assert learner["access"] == "key" and learner["last_activity_at"] is not None

            # 9. Чужой курс не правится; предпросмотр markdown — тем же очистителем.
            with pytest.raises(AcademyError) as foreign_course:
                await authoring.update_course("whieda", stranger, slug, {"title": "Моё"})
            assert foreign_course.value.code == "not_author"
            assert await authoring.preview_markdown("whieda", almira, "# Заголовок\n<script>x</script>") == "<h1>Заголовок</h1>\n"
            listing = await authoring.author_courses("whieda", almira)
            assert [(c["slug"], c["students"], c["lessons_total"]) for c in listing["courses"]] == [
                ("akvarel-s-nulya", 1, 2), ("akvarel-s-nulya-2", 0, 0),
            ]

        db.run_with_app(proof)
