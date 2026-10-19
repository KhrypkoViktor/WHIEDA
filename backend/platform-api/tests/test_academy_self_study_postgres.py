"""Без оплаты — самостоятельно (Виктор, 17.10.2026) на настоящем PostgreSQL.

Альберт купил «Онлайн-старт», «Алхимия капитала» открыта ему бесплатно (``free_for``) — и его
домашка с вопросом ушли Виктору на проверку. Договорённость: без оплаты курс проходят сами,
проверка автора — только у оплативших.

  * ``free_for`` (участник курса, ``course``; поставил приложение, ``unlock`` — V39) и подарок
    (source gift): ``self_study`` в курсе и в задании, обязательность задания снята, домашку и
    вопрос сдать нельзя (403 ``self_study``), урок засчитывает «Сделал»;
  * купивший этот курс сдаёт домашку как раньше, обязательная домашка держит урок.
"""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import temporary_database
from tests.test_academy_threads_postgres import _env

AUTHOR, ONLINE, GIFTED, BUYER, UNLOCKED = 7001, 9104, 9201, 9106, 9202

SEED = f"""
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id) values
  ('viktor', 'whieda', 'Виктор', {AUTHOR}, '{AUTHOR}'),
  ('albert', 'whieda', 'Альберт', {ONLINE}, '{ONLINE}'),
  ('lubov', 'whieda', 'Любовь', {GIFTED}, '{GIFTED}'),
  ('buyer', 'whieda', 'Купил', {BUYER}, '{BUYER}'),
  ('unlocked', 'whieda', 'Поставил приложение', {UNLOCKED}, '{UNLOCKED}');
insert into academy_courses (tenant_id, slug, title, access_rule, status)
values ('whieda', 'online-start-4w', 'Онлайн-старт: 4 недели практики', 'purchase', 'published');
insert into academy_courses (tenant_id, slug, title, access_rule, author_actor_id, status, kind, free_for)
values ('whieda', 'mini-dhando', 'Алхимия капитала', 'purchase', 'viktor', 'published', 'mini',
        '{{"courses": ["online-start-4w"], "unlock": {{"app": true}}}}');
insert into academy_app_installs (tenant_id, telegram_user_id, platform) values ('whieda', {UNLOCKED}, 'android');
insert into academy_lessons (tenant_id, course_id, slug, position, title, body_html)
select 'whieda', course_id, s, p, t, '<p>Урок</p>' from academy_courses,
  (values ('urok-1', 1, 'Бизнес без риска'), ('urok-2', 2, 'Второй')) as l(s, p, t)
where slug = 'mini-dhando';
insert into academy_assignments (tenant_id, lesson_id, prompt_md, prompt_html, required)
select 'whieda', lesson_id, 'Разбор своей идеи', '<p>Разбор своей идеи</p>', true
from academy_lessons where slug = 'urok-1';
insert into academy_access (tenant_id, course_id, telegram_user_id, source, payment_ref)
select 'whieda', course_id, {ONLINE}, 'purchase', 'shop:1' from academy_courses where slug = 'online-start-4w'
union all
select 'whieda', course_id, {GIFTED}, 'gift', 'owner-17.10' from academy_courses where slug = 'mini-dhando'
union all
select 'whieda', course_id, {BUYER}, 'purchase', 'shop:2' from academy_courses where slug = 'mini-dhando';
"""


def _lesson(outline: object, slug: str) -> dict:
    """Урок в плане курса, где бы он ни лежал (модули, уроки)."""
    found: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("slug") == slug and "complete" in node:
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(outline)
    assert found, slug
    return found[0]


@pytest.mark.integration
def test_free_and_gift_access_is_self_study_buyer_hands_in(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    with temporary_database("whieda_self_study") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy.homework import submit_homework
            from app.academy.service import AcademyError, course_outline, lesson_detail, load_viewer, set_lesson_done
            from app.academy.threads import post_student_message
            from app.db import fetch_all, tenant_connection

            albert, lubov, buyer, unlocked = [
                await load_viewer("whieda", uid) for uid in (ONLINE, GIFTED, BUYER, UNLOCKED)
            ]

            for viewer in (albert, lubov, unlocked):
                detail = await lesson_detail("whieda", "mini-dhando", "urok-1", viewer)
                assignment = detail["lesson"]["assignment"]
                assert (assignment["self_study"], assignment["required"]) == (True, False)
                assert detail["lesson"]["assignment"]["prompt_html"] == "<p>Разбор своей идеи</p>"
                with pytest.raises(AcademyError) as refused:
                    await submit_homework("whieda", "mini-dhando", "urok-1", viewer, text="Моя идея", media_ids=[])
                assert (refused.value.status, refused.value.code) == (403, "self_study")
                with pytest.raises(AcademyError) as asked:
                    await post_student_message("whieda", "mini-dhando", "urok-1", viewer, text="Вопрос", media_ids=[])
                assert asked.value.code == "self_study"
                # «Сделал» засчитывает урок: обязательная домашка его не держит.
                await set_lesson_done("whieda", "mini-dhando", "urok-1", viewer, done=True, source="site")
                outline = await course_outline("whieda", "mini-dhando", viewer)
                assert outline["course"]["self_study"] is True
                assert _lesson(outline, "urok-1")["complete"] is True

            # Причина открытия видна в курсе: участник курса, подарок (причины нет) и «приложение» (V39).
            reasons = [
                (await course_outline("whieda", "mini-dhando", viewer))["course"]["free_reason"]
                for viewer in (albert, lubov, unlocked)
            ]
            assert reasons == ["course", None, "unlock"]

            detail = await lesson_detail("whieda", "mini-dhando", "urok-1", buyer)
            assert (detail["lesson"]["assignment"]["self_study"], detail["lesson"]["assignment"]["required"]) == (False, True)
            await set_lesson_done("whieda", "mini-dhando", "urok-1", buyer, done=True, source="site")
            assert _lesson(await course_outline("whieda", "mini-dhando", buyer), "urok-1")["complete"] is False
            sent = await submit_homework("whieda", "mini-dhando", "urok-1", buyer, text="Моя идея", media_ids=[])
            assert sent["assignment_status"] == "submitted"

            async with tenant_connection("whieda") as conn:
                rows = await fetch_all(conn, "select telegram_user_id from academy_submissions")
            assert [r["telegram_user_id"] for r in rows] == [BUYER]

        db.run_with_app(proof)
