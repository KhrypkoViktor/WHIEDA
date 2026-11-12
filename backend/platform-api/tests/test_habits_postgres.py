"""Трекер привычек на настоящей PostgreSQL (V52, 11.11.2026): миграция, RLS, ручки по HTTP.

  * миграция дважды, без знака доллара, RLS включён на всех четырёх таблицах, чужой тенант ничего не видит;
  * допуск ``PLATFORM_HABITS_ALLOW``: не допущенному GET — ``enabled: false``, остальное — 403 ``habits_disabled``;
    пусто — только владелец, ``*`` — всем вошедшим; без сессии — 401;
  * шаблоны, лимит 7 (``habit_limit``, в том числе при «Продолжить»), мягкое удаление;
  * отметки: установка значения (повтор безопасен), сегодня и вчера, ``day_closed`` / ``before_start``,
    граница полуночи и смена пояса по ``tz`` запроса;
  * чужой ``habit_id`` — 404 во всех ручках; ответы с ``Cache-Control: private, no-store``;
  * «Прогресс»: прошло / выполнено / пропущено / серия, паузы в конце.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

from tests.postgres_testkit import MIGRATIONS, SQL_DIR, temporary_database

V52 = "platform_habits_v52.sql"
TABLES = ("habits", "habit_marks", "habit_pauses", "habit_profile")
OWNER, ANNA, BORIS, STRANGER = 1, 9301, 9302, 9399
P = "/api/v1/content-access/habits"
MOSCOW, VLADIVOSTOK = "Europe/Moscow", "Asia/Vladivostok"


def _utc(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=timezone.utc)


def _env(monkeypatch, allow: str = f"{ANNA},{BORIS}") -> None:
    monkeypatch.setenv("PLATFORM_CONTENT_COOKIE_SECURE", "false")
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", str(OWNER))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.setenv("PLATFORM_HABITS_ALLOW", allow)


@pytest.mark.integration
def test_v52_twice_rls_and_tenant_isolation():
    assert V52 in MIGRATIONS
    assert "$" not in (SQL_DIR / V52).read_text(encoding="utf-8")  # n8n режет знак доллара
    with temporary_database("whieda_habits_v52") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, MIGRATIONS, twice=True)
            db.apply_migrations(conn, (V52,))  # и ещё раз сам по себе
            for table in TABLES:
                row = conn.execute("select relrowsecurity from pg_class where relname = %s", (table,)).fetchone()
                assert row[0] is True, table
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, fetch_one, tenant_connection

            async with tenant_connection("whieda") as conn:
                row = await fetch_one(
                    conn,
                    "insert into habits (tenant_id, telegram_user_id, template, rhythm, goal, start_day) "
                    "values ('whieda', 1, 'warmup', 'daily', 1, '2026-11-11') returning habit_id::text as id",
                )
                await conn.execute(
                    "insert into habit_marks (habit_id, day, tenant_id, telegram_user_id) values (%s::uuid, '2026-11-11', 'whieda', 1)",
                    (row["id"],),
                )
                await conn.execute(
                    "insert into habit_profile (tenant_id, telegram_user_id, tz) values ('whieda', 1, 'Europe/Moscow')"
                )
            async with tenant_connection("other") as conn:
                for table in TABLES:
                    assert await fetch_all(conn, f"select 1 from {table}") == [], table
                with pytest.raises(psycopg.errors.InsufficientPrivilege):  # RLS: чужой тенант писать не может
                    await conn.execute(
                        "insert into habit_profile (tenant_id, telegram_user_id, tz) values ('whieda', 2, 'UTC')"
                    )

        db.run_with_app(proof)


@pytest.mark.integration
def test_habits_over_http(monkeypatch):
    _env(monkeypatch)
    with temporary_database("whieda_habits_http") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, MIGRATIONS)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.habits import service
            from app.main import create_app
            from app.tenancy import TenantContext

            async def resolve(_host: str) -> TenantContext:
                return TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                                     entitlements={"structure_basic": True})

            who: dict = {"uid": ANNA, "cookie": "raw"}
            clock = {"now": _utc(11, 9)}  # среда 11.11, 12:00 в Москве

            async def session(*_a, **_k) -> dict:
                return {"session_id": "s1", "tenant_id": "whieda", "scope": "telegram_verified",
                        "expires_at": datetime.now(timezone.utc) + timedelta(days=1), "telegram_user_id": who["uid"]}

            with (
                patch("app.tenancy.resolve_tenant_from_host", resolve),
                patch("app.content_access.routes.read_session_cookie", lambda _r: who["cookie"]),
                patch("app.content_access.routes.validate_content_session", session),
                patch.object(service, "now_utc", lambda: clock["now"]),
            ):
                app = create_app()
                app.state.http_client = AsyncMock()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                                       headers={"host": "wwc.best"}) as client:

                    async def call(method: str, path: str, expected: int = 200, **kwargs):
                        response = await client.request(method, f"{P}{path}", **kwargs)
                        assert response.status_code == expected, (method, path, response.status_code, response.text)
                        assert response.headers["cache-control"] == "private, no-store"
                        return response.json()

                    async def add(template: str, **extra) -> dict:
                        return await call("POST", "", json={"template": template, "tz": MOSCOW, **extra})

                    async def mark(habit_id: str, day: str, done: bool, expected: int = 200, tz: str = MOSCOW):
                        return await call("PUT", f"/{habit_id}/marks/{day}", expected, json={"done": done, "tz": tz})

                    # ---- допуск -----------------------------------------------------------------
                    who["cookie"] = None
                    assert (await call("GET", "", 401))["error"] == "content_session_required"
                    who["cookie"] = "raw"
                    who["uid"] = None
                    assert (await call("GET", "", 401))["error"] == "telegram_session_required"
                    who["uid"] = STRANGER
                    assert await call("GET", f"?tz={MOSCOW}") == {"ok": True, "enabled": False}
                    for method, path, kw in (
                        ("GET", "/progress", {}), ("POST", "", {"json": {"template": "warmup"}}),
                        ("PATCH", "/00000000-0000-0000-0000-000000000000", {"json": {"goal": 1}}),
                        ("POST", "/00000000-0000-0000-0000-000000000000/pause", {"json": {}}),
                        ("POST", "/00000000-0000-0000-0000-000000000000/resume", {"json": {}}),
                        ("DELETE", "/00000000-0000-0000-0000-000000000000", {}),
                        ("PUT", "/00000000-0000-0000-0000-000000000000/marks/2026-11-11", {"json": {"done": True}}),
                    ):
                        assert (await call(method, path, 403, **kw))["error"] == "habits_disabled", (method, path)
                    # Не допущенный ничего не записал, даже пояс.
                    async with await psycopg.AsyncConnection.connect(db.admin_dsn) as admin:
                        for table in TABLES:
                            cur = await admin.execute(f"select count(*) from {table}")
                            assert (await cur.fetchone())[0] == 0, table

                    # ---- пустой экран, добавление ----------------------------------------------
                    who["uid"] = ANNA
                    empty = await call("GET", f"?tz={MOSCOW}")
                    assert empty == {"ok": True, "enabled": True, "tz": MOSCOW, "today": "2026-11-11",
                                     "yesterday": "2026-11-10", "limit": 7, "active_count": 0, "habits": [], "goal": None}

                    warmup = await add("warmup")
                    assert warmup == {
                        "ok": True, "id": warmup["id"], "template": "warmup", "title": None, "rhythm": "daily", "goal": 1,
                        "params": {}, "status": "active", "start_day": "2026-11-11",
                        "marks": {"today": False, "yesterday": False}, "period": None, "streak": 0,
                    }
                    water = await add("water", params={"liters": 2.5})
                    assert water["params"] == {"liters": 2.5}
                    strength = await add("strength")
                    assert (strength["rhythm"], strength["goal"], strength["period"]) == ("weekly", 2, {"done": 0, "goal": 2})
                    book = await add("book")
                    assert (book["rhythm"], book["period"]) == ("monthly", {"done": 0, "goal": 1})
                    mine = await add("custom", title="  Йога  утром ")
                    assert mine["title"] == "Йога утром"
                    reading = await add("reading")
                    assert reading["params"] == {"pages": 10}
                    cardio = await add("cardio")

                    # ---- лимит 7 ----------------------------------------------------------------
                    over = await call("POST", "", 409, json={"template": "meetings", "tz": MOSCOW})
                    assert over["error"] == "habit_limit"
                    listing = await call("GET", f"?tz={MOSCOW}")
                    assert (listing["active_count"], len(listing["habits"])) == (7, 7)
                    assert [h["template"] for h in listing["habits"]][:3] == ["warmup", "water", "strength"]  # по порядку добавления
                    assert listing["habits"][0]["id"] == warmup["id"]

                    # Ошибки входа.
                    assert (await call("POST", "", 400, json={"template": "swim", "tz": MOSCOW}))["error"] == "bad_template"
                    assert (await call("POST", "", 400, json={"template": "custom", "title": "", "tz": MOSCOW}))["error"] == "bad_title"
                    assert (await call("POST", "", 400, json={"template": "water", "tz": MOSCOW}))["error"] == "bad_params"
                    assert (await call("POST", "", 400, json={"template": "book", "rhythm": "daily", "tz": MOSCOW}))["error"] == "bad_rhythm"
                    assert (await call("POST", "", 400, json={"template": "warmup", "tz": "Mars/Olympus"}))["error"] == "bad_tz"

                    # ---- удаление и пауза освобождают место ---------------------------------------
                    assert await call("DELETE", f"/{mine['id']}") == {"ok": True}
                    assert (await call("DELETE", f"/{mine['id']}", 404))["error"] == "habit_not_found"
                    assert len((await call("GET", f"?tz={MOSCOW}"))["habits"]) == 6
                    paused = await call("POST", f"/{reading['id']}/pause", json={"tz": MOSCOW})
                    assert paused["status"] == "paused"
                    run = await add("custom", title="Бег")  # 6 активных
                    calls_habit = await add("calls")  # 7 активных, восемь строк
                    full = await call("GET", f"?tz={MOSCOW}")
                    assert (full["active_count"], len(full["habits"])) == (7, 8)
                    assert full["habits"][-1]["id"] == reading["id"]  # пауза — внизу списка
                    assert (await call("POST", "", 409, json={"template": "meetings", "tz": MOSCOW}))["error"] == "habit_limit"
                    stop = await call("POST", f"/{reading['id']}/resume", 409, json={"tz": MOSCOW})
                    assert stop["error"] == "habit_limit"  # продолжить при семи активных нельзя
                    await call("DELETE", f"/{run['id']}")
                    assert (await call("POST", f"/{reading['id']}/resume", json={"tz": MOSCOW}))["status"] == "active"
                    again = await call("POST", f"/{reading['id']}/pause", json={"tz": MOSCOW})
                    assert again["status"] == "paused"
                    # Удалённые не возвращаются после перезагрузки.
                    for gone in (mine["id"], run["id"]):
                        assert gone not in [h["id"] for h in (await call("GET", f"?tz={MOSCOW}"))["habits"]]
                        assert gone not in [h["id"] for h in (await call("GET", "/progress"))["habits"]]

                    # ---- отметки: сегодня и вчера -----------------------------------------------
                    marked = await mark(warmup["id"], "2026-11-11", True)
                    assert (marked["marks"], marked["streak"]) == ({"today": True, "yesterday": False}, 1)
                    assert (await mark(warmup["id"], "2026-11-11", True))["marks"]["today"] is True  # повтор безопасен
                    assert (await mark(warmup["id"], "2026-11-11", False))["marks"]["today"] is False
                    assert (await mark(warmup["id"], "2026-11-11", False))["streak"] == 0  # снять снятое — тоже безопасно
                    assert (await mark(warmup["id"], "2026-11-10", True, 400))["error"] == "before_start"  # привычка добавлена сегодня
                    assert (await mark(warmup["id"], "2026-11-12", True, 400))["error"] == "day_closed"  # будущее
                    assert (await mark(warmup["id"], "2026-11-09", True, 400))["error"] == "day_closed"
                    assert (await mark(warmup["id"], "11.11.2026", True, 400))["error"] == "bad_day"
                    assert (await call("PUT", f"/{warmup['id']}/marks/2026-11-11", 400, json={"tz": MOSCOW}))["error"] == "bad_params"

                    # Недельная: две отметки — цель выполнена, сверх цели можно.
                    assert (await mark(strength["id"], "2026-11-11", True))["period"] == {"done": 1, "goal": 2}
                    after = await mark(strength["id"], "2026-11-11", True)
                    assert after["period"] == {"done": 1, "goal": 2}  # один день — одна отметка
                    # Месячная: вчера недоступно (добавлена сегодня), сегодня — да.
                    assert (await mark(book["id"], "2026-11-11", True))["period"] == {"done": 1, "goal": 1}

                    # ---- чужой habit_id --------------------------------------------------------
                    who["uid"] = BORIS
                    assert (await call("GET", f"?tz={MOSCOW}"))["habits"] == []
                    for method, path, kw in (
                        ("PATCH", f"/{warmup['id']}", {"json": {"goal": 1}}),
                        ("POST", f"/{warmup['id']}/pause", {"json": {}}),
                        ("POST", f"/{warmup['id']}/resume", {"json": {}}),
                        ("DELETE", f"/{warmup['id']}", {}),
                        ("PUT", f"/{warmup['id']}/marks/2026-11-11", {"json": {"done": True, "tz": MOSCOW}}),
                        ("PUT", "/not-a-uuid/marks/2026-11-11", {"json": {"done": True, "tz": MOSCOW}}),
                    ):
                        assert (await call(method, path, 404, **kw))["error"] == "habit_not_found", (method, path)
                    # Борис добавил свою — Анна её не видит; лимит у каждого свой.
                    boris = await add("warmup")
                    who["uid"] = ANNA
                    assert boris["id"] not in [h["id"] for h in (await call("GET", f"?tz={MOSCOW}"))["habits"]]
                    assert (await mark(boris["id"], "2026-11-11", True, 404))["error"] == "habit_not_found"
                    who["uid"] = BORIS
                    assert (await call("GET", f"?tz={MOSCOW}"))["habits"][0]["marks"]["today"] is False  # отметка Анны не его

                    # ---- «вчера», полночь, смена пояса -----------------------------------------
                    who["uid"] = ANNA
                    clock["now"] = _utc(12, 9)  # четверг 12.11, 12:00 в Москве
                    thursday = await call("GET", f"?tz={MOSCOW}")
                    assert (thursday["today"], thursday["yesterday"]) == ("2026-11-12", "2026-11-11")
                    by_template = {h["template"]: h for h in thursday["habits"]}
                    assert by_template["warmup"]["marks"] == {"today": False, "yesterday": False}
                    assert by_template["warmup"]["streak"] == 0  # 11-е не отмечено, а закончилось: пропуск
                    fixed = await mark(warmup["id"], "2026-11-11", True)  # вернулось из «пропущено» в «выполнено»
                    assert (fixed["marks"]["yesterday"], fixed["streak"]) == (True, 1)
                    # 23:59 в Москве — нажатие дошло в 00:01: день 12.11 принят как вчера.
                    clock["now"] = _utc(12, 21, 1)  # 13.11, 00:01 по Москве
                    late = await mark(warmup["id"], "2026-11-12", True)
                    assert (late["marks"], late["streak"]) == ({"today": False, "yesterday": True}, 2)
                    # Страница, открытая позавчера, — отказ.
                    assert (await mark(warmup["id"], "2026-11-11", True, 400))["error"] == "day_closed"
                    # Человек улетел на Дальний Восток (+10 ч): в 18:00 по Москве там уже 01:00 следующего дня.
                    clock["now"] = _utc(13, 15)
                    assert (await call("GET", f"?tz={MOSCOW}"))["today"] == "2026-11-13"
                    far = await call("GET", f"?tz={VLADIVOSTOK}")
                    assert (far["tz"], far["today"], far["yesterday"]) == (VLADIVOSTOK, "2026-11-14", "2026-11-13")
                    assert (await call("GET", ""))["today"] == "2026-11-14"  # пояс запомнился: запрос без tz считает по нему
                    async with await psycopg.AsyncConnection.connect(db.admin_dsn) as admin:
                        cur = await admin.execute("select tz from habit_profile where telegram_user_id = %s", (ANNA,))
                        assert [r[0] for r in await cur.fetchall()] == [VLADIVOSTOK]
                    # Отметки за прошлое хранятся по датам и не пересчитываются; вчера по новому поясу — 13-е.
                    assert (await mark(warmup["id"], "2026-11-13", True, tz=VLADIVOSTOK))["marks"]["yesterday"] is True
                    assert (await mark(warmup["id"], "2026-11-13", False, tz=VLADIVOSTOK))["marks"]["yesterday"] is False
                    assert (await call("GET", f"?tz={MOSCOW}"))["today"] == "2026-11-13"  # вернулся

                    # ---- изменение ритма и параметров ----------------------------------------
                    changed = await call("PATCH", f"/{cardio['id']}", json={"rhythm": "weekly", "goal": 3, "tz": MOSCOW})
                    assert (changed["rhythm"], changed["goal"], changed["period"]) == ("weekly", 3, {"done": 0, "goal": 3})
                    assert (await call("PATCH", f"/{cardio['id']}", 400, json={"rhythm": "monthly", "tz": MOSCOW}))["error"] == "bad_rhythm"
                    assert (await call("PATCH", f"/{cardio['id']}", 400, json={"title": "x", "tz": MOSCOW}))["error"] == "bad_title"
                    assert (await call("PATCH", f"/{water['id']}", json={"params": {"liters": 2.6}, "tz": MOSCOW}))["params"] == {"liters": 2.6}
                    assert (await call("PATCH", f"/{water['id']}", 400, json={"params": {"liters": 0.1}, "tz": MOSCOW}))["error"] == "bad_params"
                    assert (await call("PATCH", f"/{water['id']}", json={"tz": MOSCOW}))["params"] == {"liters": 2.6}  # пустое изменение

                    # ---- «Прогресс» -----------------------------------------------------------
                    clock["now"] = _utc(13, 9)  # пятница 13.11 по Москве
                    progress = await call("GET", f"/progress?tz={MOSCOW}")
                    assert (progress["ok"], progress["enabled"], progress["today"]) == (True, True, "2026-11-13")
                    items = {h["template"]: h for h in progress["habits"]}
                    assert items["warmup"] == {
                        "id": warmup["id"], "template": "warmup", "title": None, "rhythm": "daily", "goal": 1, "params": {},
                        "status": "active", "period": "day", "passed": 2, "done": 2, "missed": 0, "streak": 2, "paused": False,
                    }
                    assert (items["book"]["period"], items["book"]["passed"], items["book"]["done"]) == ("month", 1, 1)
                    assert (items["strength"]["period"], items["strength"]["passed"]) == ("week", 0)  # неделя смены ещё идёт
                    assert [items["calls"][k] for k in ("period", "passed", "done", "missed", "streak")] == ["day", 2, 0, 2, 0]
                    assert progress["habits"][-1]["paused"] is True and progress["habits"][-1]["id"] == reading["id"]

                    # ---- пауза: дни паузы не считаются ---------------------------------------
                    await call("POST", f"/{water['id']}/pause", json={"tz": MOSCOW})
                    clock["now"] = _utc(16, 9)
                    resumed = await call("POST", f"/{water['id']}/resume", json={"tz": MOSCOW})
                    assert resumed["status"] == "active"
                    water_progress = {h["template"]: h for h in (await call("GET", f"/progress?tz={MOSCOW}"))["habits"]}["water"]
                    # Вода: 11 и 12 ноября пропущены до паузы, 13–15 — пауза, 16-е сегодня и ещё не в счёте.
                    assert [water_progress[k] for k in ("passed", "done", "missed", "streak", "paused")] == [2, 0, 2, 0, False]

        db.run_with_app(proof)
