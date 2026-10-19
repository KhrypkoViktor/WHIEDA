"""Academy V39 on real PostgreSQL: the mini-course opens for free after a test and the app.

ТЗ: ``PROCESS/wwc-mini-courses-20261016/TZ_UNLOCK.md`` пп. 2–5. По HTTP, с настоящим SQL и миграциями;
подделана только сессия входа (как в ``test_academy_rhythm_site_postgres``).

  * миграция идемпотентна, обе таблицы изолированы по тенанту, платформа — из трёх значений;
  * пауза пересдачи: провал → попытка с ``retry_at`` в состоянии → вторая попытка 429 ``quiz_cooldown``
    (в базе одна попытка) → сдвиг ``created_at`` назад → попытка проходит; сданный тест паузы снимает;
  * отметка приложения: upsert, неизвестная платформа — null и не стирает известную, без входа — 401;
  * закрытый курс отдаёт ``unlock`` (список и деталь), сданный тест и приложение вместе открывают его
    (``free_reason: unlock``, самостоятельно), открытому ``unlock`` — null;
  * сообщение об открытии — ровно одно, в любом порядке событий; не уходит купившему; сбой постановки
    не ломает ответ и не пишет запись, следующий повод шлёт.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

from tests.postgres_testkit import MIGRATIONS, SQL_DIR, temporary_database

V39 = "platform_academy_unlock_v39.sql"
P = "/api/v1/content-access/academy"
OWNER, ANNA, BORIS, CARL, DINA, EVA, FRANK, BUYER = 1, 9301, 9302, 9303, 9304, 9305, 9306, 9307
QUIZ_URL = "/academy/?course=zapusk-wwc&lesson=itogi-i-plan"
RULE = {"unlock": {"quiz": {"course": "zapusk-wwc", "lesson": "itogi-i-plan"}, "app": True}}
QUIZ = {
    "pass": 1,
    "retry_after_min": 30,
    "questions": [
        {"id": "q1", "q": "Что делать первым?", "ref": "Урок 1",
         "options": [
             {"id": "a", "text": "Ничего", "correct": False, "hint": "Не совсем. Перечитайте: урок 1."},
             {"id": "b", "text": "Начать", "correct": True, "hint": ""},
         ]},
    ],
}
WRONG, RIGHT = {"answers": {"q1": "a"}}, {"answers": {"q1": "b"}}

PEOPLE = f"""
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id) values
  ('anna', 'whieda', 'Анна', {ANNA}, '{ANNA}'),
  ('boris', 'whieda', 'Борис', {BORIS}, '{BORIS}'),
  ('carl', 'whieda', 'Карл', {CARL}, '{CARL}'),
  ('dina', 'whieda', 'Дина', {DINA}, '{DINA}'),
  ('eva', 'whieda', 'Ева', {EVA}, '{EVA}'),
  ('frank', 'whieda', 'Франк', {FRANK}, '{FRANK}'),
  ('buyer', 'whieda', 'Купил', {BUYER}, '{BUYER}');
