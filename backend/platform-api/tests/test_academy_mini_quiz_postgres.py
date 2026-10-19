"""Academy V35 on real PostgreSQL: mini-courses.

  * the migration runs twice, the attempts table is tenant-isolated;
  * a ``purchase`` course with ``free_for`` is open to the club (active or in grace,
    not expired) and to students of the named course (active access, not revoked),
    ``free_reason`` goes out and the «Купить» card does not;
  * the lesson's test: built from a ```quiz block, loaded with ``--kind mini`` and the
    free-for flags, shown without answers, scored with hints; only a passed attempt
    marks the lesson done, «Сделал» by hand is 409 ``quiz_required``;
  * the author's path: a ```quiz block in ``body_md`` goes to ``quiz_json``, ``quiz: null``
    removes it; ``kind`` and ``free_for`` are the owner's.
"""

from __future__ import annotations

import json
from pathlib import Path

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, SQL_DIR, temporary_database

V35 = "platform_academy_mini_quiz_v35.sql"
SHOP_ITEM = SQL_DIR / "data" / "shop_item_mini_dhando.sql"
SECRET = "q" * 40
ADMIN, AUTHOR = 1, 7001
CLUB, GRACE, EXPIRED, ONLINE, REVOKED, BUYER, STRANGER = 9101, 9102, 9103, 9104, 9105, 9106, 9107
PNG = b"\x89PNG\r\n\x1a\n" + b"scheme"

PEOPLE = f"""
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id, telegram_username) values
  ('almira', 'whieda', 'Альмира', {AUTHOR}, '{AUTHOR}', 'almira_art'),
  ('club', 'whieda', 'В клубе', {CLUB}, '{CLUB}', null),
  ('grace', 'whieda', 'Клуб в грейсе', {GRACE}, '{GRACE}', null),
  ('expired', 'whieda', 'Клуб истёк', {EXPIRED}, '{EXPIRED}', null),
  ('online', 'whieda', 'Онлайн-старт', {ONLINE}, '{ONLINE}', null),
  ('revoked', 'whieda', 'Доступ отозван', {REVOKED}, '{REVOKED}', null),
  ('buyer', 'whieda', 'Купил', {BUYER}, '{BUYER}', null);
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode) values
  ('almira', 'whieda', 'almira', 'named'),
  ('clubber', 'whieda', 'club', 'named'),
  ('gracer', 'whieda', 'grace', 'named'),
  ('oldclub', 'whieda', 'expired', 'named');
insert into academy_shelf (tenant_id, actor_id, paid_until, status)
values ('whieda', 'almira', now() + interval '60 days', 'active');
insert into partner_subscriptions (tenant_id, ref_code, paid_until) values
  ('whieda', 'clubber', now() + interval '60 days'),
  ('whieda', 'gracer', now() + interval '60 days'),
  ('whieda', 'oldclub', now() + interval '60 days');
-- Грейс клуба — 3 дня после paid_until (subscriptions.GRACE_PERIOD), как в кабинете /me/.
insert into partner_product_access (tenant_id, ref_code, product_code, paid_until) values
  ('whieda', 'clubber', 'club_subscription', now() + interval '30 days'),
  ('whieda', 'gracer', 'club_subscription', now() - interval '1 day'),
  ('whieda', 'oldclub', 'club_subscription', now() - interval '10 days');
insert into academy_courses (tenant_id, slug, title, access_rule, status)
values ('whieda', 'online-start-4w', 'Онлайн-старт: 4 недели практики', 'purchase', 'published');
insert into academy_access (tenant_id, course_id, telegram_user_id, source, payment_ref, revoked_at)
select 'whieda', course_id, {ONLINE}, 'purchase', 'shop:1', null from academy_courses where slug = 'online-start-4w'
union all
select 'whieda', course_id, {REVOKED}, 'purchase', 'shop:2', now() from academy_courses where slug = 'online-start-4w';
"""

