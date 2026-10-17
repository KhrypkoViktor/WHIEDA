"""Ритм курса на сайте поверх настоящей PostgreSQL (17.10.2026): GET → POST → GET по HTTP.

Роуты ``/academy/rhythm/today`` вместе с реальным SQL ``app.academy.rhythm`` и реальными
миграциями (route-тесты мокают хранилище, ``test_academy_rhythm_postgres`` ходит мимо роутов).
Подделана только сессия входа и «сегодня»:

  * участник с доступом: пустой день, серия по вчерашним дням, три галочки подряд — серия растёт,
    снятая галочка — снова нет; урок дня идёт путём сайта и сдвигается после «Сделал»;
  * пропущенный день — ``broken``; владелец (preview-админ) в ритме без доступа;
  * чужой и отозванный доступ — ``member: false`` в GET и 403 ``rhythm_not_member`` в POST, ничего не записано;
  * неверная галочка — 400 ``bad_field``.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

from tests.postgres_testkit import MIGRATIONS, temporary_database
from tests.test_academy_rhythm_postgres import MARINA, OWNER, PEOPLE, REVOKED, SERGEY, STRANGER, _env

P = "/api/v1/content-access/academy"
URL = f"{P}/rhythm/today"
COURSE = "online-start-4w"
TODAY = date(2026, 10, 19)
NONE = {"post": False, "person": False, "five": False}
ALL = {"post": True, "person": True, "five": True}


@pytest.mark.integration
def test_get_post_get_streak_over_http(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("PLATFORM_CONTENT_COOKIE_SECURE", "false")
    with temporary_database("whieda_rhythm_site") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, (*MIGRATIONS, "platform_service_sales_v10.sql"))
            conn.execute(PEOPLE)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy import rhythm
            from app.main import create_app
            from app.tenancy import TenantContext

            async def resolve(_host: str) -> TenantContext:
                return TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                                     entitlements={"structure_basic": True})

            who = {"uid": MARINA}

            async def session(*_args, **_kwargs) -> dict:
                return {"session_id": "s1", "tenant_id": "whieda", "scope": "telegram_verified",
                        "expires_at": datetime.now(timezone.utc) + timedelta(days=1), "telegram_user_id": who["uid"]}

            async def full_day(uid: int, day: date) -> None:
                for field in rhythm.FIELDS:
                    await rhythm.toggle_mark("whieda", uid, day, field)

            with (
                patch("app.tenancy.resolve_tenant_from_host", resolve),
                patch("app.content_access.routes.read_session_cookie", return_value="raw"),
                patch("app.content_access.routes.validate_content_session", session),
                patch.object(rhythm, "local_today", lambda now=None: TODAY),
            ):
                app = create_app()
                app.state.http_client = AsyncMock()
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                                       headers={"host": "wwc.best"}) as client:

                    async def call(method: str, path: str, expected: int = 200, **kwargs):
                        response = await client.request(method, path, **kwargs)
                        assert response.status_code == expected, (method, path, response.status_code, response.text)
                        if expected == 200:  # у ошибок заголовок _viewer не доезжает — так во всём Академии
                            assert response.headers["cache-control"] == "private, no-store"
                        return response.json()

                    # Марина: засчитаны 17-е и 18-е; сегодня (19-е) пока пусто.
                    for back in (2, 1):
                        await full_day(MARINA, TODAY - timedelta(days=back))
                    first = await call("GET", URL)
                    assert first == {
                        "ok": True, "member": True, "course_slug": COURSE, "day": "2026-10-19",
                        "marks": NONE, "streak": 2, "broken": False,
                        "lesson": {"slug": "start", "title": "Самое сложное — начать", "minutes": 5,
                                   "url": "/academy/?course=online-start-4w&lesson=start"},
                    }

                    # Две галочки — день не засчитан, серия по вчера; третья — серия 3.
                    after_post = await call("POST", URL, json={"field": "post"})
                    assert (after_post["marks"], after_post["streak"]) == ({**NONE, "post": True}, 2)
                    assert (await call("POST", URL, json={"field": "person"}))["streak"] == 2
                    done = await call("POST", URL, json={"field": "five"})
                    assert (done["marks"], done["streak"], done["broken"]) == (ALL, 3, False)
                    assert await call("GET", URL) == done  # POST и GET отдают один и тот же объект

                    # Галочку можно снять: день перестаёт считаться, серия — снова по вчера.
                    undone = await call("POST", URL, json={"field": "five"})
                    assert (undone["marks"], undone["streak"]) == ({**ALL, "five": False}, 2)
                    assert (await call("POST", URL, json={"field": "five"}))["streak"] == 3
                    assert (await call("POST", URL, 400, json={"field": "streak"}))["error"] == "bad_field"
                    assert (await call("POST", URL, 400, json={}))["error"] == "bad_field"

                    # «Сделал» по первому уроку — урок дня сдвигается на следующий.
                    await call("POST", f"{P}/courses/{COURSE}/lessons/start/done", json={"done": True})
                    assert (await call("GET", URL))["lesson"]["slug"] == "profil"
                    assert (await call("GET", URL))["lesson"]["url"] == "/academy/?course=online-start-4w&lesson=profil"

                    # Сергей: засчитан только позавчерашний день, вчера пропуск — «начнём заново».
                    who["uid"] = SERGEY
                    await full_day(SERGEY, TODAY - timedelta(days=3))
                    gap = await call("GET", URL)
                    assert (gap["member"], gap["marks"], gap["streak"], gap["broken"]) == (True, NONE, 0, True)

                    # Владелец — в ритме без строки доступа.
                    who["uid"] = OWNER
                    owner = await call("GET", URL)
                    assert (owner["member"], owner["streak"], owner["broken"]) == (True, 0, False)
                    assert (await call("POST", URL, json={"field": "post"}))["marks"] == {**NONE, "post": True}

                    # Чужой и отозванный доступ: GET — 200 без карточки, POST — 403, в базе пусто.
                    for uid in (STRANGER, REVOKED):
                        who["uid"] = uid
                        outsider = await call("GET", URL)
                        assert outsider == {
                            "ok": True, "member": False, "course_slug": COURSE, "day": "2026-10-19",
                            "marks": NONE, "streak": 0, "broken": False, "lesson": None,
                        }
                        assert (await call("POST", URL, 403, json={"field": "post"}))["error"] == "rhythm_not_member"
                        assert (await rhythm.card_state("whieda", uid, TODAY, TODAY))["marks"] == NONE

        db.run_with_app(proof)
