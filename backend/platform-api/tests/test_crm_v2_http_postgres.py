"""WWC CRM v2 over HTTP against real PostgreSQL: the routes, their bodies and the
cursors in query strings glued to the real SQL (route tests mock storage,
service tests skip the routes). Only the Telegram session is faked; the PRO
check reads the seeded partner subscription."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

from tests.postgres_testkit import temporary_database
from tests.test_crm_postgres import LEAD_TABLES, SEED

BASE = "/api/v1/content-access/crm"


@pytest.mark.integration
def test_v2_api_over_http(monkeypatch):
    for name in ("PLATFORM_DISABLED_FEATURES", "PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", "PLATFORM_BILLING_OWNER_TELEGRAM_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "*")
    monkeypatch.setenv("PLATFORM_CONTENT_COOKIE_SECURE", "false")
    monkeypatch.setenv("PLATFORM_CRM_LEAD_CARDS", "false")

    with temporary_database("whieda_crm_http") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(LEAD_TABLES)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.main import create_app
            from app.tenancy import TenantContext

            async def resolve(_host: str) -> TenantContext:
                return TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                                     entitlements={"structure_basic": True, "crm": True})

            who = {"telegram_user_id": 7001}

            async def session(*_args, **_kwargs) -> dict:
                return {"session_id": "s1", "tenant_id": "whieda", "scope": "telegram_verified",
                        "expires_at": datetime.now(timezone.utc) + timedelta(days=1),
                        "telegram_user_id": who["telegram_user_id"]}

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
                        response = await client.request(method, BASE + path, **kwargs)
                        assert response.status_code == expected, (method, path, response.status_code, response.text)
                        assert response.headers["cache-control"] == "private, no-store"
                        return response.json() if response.content else None

                    me = await call("GET", "/me")
                    assert (me["install_hint_dismissed"], me["my_name"]) == (False, "")
                    today = date.fromisoformat(me["today"])
                    assert (await call("PATCH", "/me", json={"install_hint_dismissed": True}))["install_hint_dismissed"]
                    assert (await call("PATCH", "/me", json={"timezone": "Europe/Minsk"}))["install_hint_dismissed"]

                    ids = {}
                    for name, tags in (("Яна", ["vip"]), ("Анна", []), ("Борис", ["vip", "минск"]),
                                       ("Вера", []), ("Глеб", ["минск"])):
                        created = await call("POST", "/contacts", 201, json={"name": name, "tags": tags})
                        ids[name] = created["contact"]["id"]

                    # People: the cursor goes back through the query string as is.
                    names, cursor = [], None
                    while True:
                        params = {"sort": "name", "limit": 2, **({"cursor": cursor} if cursor else {})}
                        page = await call("GET", "/contacts", params=params)
                        assert page["total"] == 5 and "contacts" not in page
                        names += [item["name"] for item in page["items"]]
                        cursor = page["next_cursor"]
                        if not cursor:
                            break
                    assert names == ["Анна", "Борис", "Вера", "Глеб", "Яна"]
                    legacy = await call("GET", "/contacts")
                    assert len(legacy["contacts"]) == 5 and legacy["contacts"] == legacy["items"]
                    assert [i["name"] for i in (await call("GET", "/contacts", params={"tag": "минск", "sort": "name"}))["items"]] == ["Борис", "Глеб"]
                    assert (await call("GET", "/contacts", 400, params={"cursor": "zzz", "limit": 5}))["error"] == "invalid_cursor"
                    assert (await call("GET", "/tags"))["items"] == [{"tag": "vip", "count": 2}, {"tag": "минск", "count": 2}]

                    # A card: «Сделано», «Перенести», a tap with a template, the history.
                    anna = ids["Анна"]
                    assert (await call("POST", f"/contacts/{anna}/done", 400, json={}))["error"] == "meeting_at_required"
                    meeting = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0)
                    done = await call("POST", f"/contacts/{anna}/done", json={"meeting_at": meeting.isoformat()})
                    assert (done["contact"]["status"], done["contact"]["next_step"]) == ("invited", "result")
                    moved = await call("POST", f"/contacts/{anna}/snooze", json={"date": (today + timedelta(days=1)).isoformat()})
                    assert moved["contact"]["next_at"] == (today + timedelta(days=1)).isoformat()
                    assert (await call("POST", f"/contacts/{anna}/snooze", 400, json={"days": 0}))["error"] == "invalid_snooze"
                    templates = (await call("GET", "/templates"))["items"]
                    assert len(templates) == 6
                    logged = await call("POST", f"/contacts/{anna}/log", 201,
                                        json={"kind": "message", "channel": "telegram", "template_id": templates[0]["id"]})
                    assert logged["activity"]["payload"]["template_title"] == "Приглашение"
                    assert logged["contact"]["last_touch_at"]
                    await call("POST", f"/contacts/{anna}/notes", 201, json={"body": "перезвонить"})
                    kinds, cursor = [], None
                    while True:
                        feed = await call("GET", f"/contacts/{anna}/activities",
                                          params={"limit": 2, **({"cursor": cursor} if cursor else {})})
                        kinds += [item["kind"] for item in feed["items"]]
                        cursor = feed["next_cursor"]
                        if not cursor:
                            break
                    assert kinds == ["note", "message", "step", "meeting", "status", "step", "created"]
                    patched = await call("PATCH", f"/contacts/{anna}", json={"priority": True, "tags": ["#Клиент"]})
                    assert (patched["contact"]["priority"], patched["contact"]["tags"]) == (1, ["Клиент"])

                    # Pipeline: counts and the rest of a column by cursor.
                    board = await call("GET", "/pipeline")
                    counts = {column["status"]: column["count"] for column in board["columns"]}
                    assert (counts["new"], counts["invited"], board["total"]) == (4, 1, 5)
                    first = await call("GET", "/pipeline/new", params={"limit": 3})
                    rest = await call("GET", "/pipeline/new", params={"limit": 3, "cursor": first["next_cursor"]})
                    assert len(first["items"]) == 3 and len(rest["items"]) == 1 and rest["next_cursor"] is None
                    assert (await call("GET", "/pipeline/lost", 400))["error"] == "invalid_status"

                    # Delete → gone everywhere → «Вернуть».
                    await call("DELETE", f"/contacts/{ids['Вера']}", 204)
                    await call("GET", f"/contacts/{ids['Вера']}", 404)
                    assert (await call("GET", "/contacts", params={"limit": 10}))["total"] == 4
                    restored = await call("POST", f"/contacts/{ids['Вера']}/restore")
                    assert restored["contact"]["name"] == "Вера"

                    # Templates.
                    own = await call("POST", "/templates", 201, json={"title": "Свой", "body": "{имя}, привет"})
                    await call("PATCH", f"/templates/{own['template']['id']}", json={"body": "{имя}, добрый день"})
                    await call("DELETE", f"/templates/{own['template']['id']}", 204)
                    await call("DELETE", f"/templates/{own['template']['id']}", 404)
                    assert (await call("POST", "/templates", 400, json={"title": "x", "body": " "}))["error"] == "body_required"

                    # Another partner sees none of it.
                    who["telegram_user_id"] = 7002
                    for method, path in (("GET", f"/contacts/{anna}"), ("GET", f"/contacts/{anna}/activities"),
                                         ("POST", f"/contacts/{anna}/restore"), ("PATCH", f"/templates/{templates[0]['id']}")):
                        await call(method, path, 404, **({"json": {"title": "x"}} if method == "PATCH" else {}))
                    assert (await call("GET", "/contacts", params={"limit": 10}))["total"] == 0
                    assert len((await call("GET", "/templates"))["items"]) == 6  # his own defaults

        db.run_with_app(proof)