QUIZ = {
    "pass": 2,
    "questions": [
        {"q": "Что защищало Пателей от разорения?", "ref": "Разбор, семья Пателей",
         "options": [
             {"text": "Высокие цены", "correct": False, "hint": "Не совсем. Перечитайте: Разбор, семья Пателей."},
             {"text": "Низкие постоянные расходы", "correct": True, "hint": "Верно. Чем меньше должны, тем труднее разорить."},
         ]},
        {"q": "Чем помог возврат самолёта?", "ref": "Разбор, Брэнсон",
         "options": [
             {"text": "Ничем", "correct": False, "hint": "Не совсем. Перечитайте: Разбор, Брэнсон."},
             {"text": "Худший исход стал дешёвым", "correct": True, "hint": ""},
             {"text": "Самолёт стал бесплатным", "correct": False, "hint": "Не совсем. Аренда не бесплатна."},
         ]},
    ],
}


def _env(monkeypatch, media_dir) -> None:
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", str(ADMIN))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(ADMIN))
    monkeypatch.setenv("PLATFORM_MEDIA_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("PLATFORM_ACADEMY_MEDIA_DIR", str(media_dir))
    monkeypatch.setenv("PLATFORM_TELEGRAM_BOT_USERNAME", "WHIEDA_Advisor_bot")
    monkeypatch.delenv("PLATFORM_ACADEMY_MEDIA_VIA_API", raising=False)
    monkeypatch.delenv("PLATFORM_ACADEMY_NOTIFY_BINDING", raising=False)
    monkeypatch.delenv("PLATFORM_SCHEDULED_NOTIFY_BINDINGS", raising=False)


def _module(path: Path, name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bundle(folder: Path) -> dict:
    """Мини-курс из Obsidian-папки: урок без теста (с картинкой) и урок с тестом после чек-листа."""
    pytest.importorskip("markdown")
    (folder / "img").mkdir(parents=True)
    (folder / "img" / "shema.png").write_bytes(PNG)
    (folder / "00.md").write_text(
        "# Как проходить курс\n\nПрочитайте и сделайте упражнение.\n\n![Схема](img/shema.png)\n", encoding="utf-8"
    )
    (folder / "01.md").write_text(
        "# Орёл или решка\n\n## Разбор\n\nТекст.\n\n## Чек-лист\n\n- [ ] Записал «орёл»\n\n"
        "## Промпт\n\n```\nТы — консультант.\n```\n\n"
        f"## Тест\n\n```quiz\n{json.dumps(QUIZ, ensure_ascii=False, indent=1)}\n```\n",
        encoding="utf-8",
    )
    (folder / "_academy_course.json").write_text(
        json.dumps(
            {
                "slug": "mini-dhando", "title": "Алхимия капитала", "access": "purchase",
                "modules": [{"title": "Мини-курс", "lessons": [
                    {"file": "00.md", "slug": "kak-prokhodit", "short": "Как проходить"},
                    {"file": "01.md", "slug": "orel-reshka", "short": "Орёл или решка"},
                ]}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    scripts = Path(__file__).resolve().parents[1] / "scripts" / "academy"
    return _module(scripts / "build_bundle.py", "academy_build_bundle_v35").build(folder)


@pytest.mark.integration
def test_v35_twice_and_attempts_are_tenant_isolated():
    assert V35 in MIGRATIONS
    sql = (SQL_DIR / V35).read_text(encoding="utf-8")
    assert "$" not in sql and "$" not in SHOP_ITEM.read_text(encoding="utf-8")  # n8n режет знак доллара
    with temporary_database("whieda_academy_v35") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, MIGRATIONS, twice=True)
            course_id = conn.execute(
                "insert into academy_courses (tenant_id, slug, title, access_rule, status, kind, free_for)"
                " values ('whieda', 'mini', 'Мини', 'purchase', 'published', 'mini', '{\"club\": true}')"
                " returning course_id"
            ).fetchone()[0]
            lesson_id = conn.execute(
                "insert into academy_lessons (tenant_id, course_id, slug, position, title, body_html, quiz_json)"
                " values ('whieda', %s, 'u', 1, 'У', '<p>u</p>', %s) returning lesson_id",
                (course_id, json.dumps({"pass": 1, "questions": []})),
            ).fetchone()[0]
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("update academy_courses set free_for = '[1]' where course_id = %s", (course_id,))
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("update academy_lessons set quiz_json = '{\"pass\": 1}' where lesson_id = %s", (lesson_id,))
            conn.execute(
                "insert into academy_quiz_attempts (tenant_id, lesson_id, telegram_user_id, answers, score, passed)"
                " values ('whieda', %s, 9001, '{\"q1\": \"a\"}', 1, true)",
                (lesson_id,),
            )
            rls = conn.execute("select relrowsecurity from pg_class where relname = 'academy_quiz_attempts'").fetchone()
            assert rls == (True,)
            conn.execute(SHOP_ITEM.read_text(encoding="utf-8"))
            conn.execute(SHOP_ITEM.read_text(encoding="utf-8"))  # повторно — без дублей и ошибок
            item = conn.execute(
                "select kind, category, title, price_wusd_minor, price_rub_minor, price_club_wusd_minor, course_slug,"
                " confirmer, status, sort_order from shop_items where code = 'mini-dhando'"
            ).fetchall()
            assert item == [
                ("course", "courses", "Мини-курс: Алхимия капитала", 500, 50000, None, "mini-dhando", "owner", "pilot", 20)
            ]
            db.grant_api_role(conn)
        with psycopg.connect(db.api_dsn, autocommit=True) as api:
            api.execute("select set_config('app.tenant_id', 'other', false)")
            assert api.execute("select count(*) from academy_quiz_attempts").fetchone() == (0,)
            api.execute("select set_config('app.tenant_id', 'whieda', false)")
            assert api.execute("select count(*) from academy_quiz_attempts").fetchone() == (1,)


@pytest.mark.integration
def test_free_for_the_club_and_course_students_and_the_lesson_test(monkeypatch, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    _env(monkeypatch, media_dir)
    bundle = _bundle(tmp_path / "obsidian")
    loader = _module(Path(__file__).resolve().parents[1] / "scripts" / "academy" / "load_bundle.py", "academy_load_v35")
    with temporary_database("whieda_academy_mini") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(PEOPLE)
            db.grant_api_role(conn)
        # Команда загрузки из ТЗ: мини-курс, черновик, покупка, бесплатно клубу и «Онлайн-старту».
        loaded = loader.load(
            "whieda", bundle, status="draft", access="purchase", kind="mini",
            free_for_club=True, free_for_courses=["online-start-4w", "online-start-4w"], dsn=db.admin_dsn,
        )
        assert (loaded["kind"], loaded["status"], loaded["access_rule"], loaded["quizzes"]) == ("mini", "draft", "purchase", 1)
        assert loaded["free_for"] == {"club": True, "courses": ["online-start-4w"]}
        # Повторная загрузка без флагов: рубрика и free_for остаются.
        reloaded = loader.load("whieda", bundle, dsn=db.admin_dsn)
        assert (reloaded["kind"], reloaded["free_for"]) == ("mini", {"club": True, "courses": ["online-start-4w"]})
        assert loader.publish("whieda", "mini-dhando", dsn=db.admin_dsn)["status"] == "published"
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            stored = conn.execute("select slug, body_html, quiz_json from academy_lessons order by position").fetchall()
            conn.execute(SHOP_ITEM.read_text(encoding="utf-8"))
            conn.execute("update shop_items set status = 'published' where code = 'mini-dhando'")
            conn.execute(
                "insert into academy_access (tenant_id, course_id, telegram_user_id, source, payment_ref)"
                " select 'whieda', course_id, %s, 'purchase', 'shop:3' from academy_courses where slug = 'mini-dhando'",
                (BUYER,),
            )
            [(media_id,)] = conn.execute("select media_id::text from academy_media").fetchall()
        assert stored[0][2] is None
        assert [q["id"] for q in stored[1][2]["questions"]] == ["q1", "q2"]
        assert "quiz" not in stored[1][1] and "Не совсем" not in stored[1][1]
        assert "<pre><code>Ты — консультант." in stored[1][1]

        async def proof() -> None:
            from app.academy.media import MediaError
            from app.academy.media_service import media_url
            from app.academy.service import (
                AcademyError,
                course_outline,
                lesson_detail,
                list_courses,
                load_viewer,
                set_lesson_done,
                submit_quiz,
            )
            from app.db import fetch_all, tenant_connection

            async def card(user: int) -> dict:
                courses = {c["slug"]: c for c in await list_courses("whieda", await load_viewer("whieda", user))}
                return courses["mini-dhando"]

            # 1. Каталог: рубрика, причина бесплатного доступа, кнопка «Купить» — только без неё.
            club = await card(CLUB)
            assert (club["kind"], club["locked"], club["free_reason"]) == ("mini", False, "club")
            assert "purchase" not in club and "author_contact" not in club
            assert (await card(GRACE))["free_reason"] == "club"  # грейс клуба — ещё открыт
            online = await card(ONLINE)
            assert (online["locked"], online["free_reason"]) == (False, "course")
            for user in (EXPIRED, REVOKED, STRANGER):
                locked = await card(user)
                assert (locked["locked"], locked["lock_reason"], locked["free_reason"]) == (True, "purchase_required", None)
                assert locked["purchase"]["code"] == "mini-dhando" and locked["purchase"]["prices"]["wusd"] == 5
            bought = await card(BUYER)
            assert (bought["locked"], bought["free_reason"]) == (False, None)  # открыт покупкой — причины нет
            admin = await card(ADMIN)
            assert (admin["locked"], admin["free_reason"]) == (False, None)

            # 2. Детали курса — те же поля.
            club_viewer = await load_viewer("whieda", CLUB)
            stranger = await load_viewer("whieda", STRANGER)
            outline = await course_outline("whieda", "mini-dhando", club_viewer, allow_locked=True)
            assert (outline["course"]["kind"], outline["course"]["free_reason"], outline["course"]["locked"]) == ("mini", "club", False)
            assert "purchase" not in outline["course"]
            assert [(l["slug"], l["has_quiz"]) for l in outline["lessons"]] == [("kak-prokhodit", False), ("orel-reshka", True)]
            closed = await course_outline("whieda", "mini-dhando", stranger, allow_locked=True)
            assert closed["course"]["free_reason"] is None and closed["course"]["purchase"]["code"] == "mini-dhando"
            with pytest.raises(AcademyError) as no_access:
                await submit_quiz("whieda", "mini-dhando", "orel-reshka", stranger, answers={"q1": "b"})
            assert no_access.value.code == "purchase_required"

            # Картинка урока открытого бесплатно курса: участнику клуба — да, чужому — нет.
            assert (await media_url("whieda", media_id, club_viewer))["url"].startswith("/academy-media/")
            with pytest.raises(MediaError):
                await media_url("whieda", media_id, stranger)

            # 3. Урок с тестом: без верных ответов и подсказок, состояние — ни одной попытки.
            detail = await lesson_detail("whieda", "mini-dhando", "orel-reshka", club_viewer)
            quiz = detail["lesson"]["quiz"]
            assert quiz["pass"] == 2 and [q["id"] for q in quiz["questions"]] == ["q1", "q2"]
            assert quiz["questions"][1]["options"] == [
                {"id": "a", "text": "Ничем"}, {"id": "b", "text": "Худший исход стал дешёвым"},
                {"id": "c", "text": "Самолёт стал бесплатным"},
            ]
            assert quiz["questions"][0]["ref"] == "Разбор, семья Пателей"
            dumped = json.dumps(detail, ensure_ascii=False)
            assert "correct" not in dumped and "hint" not in dumped and "Не совсем" not in dumped
            assert detail["lesson"]["quiz_state"] == {"passed": False, "best": 0, "attempts": 0, "retry_at": None}
            plain = await lesson_detail("whieda", "mini-dhando", "kak-prokhodit", club_viewer)
            assert (plain["lesson"]["quiz"], plain["lesson"]["quiz_state"]) == (None, None)

            # 4. «Сделал» вручную на уроке с тестом — 409; снять отметку можно всегда.
            with pytest.raises(AcademyError) as manual:
                await set_lesson_done("whieda", "mini-dhando", "orel-reshka", club_viewer, done=True, source="site")
            assert (manual.value.status, manual.value.code) == (409, "quiz_required")
            assert (await set_lesson_done("whieda", "mini-dhando", "orel-reshka", club_viewer, done=False, source="site"))["ok"]
            # Урок без теста отмечается как раньше.
            await set_lesson_done("whieda", "mini-dhando", "kak-prokhodit", club_viewer, done=True, source="site")

            async def progress_rows() -> list:
                async with tenant_connection("whieda") as conn:
                    return await fetch_all(
                        conn,
                        "select l.slug, p.source from academy_progress p join academy_lessons l on l.lesson_id = p.lesson_id"
                        " where p.telegram_user_id = %s order by l.position",
                        (CLUB,),
                    )

            # 5. Непройденная попытка: подсказки, неотвеченный — неверный, прогресса нет.
            failed = await submit_quiz("whieda", "mini-dhando", "orel-reshka", club_viewer, answers={"q1": "a"})
            assert (failed["score"], failed["pass"], failed["passed"], failed["done"]) == (0, 2, False, False)
            assert failed["results"] == [
                {"id": "q1", "answer": "a", "correct": False, "hint": "Не совсем. Перечитайте: Разбор, семья Пателей."},
                {"id": "q2", "answer": None, "correct": False, "hint": ""},
            ]
            assert [row["slug"] for row in await progress_rows()] == ["kak-prokhodit"]
            half = await submit_quiz("whieda", "mini-dhando", "orel-reshka", club_viewer, answers={"q1": "b", "q2": "c"})
            assert (half["score"], half["passed"], half["quiz_state"]) == (1, False, {"passed": False, "best": 1, "attempts": 2, "retry_at": None})
            assert [row["slug"] for row in await progress_rows()] == ["kak-prokhodit"]

            # 6. Сданная попытка: урок отмечен (source=site), курс пройден; повтор — без дублей.
            passed = await submit_quiz("whieda", "mini-dhando", "orel-reshka", club_viewer, answers={"q1": "b", "q2": "b"})
            assert (passed["score"], passed["passed"], passed["done"]) == (2, True, True)
            assert passed["results"][1] == {"id": "q2", "answer": "b", "correct": True, "hint": ""}
            assert (passed["lessons_done"], passed["lessons_total"], passed["next_lesson"]) == (2, 2, None)
            again = await submit_quiz("whieda", "mini-dhando", "orel-reshka", club_viewer, answers={"q1": "b", "q2": "b"})
            assert again["quiz_state"] == {"passed": True, "best": 2, "attempts": 4, "retry_at": None}
            assert [(row["slug"], row["source"]) for row in await progress_rows()] == [
                ("kak-prokhodit", "site"), ("orel-reshka", "site"),
            ]
            # После сданного теста ручная отметка снова разрешена (снять и вернуть).
            await set_lesson_done("whieda", "mini-dhando", "orel-reshka", club_viewer, done=False, source="site")
            await set_lesson_done("whieda", "mini-dhando", "orel-reshka", club_viewer, done=True, source="bot")
            assert (await card(CLUB))["lessons_done"] == 2
            detail = await lesson_detail("whieda", "mini-dhando", "orel-reshka", club_viewer)
            assert detail["lesson"]["quiz_state"] == {"passed": True, "best": 2, "attempts": 4, "retry_at": None}
            assert detail["lesson"]["complete"] is True
            async with tenant_connection("whieda") as conn:
                attempts = await fetch_all(
                    conn,
                    "select answers, score, passed from academy_quiz_attempts where telegram_user_id = %s order by created_at",
                    (CLUB,),
                )
            assert [(a["score"], a["passed"]) for a in attempts] == [(0, False), (1, False), (2, True), (2, True)]
            assert attempts[0]["answers"] == {"q1": "a"}

            # 7. Отметка урока с тестом, поставленная раньше теста, курс не завершает.
            online_viewer = await load_viewer("whieda", ONLINE)
            async with tenant_connection("whieda") as conn:
                await conn.execute(
                    "insert into academy_progress (tenant_id, telegram_user_id, lesson_id, source)"
                    " select 'whieda', %s, lesson_id, 'bot' from academy_lessons where slug = 'orel-reshka'",
                    (ONLINE,),
                )
            early = await course_outline("whieda", "mini-dhando", online_viewer, allow_locked=True)
            row = next(l for l in early["lessons"] if l["slug"] == "orel-reshka")
            assert (row["done"], row["complete"], early["course"]["lessons_done"]) == (True, False, 0)
            assert (await card(ONLINE))["lessons_done"] == 0

        db.run_with_app(proof)


@pytest.mark.integration
def test_author_quiz_block_kind_and_free_for(monkeypatch, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    _env(monkeypatch, media_dir)
    with temporary_database("whieda_academy_quiz_author") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(PEOPLE)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy import author as authoring
            from app.academy.service import AcademyError, load_viewer

            owner = await load_viewer("whieda", ADMIN)
            almira = await load_viewer("whieda", AUTHOR)
            created = await authoring.create_course("whieda", almira, title="Мини-курс Альмиры", slug="mini-almira")
            assert created["course"]["kind"] == "course" and created["course"]["free_for"] is None
            # Рубрика и бесплатный доступ — только владелец.
            for patch in ({"kind": "mini"}, {"free_for": {"club": True}}):
                with pytest.raises(AcademyError) as refused:
                    await authoring.update_course("whieda", almira, "mini-almira", patch)
                assert (refused.value.status, refused.value.code) == (403, "not_owner")
            with pytest.raises(AcademyError) as bad_kind:
                await authoring.update_course("whieda", owner, "mini-almira", {"kind": "webinar"})
            assert bad_kind.value.code == "bad_kind"
            with pytest.raises(AcademyError) as bad_free:
                await authoring.update_course("whieda", owner, "mini-almira", {"free_for": {"courses": ["Не slug"]}})
            assert bad_free.value.code == "bad_free_for"
            updated = await authoring.update_course(
                "whieda", owner, "mini-almira",
                {"kind": "mini", "free_for": {"club": True, "courses": ["online-start-4w", "online-start-4w"]}},
            )
            assert updated["course"]["kind"] == "mini"
            assert updated["course"]["free_for"] == {"club": True, "courses": ["online-start-4w"]}
            cleared = await authoring.update_course("whieda", owner, "mini-almira", {"free_for": {"club": False}})
            assert cleared["course"]["free_for"] is None

            module = (await authoring.create_module("whieda", almira, "mini-almira", {"title": "Мини-курс"}))["module"]
            block = json.dumps(QUIZ, ensure_ascii=False)
            body = f"Текст урока.\n\n```\nПромпт\n```\n\n## Тест\n\n```quiz\n{block}\n```\n"
            lesson = (await authoring.create_lesson(
                "whieda", almira, "mini-almira", module["module_id"], {"title": "Орёл или решка", "body_md": body},
            ))["lesson"]
            assert "```quiz" not in lesson["body_md"] and "Промпт" in lesson["body_md"]
            assert "<pre><code>Промпт" in lesson["body_html"] and "Не совсем" not in lesson["body_html"]
            assert lesson["quiz"]["questions"][0]["options"][0]["hint"].startswith("Не совсем")  # автору — целиком
            lesson_id = lesson["lesson_id"]
            # Без блока в тексте тест остаётся; блок с ошибкой — 400 с причиной.
            kept = (await authoring.update_lesson("whieda", almira, "mini-almira", lesson_id, {"body_md": "Новый текст."}))["lesson"]
            assert kept["quiz"]["pass"] == 2 and kept["body_md"] == "Новый текст."
            broken = json.loads(block)
            broken["questions"][0]["options"][1]["correct"] = False
            with pytest.raises(AcademyError) as bad_quiz:
                await authoring.update_lesson(
                    "whieda", almira, "mini-almira", lesson_id, {"body_md": f"```quiz\n{json.dumps(broken)}\n```"}
                )
            assert bad_quiz.value.code == "bad_quiz" and "ровно один" in bad_quiz.value.extra["detail"]
            with pytest.raises(AcademyError) as twice:
                await authoring.update_lesson(
                    "whieda", almira, "mini-almira", lesson_id, {"body_md": f"```quiz\n{block}\n```", "quiz": None}
                )
            assert twice.value.code == "bad_quiz"
            # Тест отдельным полем: объект — заменить, null — убрать.
            replaced = (await authoring.update_lesson(
                "whieda", almira, "mini-almira", lesson_id, {"quiz": {**QUIZ, "pass": 1}}
            ))["lesson"]
            assert replaced["quiz"]["pass"] == 1
            removed = (await authoring.update_lesson("whieda", almira, "mini-almira", lesson_id, {"quiz": None}))["lesson"]
            assert removed["quiz"] is None
            # Предпросмотр: тест не показывается, но проверяется.
            assert "quiz" not in await authoring.preview_markdown("whieda", almira, f"Текст\n\n```quiz\n{block}\n```\n")
            with pytest.raises(AcademyError):
                await authoring.preview_markdown("whieda", almira, "```quiz\n{не json}\n```")
            structure = await authoring.author_course("whieda", almira, "mini-almira")
            assert [l["has_quiz"] for m in structure["modules"] for l in m["lessons"]] == [False]

        db.run_with_app(proof)
