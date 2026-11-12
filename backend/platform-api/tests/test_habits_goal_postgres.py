"""Цель в трекере привычек на настоящей PostgreSQL (V53, 12.11.2026): миграция, RLS, ручки по HTTP.

  * миграция дважды, без знака доллара, только добавление (V52 не меняется), RLS включён, чужой тенант не видит;
  * одна цель без ответа на человека — на уровне базы (частичный уникальный индекс);
  * тот же допуск, что у привычек: не допущенному GET /habits без цели, остальные ручки цели — 403;
  * поставить / изменить (начало не меняется), ошибки ``bad_goal_*``, числа по местному дню и подставленному «сейчас»;
  * «Да» до срока закрывает цель, «Нет» до срока — 409 ``goal_not_expired``, после срока — ``missed``;
  * прошлая цель остаётся в базе с ответом и днём ответа; чужая цель не видна и не меняется;
  * ``goal`` в ``GET /habits`` и ``GET /habits/progress``, «снять» цель — мягко.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

from tests.postgres_testkit import MIGRATIONS, SQL_DIR, temporary_database

V52 = "platform_habits_v52.sql"
V53 = "platform_habit_goals_v53.sql"
OWNER, ANNA, BORIS, STRANGER = 1, 9301, 9302, 9399
P = "/api/v1/content-access/habits"
MOSCOW, VLADIVOSTOK = "Europe/Moscow", "Asia/Vladivostok"


def _utc(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


def _env(monkeypatch, allow: str = f"{ANNA},{BORIS}") -> None:
    monkeypatch.setenv("PLATFORM_CONTENT_COOKIE_SECURE", "false")
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", str(OWNER))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.setenv("PLATFORM_HABITS_ALLOW", allow)


@pytest.mark.integration
def test_v53_twice_only_adds_rls_and_one_open_goal():
    assert V53 in MIGRATIONS
    sql = (SQL_DIR / V53).read_text(encoding="utf-8")
    assert "$" not in sql  # n8n режет знак доллара
    lowered = sql.lower()
    for forbidden in ("drop table", "drop column", "alter table habits", "alter table habit_marks", "alter table habit_pauses", "alter table habit_profile", "delete from", "truncate"):
        assert forbidden not in lowered, forbidden  # только добавление
    with temporary_database("whieda_habit_goals_v53") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, MIGRATIONS, twice=True)
            db.apply_migrations(conn, (V53,))  # и ещё раз сам по себе
            row = conn.execute("select relrowsecurity from pg_class where relname = 'habit_goals'").fetchone()
            assert row[0] is True
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, tenant_connection

            insert = (
                "insert into habit_goals (tenant_id, telegram_user_id, title, start_day, due_day, outcome, answered_on) "
                "values ('whieda', %s, %s, '2026-11-01', '2026-12-31', %s, %s)"
            )
            async with tenant_connection("whieda") as conn:
                await conn.execute(insert, (1, "Первая", None, None))
                await conn.execute(insert, (1, "Закрытая", "achieved", "2026-11-05"))  # ответ закрывает цель
                await conn.execute(insert, (2, "Другого человека", None, None))
            async with tenant_connection("whieda") as conn:
                with pytest.raises(psycopg.errors.UniqueViolation):  # вторая цель без ответа
                    await conn.execute(insert, (1, "Вторая", None, None))
            async with tenant_connection("whieda") as conn:
                with pytest.raises(psycopg.errors.CheckViolation):  # ответ без дня ответа
                    await conn.execute(insert, (3, "Без дня", "missed", None))
            async with tenant_connection("whieda") as conn:
                with pytest.raises(psycopg.errors.CheckViolation):  # срок раньше начала
                    await conn.execute(
                        "insert into habit_goals (tenant_id, telegram_user_id, title, start_day, due_day) "
                        "values ('whieda', 4, 'Назад', '2026-12-31', '2026-11-01')"
                    )
            async with tenant_connection("whieda") as conn:
                with pytest.raises(psycopg.errors.CheckViolation):  # 81 знак
                    await conn.execute(insert, (5, "Я" * 81, None, None))
            async with tenant_connection("other") as conn:
                assert await fetch_all(conn, "select 1 from habit_goals") == []
                with pytest.raises(psycopg.errors.InsufficientPrivilege):  # RLS: чужой тенант писать не может
                    await conn.execute(insert, (6, "Чужая", None, None))

        db.run_with_app(proof)


@pytest.mark.integration
def test_goal_over_http(monkeypatch):
    _env(monkeypatch)
    with temporary_database("whieda_habit_goals_http") as db:
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
            clock = {"now": _utc(11, 11, 9)}  # среда 11.11.2026, 12:00 в Москве

            async def session(*_a, **_k) -> dict:
                return {"session_id": "s1", "tenant_id": "whieda", "scope": "telegram_verified",
                        "expires_at": datetime.now(timezone.utc) + timedelta(days=1), "telegram_user_id": who["uid"]}

            async def rows(sql: str, *args):
                async with await psycopg.AsyncConnection.connect(db.admin_dsn) as admin:
                    cur = await admin.execute(sql, args)
                    return await cur.fetchall()

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

                    async def put(expected: int = 200, tz: str = MOSCOW, **fields):
                        return await call("PUT", "/goal", expected, json={"tz": tz, **fields})

                    # ---- допуск ------------------------------------------------------------------
                    who["cookie"] = None
                    assert (await call("PUT", "/goal", 401, json={}))["error"] == "content_session_required"
                    who["cookie"] = "raw"
                    who["uid"] = None
                    assert (await call("GET", "/goal", 401))["error"] == "telegram_session_required"
                    who["uid"] = STRANGER
                    assert await call("GET", f"?tz={MOSCOW}") == {"ok": True, "enabled": False}  # без поля goal
                    for method, path, kw in (
                        ("GET", "/goal", {}),
                        ("PUT", "/goal", {"json": {"title": "Цель", "due": "2026-12-31"}}),
                        ("POST", "/goal/answer", {"json": {"achieved": True}}),
                        ("DELETE", "/goal", {}),
                    ):
                        assert (await call(method, path, 403, **kw))["error"] == "habits_disabled", (method, path)
                    assert await rows("select count(*) from habit_goals") == [(0,)]  # не допущенный ничего не записал

                    # ---- цели нет ----------------------------------------------------------------
                    who["uid"] = ANNA
                    assert (await call("GET", f"?tz={MOSCOW}"))["goal"] is None
                    assert (await call("GET", f"/progress?tz={MOSCOW}"))["goal"] is None
                    assert await call("GET", f"/goal?tz={MOSCOW}") == {"ok": True, "goal": None}
                    assert (await call("POST", "/goal/answer", 404, json={"achieved": True, "tz": MOSCOW}))["error"] == "goal_not_found"
                    assert (await call("DELETE", "/goal", 404))["error"] == "goal_not_found"

                    # ---- поставить: начало по умолчанию — сегодня; примеры 5.5 -------------------
                    made = await put(title="  Квалификация   директора ", due="2026-12-31")
                    goal_id = made["goal"]["id"]
                    assert made == {"ok": True, "goal": {
                        "id": goal_id, "title": "Квалификация директора", "start": "2026-11-11", "due": "2026-12-31",
                        "left": 50, "passed": 0, "total": 50, "percent": 0, "expired": False}}
                    listing = await call("GET", f"?tz={MOSCOW}")
                    assert listing["goal"] == made["goal"]
                    assert listing["habits"] == [] and listing["active_count"] == 0  # привычки и цель друг на друга не влияют
                    assert (await call("GET", f"/progress?tz={MOSCOW}"))["goal"] == made["goal"]

                    # ---- изменить: текст и срок; начало остаётся прежним --------------------------
                    clock["now"] = _utc(11, 12, 9)  # четверг 12.11: прошёл 1 день из 50
                    edited = await put(title="Директор", due="2027-01-31")
                    assert edited["goal"]["id"] == goal_id  # та же цель, не новая
                    assert (edited["goal"]["title"], edited["goal"]["start"], edited["goal"]["due"]) == ("Директор", "2026-11-11", "2027-01-31")
                    assert (edited["goal"]["left"], edited["goal"]["passed"], edited["goal"]["total"], edited["goal"]["percent"]) == (80, 1, 81, 1)
                    assert (await put(400, title="Директор", due="2027-01-31", start="2026-10-01"))["error"] == "bad_goal_start"
                    same = await put(title="Директор", due="2027-01-31", start="2026-11-11")  # то же начало — не ошибка
                    assert same["goal"]["start"] == "2026-11-11"
                    assert await rows("select count(*) from habit_goals") == [(1,)]

                    # ---- ошибки входа -------------------------------------------------------------
                    assert (await put(400, title="", due="2027-01-31"))["error"] == "bad_goal_title"
                    assert (await put(400, title="Я" * 81, due="2027-01-31"))["error"] == "bad_goal_title"
                    assert (await put(400, title="Цель"))["error"] == "bad_goal_due"  # срок обязателен
                    assert (await put(400, title="Цель", due="2026-11-11"))["error"] == "bad_goal_due"  # вчера по местному дню
                    assert (await put(400, title="Цель", due="скоро"))["error"] == "bad_goal_due"
                    assert (await put(400, title="Цель", due="2027-01-31", tz="Mars/Olympus"))["error"] == "bad_tz"
                    assert (await call("POST", "/goal/answer", 400, json={"achieved": "yes", "tz": MOSCOW}))["error"] == "bad_params"
                    assert (await call("POST", "/goal/answer", 400, json={"tz": MOSCOW}))["error"] == "bad_params"
                    assert (await call("GET", f"/goal?tz={MOSCOW}"))["goal"]["title"] == "Директор"  # ошибки ничего не сломали

                    # ---- чужая цель не видна и не меняется ----------------------------------------
                    who["uid"] = BORIS
                    assert (await call("GET", f"?tz={MOSCOW}"))["goal"] is None
                    assert (await call("POST", "/goal/answer", 404, json={"achieved": True, "tz": MOSCOW}))["error"] == "goal_not_found"
                    boris = await put(title="Моя цель", due="2026-12-01", start="2026-11-01")
                    assert boris["goal"]["start"] == "2026-11-01"  # начало при создании можно поставить раньше
                    assert (boris["goal"]["left"], boris["goal"]["passed"], boris["goal"]["total"]) == (19, 11, 30)
                    assert boris["goal"]["percent"] == 37  # 11 из 30 = 36,7
                    who["uid"] = ANNA
                    assert (await call("GET", f"?tz={MOSCOW}"))["goal"]["title"] == "Директор"

                    # ---- «Нет» до срока нельзя; «Да» раньше срока закрывает цель -------------------
                    stop = await call("POST", "/goal/answer", 409, json={"achieved": False, "tz": MOSCOW})
                    assert stop["error"] == "goal_not_expired"
                    assert (await call("GET", f"?tz={MOSCOW}"))["goal"]["title"] == "Директор"
                    early = await call("POST", "/goal/answer", json={"achieved": True, "tz": MOSCOW})
                    assert early == {"ok": True, "goal": None}
                    assert (await call("GET", f"?tz={MOSCOW}"))["goal"] is None
                    assert await rows(
                        "select title, outcome, answered_on::text, deleted_at is null from habit_goals "
                        "where telegram_user_id = %s", ANNA) == [("Директор", "achieved", "2026-11-12", True)]  # история осталась
                    assert (await call("POST", "/goal/answer", 404, json={"achieved": True, "tz": MOSCOW}))["error"] == "goal_not_found"

                    # ---- новая цель после ответа: начало — сегодня, прошлая в базе ------------------
                    clock["now"] = _utc(11, 15, 9)
                    fresh = await put(title="Вторая цель", due="2026-11-20")
                    assert fresh["goal"]["id"] != goal_id and fresh["goal"]["start"] == "2026-11-15"
                    assert await rows("select count(*) from habit_goals where telegram_user_id = %s", ANNA) == [(2,)]

                    # ---- последний день: ещё идёт; на следующий день — срок вышел -------------------
                    clock["now"] = _utc(11, 20, 9)  # 20.11 по Москве: последний день срока
                    last = (await call("GET", f"?tz={MOSCOW}"))["goal"]
                    assert (last["left"], last["expired"], last["passed"], last["total"], last["percent"]) == (0, False, 5, 5, 100)
                    assert (await call("POST", "/goal/answer", 409, json={"achieved": False, "tz": MOSCOW}))["error"] == "goal_not_expired"
                    clock["now"] = _utc(11, 21, 9)  # 21.11: срок вышел
                    gone = (await call("GET", f"?tz={MOSCOW}"))["goal"]
                    assert (gone["left"], gone["expired"], gone["passed"], gone["percent"]) == (0, True, 5, 100)
                    assert (await call("GET", f"/progress?tz={MOSCOW}"))["goal"]["expired"] is True
                    # Привычки при вышедшем сроке работают как обычно.
                    habit = await call("POST", "", json={"template": "warmup", "tz": MOSCOW})
                    marked = await call("PUT", f"/{habit['id']}/marks/2026-11-21", json={"done": True, "tz": MOSCOW})
                    assert marked["marks"]["today"] is True
                    # «Нет» после срока — цель закрыта как не достигнутая.
                    missed = await call("POST", "/goal/answer", json={"achieved": False, "tz": MOSCOW})
                    assert missed == {"ok": True, "goal": None}
                    assert await rows(
                        "select outcome, answered_on::text from habit_goals where telegram_user_id = %s and title = 'Вторая цель'",
                        ANNA) == [("missed", "2026-11-21")]

                    # ---- пояс: «сегодня» по тz человека ---------------------------------------------
                    clock["now"] = _utc(11, 21, 15, 30)  # 21.11 18:30 в Москве, 22.11 01:30 во Владивостоке
                    await put(title="Пояс", due="2026-11-22", tz=MOSCOW)
                    in_moscow = (await call("GET", f"?tz={MOSCOW}"))["goal"]
                    assert (in_moscow["left"], in_moscow["expired"]) == (1, False)
                    in_vladivostok = (await call("GET", f"?tz={VLADIVOSTOK}"))["goal"]
                    assert (in_vladivostok["left"], in_vladivostok["expired"]) == (0, False)  # там уже 22.11: последний день
                    assert in_vladivostok["passed"] == in_moscow["passed"] + 1  # число дней сдвинулось на единицу

                    # ---- снять цель: мягко, строка остаётся ---------------------------------------
                    assert await call("DELETE", "/goal") == {"ok": True}
                    assert (await call("GET", f"?tz={MOSCOW}"))["goal"] is None
                    assert (await call("DELETE", "/goal", 404))["error"] == "goal_not_found"
                    assert await rows("select deleted_at is not null from habit_goals where title = 'Пояс'") == [(True,)]
                    # После «снять» можно ставить новую (индекс не мешает).
                    assert (await put(title="После снятия", due="2026-12-31"))["goal"]["title"] == "После снятия"

        db.run_with_app(proof)