insert into academy_courses (tenant_id, slug, title, access_rule, status)
values ('whieda', 'zapusk-wwc', 'Запуск WWC', 'pro', 'published');
insert into academy_courses (tenant_id, slug, title, access_rule, status, kind, free_for)
values ('whieda', 'mini-dhando', 'Алхимия капитала', 'purchase', 'published', 'mini', '@RULE@');
insert into academy_lessons (tenant_id, course_id, slug, position, title, body_html, quiz_json)
select 'whieda', course_id, 'itogi-i-plan', 1, 'Итоги запуска и план на 30 дней', '<p>Итоги</p>', '@QUIZ@'
from academy_courses where slug = 'zapusk-wwc';
insert into academy_lessons (tenant_id, course_id, slug, position, title, body_html)
select 'whieda', course_id, 'urok-1', 1, 'Первый урок', '<p>Урок</p>' from academy_courses where slug = 'mini-dhando';
insert into academy_access (tenant_id, course_id, telegram_user_id, source, payment_ref)
select 'whieda', c.course_id, u.uid, 'purchase', 'shop:' || u.uid
from academy_courses c, (values ({ANNA}), ({CARL}), ({DINA}), ({EVA}), ({FRANK}), ({BUYER})) as u(uid)
where c.slug = 'zapusk-wwc';
insert into academy_access (tenant_id, course_id, telegram_user_id, source, payment_ref)
select 'whieda', course_id, {BUYER}, 'purchase', 'shop:mini' from academy_courses where slug = 'mini-dhando';
"""


def _env(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", str(OWNER))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.setenv("PLATFORM_CONTENT_COOKIE_SECURE", "false")
    monkeypatch.setenv("PLATFORM_ACADEMY_SITE_BASE", "https://wwc.best")
    monkeypatch.setenv("PLATFORM_ACADEMY_NOTIFY_BINDING", "test-bot")
    monkeypatch.delenv("PLATFORM_SCHEDULED_NOTIFY_BINDINGS", raising=False)


def _seed(conn: psycopg.Connection) -> None:
    # Несколько команд в одном execute — без параметров, JSON вставляем в текст (одинарных кавычек в нём нет).
    conn.execute(
        PEOPLE.replace("@RULE@", json.dumps(RULE, ensure_ascii=False)).replace(
            "@QUIZ@", json.dumps(_stored_quiz(), ensure_ascii=False)
        )
    )


def _stored_quiz() -> dict:
    from app.academy.quiz import validate_quiz

    return validate_quiz(QUIZ)


def _select(db, query: str, *params):
    with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
        return conn.execute(query, params).fetchall()


@pytest.mark.integration
def test_v39_twice_tables_are_tenant_isolated_and_checked():
    assert V39 in MIGRATIONS
    assert "$" not in (SQL_DIR / V39).read_text(encoding="utf-8")  # n8n режет знак доллара
    with temporary_database("whieda_unlock_v39") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, MIGRATIONS, twice=True)
            conn.execute(
                "insert into academy_courses (tenant_id, slug, title, access_rule, status)"
                " values ('whieda', 'c', 'C', 'purchase', 'published')"
            )
            conn.execute("insert into academy_app_installs (tenant_id, telegram_user_id, platform) values ('whieda', 1, 'ios')")
            conn.execute("insert into academy_app_installs (tenant_id, telegram_user_id) values ('whieda', 2)")
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute("insert into academy_app_installs (tenant_id, telegram_user_id) values ('whieda', 1)")
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "insert into academy_app_installs (tenant_id, telegram_user_id, platform) values ('whieda', 3, 'windows')"
                )
            conn.execute(
                "insert into academy_unlock_notices (tenant_id, course_id, telegram_user_id)"
                " select 'whieda', course_id, 1 from academy_courses where slug = 'c'"
            )
            with pytest.raises(psycopg.errors.UniqueViolation):
                conn.execute(
                    "insert into academy_unlock_notices (tenant_id, course_id, telegram_user_id)"
                    " select 'whieda', course_id, 1 from academy_courses where slug = 'c'"
                )
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                conn.execute(
                    "insert into academy_unlock_notices (tenant_id, course_id, telegram_user_id)"
                    " values ('whieda', gen_random_uuid(), 1)"
                )
            for table in ("academy_app_installs", "academy_unlock_notices"):
                assert conn.execute(
                    "select relrowsecurity from pg_class where relname = %s", (table,)
                ).fetchone() == (True,)
            db.grant_api_role(conn)
        with psycopg.connect(db.api_dsn, autocommit=True) as api:
            api.execute("select set_config('app.tenant_id', 'other', false)")
            assert api.execute("select count(*) from academy_app_installs").fetchone() == (0,)
            assert api.execute("select count(*) from academy_unlock_notices").fetchone() == (0,)
            api.execute("select set_config('app.tenant_id', 'whieda', false)")
            assert api.execute("select count(*) from academy_app_installs").fetchone() == (2,)
            assert api.execute("select count(*) from academy_unlock_notices").fetchone() == (1,)
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            conn.execute("delete from academy_courses where slug = 'c'")  # записи об открытии уходят с курсом
            assert conn.execute("select count(*) from academy_unlock_notices").fetchone() == (0,)


@pytest.mark.integration
def test_cooldown_unlock_progress_and_one_notice_over_http(monkeypatch):
    _env(monkeypatch)
    with temporary_database("whieda_unlock_http") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, (*MIGRATIONS, "platform_service_sales_v10.sql"))
            _seed(conn)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy import unlock
            from app.main import create_app
            from app.tenancy import TenantContext

            async def resolve(_host: str) -> TenantContext:
                return TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                                     entitlements={"structure_basic": True})

            who: dict[str, int | None] = {"uid": ANNA}

            async def session(*_args, **_kwargs) -> dict:
                return {"session_id": "s1", "tenant_id": "whieda", "scope": "telegram_verified",
                        "expires_at": datetime.now(timezone.utc) + timedelta(days=1), "telegram_user_id": who["uid"]}

            quiz_path = f"{P}/courses/zapusk-wwc/lessons/itogi-i-plan"
            with (
                patch("app.tenancy.resolve_tenant_from_host", resolve),
                patch("app.content_access.routes.read_session_cookie", return_value="raw"),
                patch("app.content_access.routes.validate_content_session", session),
            ):
                app = create_app()
                app.state.http_client = AsyncMock()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                                       headers={"host": "wwc.best"}) as client:

                    async def call(method: str, path: str, expected: int = 200, **kwargs):
                        response = await client.request(method, path, **kwargs)
                        assert response.status_code == expected, (method, path, response.status_code, response.text)
                        return response.json()

                    async def mini(uid: int) -> dict:
                        who["uid"] = uid
                        courses = {c["slug"]: c for c in (await call("GET", f"{P}/courses"))["courses"]}
                        return courses["mini-dhando"]

                    def notices() -> list[tuple]:
                        return _select(db, "select telegram_user_id from academy_unlock_notices order by 1")

                    def outbox() -> list[tuple]:
                        return _select(
                            db,
                            "select idempotency_key, status, payload from platform_outbox"
                            " where event_type = 'academy_unlock_opened' order by outbox_id",
                        )

                    # 1. Закрытый курс отдаёт прогресс — в списке и в детали; не пройдя «Запуск», eligible: false.
                    waiting = {"quiz_passed": False, "app_installed": False, "quiz_url": QUIZ_URL,
                               "quiz_course_title": "Запуск WWC", "eligible": True}
                    card = await mini(ANNA)
                    assert (card["locked"], card["lock_reason"], card["free_reason"]) == (True, "purchase_required", None)
                    assert card["unlock"] == waiting
                    detail = await call("GET", f"{P}/courses/mini-dhando")
                    assert detail["course"]["unlock"] == waiting and detail["course"]["free_reason"] is None
                    assert (await mini(BORIS))["unlock"] == {**waiting, "eligible": False}
                    zapusk = (await call("GET", f"{P}/courses/zapusk-wwc"))["course"]
                    assert zapusk["unlock"] is None  # у курса без правила — null
                    # Купивший: курс открыт, прогресса нет.
                    bought = await mini(BUYER)
                    assert (bought["locked"], bought["free_reason"], bought["unlock"]) == (False, None, None)

                    # 2. Тест с паузой: до попыток retry_at нет, public_quiz отдаёт число минут.
                    who["uid"] = ANNA
                    lesson = (await call("GET", quiz_path))["lesson"]
                    assert lesson["quiz"]["retry_after_min"] == 30
                    assert lesson["quiz_state"] == {"passed": False, "best": 0, "attempts": 0, "retry_at": None}

                    # 3. Провал: в состоянии retry_at через 30 минут; вторая попытка — 429, попытка не пишется.
                    failed = await call("POST", f"{quiz_path}/quiz", json=WRONG)
                    assert (failed["passed"], failed["quiz_state"]["attempts"]) == (False, 1)
                    retry_at = datetime.fromisoformat(failed["quiz_state"]["retry_at"])
                    assert retry_at.utcoffset() == timedelta(0)
                    assert timedelta(minutes=29) < retry_at - datetime.now(timezone.utc) <= timedelta(minutes=30)
                    held = await call("POST", f"{quiz_path}/quiz", 429, json=RIGHT)
                    assert (held["ok"], held["error"]) == (False, "quiz_cooldown")
                    assert held["retry_at"] == failed["quiz_state"]["retry_at"]
                    assert _select(db, "select count(*) from academy_quiz_attempts") == [(1,)]
                    assert (await call("GET", quiz_path))["lesson"]["quiz_state"]["retry_at"] == failed["quiz_state"]["retry_at"]

                    # 4. Прошло 31 минута — попытка проходит; сдан — паузы нет, а один тест без приложения ничего не шлёт.
                    _select(db, "update academy_quiz_attempts set created_at = created_at - interval '31 minutes'"
                                " returning 1")
                    passed = await call("POST", f"{quiz_path}/quiz", json=RIGHT)
                    assert (passed["passed"], passed["quiz_state"]["retry_at"]) == (True, None)
                    again = await call("POST", f"{quiz_path}/quiz", json=WRONG)  # после сдачи пауз нет
                    assert (again["passed"], again["quiz_state"]["retry_at"]) == (False, None)
                    assert (again["quiz_state"]["passed"], again["quiz_state"]["attempts"]) == (True, 3)
                    assert notices() == [] and outbox() == []
                    half = await mini(ANNA)
                    assert half["locked"] is True and half["unlock"] == {**waiting, "quiz_passed": True}

                    # 5. Без входа отметка приложения — 401, ничего не записано.
                    who["uid"] = None
                    assert (await call("POST", f"{P}/app/installed", 401, json={"platform": "android"}))["error"] == (
                        "telegram_session_required"
                    )
                    assert _select(db, "select count(*) from academy_app_installs") == [(0,)]

                    # 6. Приложение — последнее условие: курс открыт, самостоятельно, одно сообщение.
                    who["uid"] = ANNA
                    marked = await call("POST", f"{P}/app/installed", json={"platform": "android"})
                    assert (marked["ok"], marked["installed"]) == (True, True)
                    first_seen = datetime.fromisoformat(marked["first_seen_at"])
                    assert first_seen.utcoffset() == timedelta(0)
                    opened = await mini(ANNA)
                    assert (opened["locked"], opened["free_reason"], opened["unlock"]) == (False, "unlock", None)
                    outline = (await call("GET", f"{P}/courses/mini-dhando"))["course"]
                    assert (outline["locked"], outline["free_reason"], outline["self_study"], outline["unlock"]) == (
                        False, "unlock", True, None,
                    )
                    assert notices() == [(ANNA,)]
                    [(mini_id,)] = _select(db, "select course_id from academy_courses where slug = 'mini-dhando'")
                    [(key, status, payload)] = outbox()
                    assert (key, status) == (f"academy_unlock_opened:{mini_id}:{ANNA}", "scheduled")
                    assert payload["text"] == (
                        "🎁 Мини-курс «Алхимия капитала» открыт бесплатно: вы сдали тест «Запуск WWC» и поставили приложение WWC."
                    )
                    assert payload["chat_id"] == str(ANNA) and payload["binding_id"] == "test-bot"
                    assert payload["site_button"] == {
                        "text": "Открыть", "url": "https://wwc.best/academy/?course=mini-dhando", "telegram_user_id": ANNA,
                    }

                    # Повторная отметка и повторные попытки — тот же один ряд, платформа и first_seen на месте.
                    marked_again = await call("POST", f"{P}/app/installed", json={"platform": "weird"})
                    assert marked_again["first_seen_at"] == marked["first_seen_at"]
                    await call("POST", f"{quiz_path}/quiz", json=RIGHT)
                    assert len(notices()) == 1 and len(outbox()) == 1
                    assert _select(db, "select platform from academy_app_installs where telegram_user_id = %s", ANNA) == [("android",)]

                    # 7. Обратный порядок: сначала приложение (платформа мусор → null, тело необязательно), потом тест.
                    who["uid"] = CARL
                    await call("POST", f"{P}/app/installed", json={"platform": "Windows 11"})
                    assert _select(db, "select platform from academy_app_installs where telegram_user_id = %s", CARL) == [(None,)]
                    assert (await mini(CARL))["unlock"] == {**waiting, "app_installed": True}
                    await call("POST", f"{P}/app/installed")  # пустое тело — тоже отметка
                    assert len(notices()) == 1
                    await call("POST", f"{quiz_path}/quiz", json=RIGHT)
                    assert notices() == [(ANNA,), (CARL,)]
                    assert (await mini(CARL))["free_reason"] == "unlock"

                    # Известная платформа не стирается неизвестной, новая известная — заменяет.
                    who["uid"] = DINA
                    await call("POST", f"{P}/app/installed", json={"platform": "ios"})
                    await call("POST", f"{P}/app/installed", json={"platform": 7})
                    assert _select(db, "select platform from academy_app_installs where telegram_user_id = %s", DINA) == [("ios",)]
                    await call("POST", f"{P}/app/installed", json={"platform": "desktop"})
                    assert _select(db, "select platform from academy_app_installs where telegram_user_id = %s", DINA) == [("desktop",)]

                    # 8. Купивший сдаёт тест и ставит приложение — «открыт бесплатно» ему не шлём.
                    who["uid"] = BUYER
                    await call("POST", f"{quiz_path}/quiz", json=RIGHT)
                    await call("POST", f"{P}/app/installed", json={"platform": "ios"})
                    assert notices() == [(ANNA,), (CARL,)]

                    # 9. Сбой постановки в очередь: ответ как обычно, записи нет; следующий повод шлёт.
                    who["uid"] = EVA
                    await call("POST", f"{P}/app/installed", json={"platform": "android"})
                    with patch.object(unlock, "enqueue_notification", AsyncMock(side_effect=RuntimeError("outbox down"))):
                        failed_send = await call("POST", f"{quiz_path}/quiz", json=RIGHT)
                    assert failed_send["passed"] is True
                    assert (EVA,) not in notices() and len(outbox()) == 2
                    assert (await mini(EVA))["free_reason"] == "unlock"  # открыт и без сообщения
                    await call("POST", f"{P}/app/installed", json={"platform": "android"})
                    assert (EVA,) in notices() and len(outbox()) == 3

                    # 10. Бот уведомлений не настроен: сообщение не поставлено, записи нет (повод повторится).
                    who["uid"] = FRANK
                    with patch("app.academy.notify.notify_binding_id", lambda: None):
                        await call("POST", f"{P}/app/installed", json={"platform": "ios"})
                        await call("POST", f"{quiz_path}/quiz", json=RIGHT)
                    assert (FRANK,) not in notices() and len(outbox()) == 3
                    await call("POST", f"{P}/app/installed", json={"platform": "ios"})
                    assert (FRANK,) in notices() and len(outbox()) == 4

        db.run_with_app(proof)
