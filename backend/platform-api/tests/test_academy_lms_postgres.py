"""Academy v2 «LMS» on real PostgreSQL (V19, 02.10.2026).

  * the migration runs twice; old access rows start when they were granted;
  * new tables are tenant-isolated (RLS) and the API role reads them;
  * one active homework per (lesson, student); a returned one can be resubmitted.
"""

from __future__ import annotations

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
