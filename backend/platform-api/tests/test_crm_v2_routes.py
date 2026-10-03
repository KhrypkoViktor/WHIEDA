"""WWC CRM v2 site API (TASK.md раздел 5): new endpoints, their bodies and errors,
the access gate and ``private, no-store`` on each, v1 compatibility of /contacts
and /me. Storage is mocked; the SQL runs in tests/test_crm_v2_postgres.py."""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.crm.rules import CrmViewer
from app.crm.service import CrmError
from tests.test_crm_routes import ACCOUNT, CONTACT, PAID, UNPAID, _call, crm_app  # noqa: F401 (fixture)

PAGE = {"items": [CONTACT], "next_cursor": "abc", "total": 7}
TEMPLATE = {"id": "t1", "title": "Приглашение", "body": "{имя}, здравствуйте!", "position": 1}
ACTIVITY = {"id": "a1", "kind": "call", "payload": {"channel": "phone"}, "created_at": "2026-10-02T10:00:00+00:00"}

NEW_ENDPOINTS = [
    ("GET", "/pipeline", None),
    ("GET", "/pipeline/new", None),
    ("GET", "/tags", None),
    ("GET", "/templates", None),
    ("POST", "/templates", {"title": "x", "body": "y"}),
    ("PATCH", "/templates/t1", {"title": "x"}),
    ("DELETE", "/templates/t1", None),
    ("GET", "/contacts/c1/activities", None),
    ("POST", "/contacts/c1/log", {"kind": "call"}),
    ("POST", "/contacts/c1/snooze", {"days": 1}),
    ("POST", "/contacts/c1/done", {}),
    ("POST", "/contacts/c1/restore", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method, path, body", NEW_ENDPOINTS)
async def test_new_endpoints_need_a_session_and_pro(crm_app, method, path, body):
    kwargs = {"json": body} if body is not None else {}
    anonymous = await _call(crm_app, method, path, viewer=None, **kwargs)
    assert anonymous.status_code == 401
    assert anonymous.headers["cache-control"] == "private, no-store"
    unpaid = await _call(crm_app, method, path, viewer=UNPAID, **kwargs)
    assert unpaid.status_code == 402 and unpaid.json()["error"] == "pro_required"
    assert unpaid.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_contacts_v1_call_keeps_300_and_the_contacts_key(crm_app):
    listing = AsyncMock(return_value=PAGE)
    response = await _call(crm_app, "GET", "/contacts", extra=[patch("app.crm.routes.list_contacts", listing)])
    assert response.status_code == 200
    assert response.json()["contacts"] == [CONTACT] and response.json()["items"] == [CONTACT]
    assert listing.await_args.kwargs["default_limit"] == 300
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_contacts_v2_query_is_paged(crm_app):
    listing = AsyncMock(return_value=PAGE)
    response = await _call(
        crm_app, "GET", "/contacts?q=%D0%B0%D0%BD%D0%BD%D0%B0&status=new&tag=vip&cursor=abc&limit=50&sort=name",
        extra=[patch("app.crm.routes.list_contacts", listing)],
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, **PAGE}  # no v1 key
    assert listing.await_args.kwargs == {
        "q": "анна", "status": "new", "tag": "vip", "cursor": "abc", "limit": 50, "sort": "name", "default_limit": 50}


@pytest.mark.asyncio
async def test_search_in_the_body_keeps_names_out_of_urls(crm_app):
    listing = AsyncMock(return_value=PAGE)
    response = await _call(crm_app, "POST", "/contacts/search", json={"q": "Анна +7928", "sort": "name"},
                           extra=[patch("app.crm.routes.list_contacts", listing)])
    assert response.status_code == 200 and response.json() == {"ok": True, **PAGE}
    assert response.headers["cache-control"] == "private, no-store"
    assert listing.await_args.kwargs == {"q": "Анна +7928", "status": None, "tag": None, "cursor": None,
                                         "limit": None, "sort": "name"}
    anonymous = await _call(crm_app, "POST", "/contacts/search", viewer=None, json={"q": "x"})
    assert anonymous.status_code == 401


def test_access_log_keeps_only_the_path_of_crm_requests():
    import logging

    from app.crm.routes import _NoQueryInAccessLog, install_access_log_filter

    install_access_log_filter()
    install_access_log_filter()  # idempotent
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(item, _NoQueryInAccessLog) for item in access.filters) == 1

    def line(path: str) -> str:
        record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                                   ("1.2.3.4:5", "GET", path, "1.1", 200), None)
        assert access.filter(record)
        return record.getMessage()

    crm = line("/api/v1/content-access/crm/contacts?q=%D0%90%D0%BD%D0%BD%D0%B0&limit=50")
    assert crm == '1.2.3.4:5 - "GET /api/v1/content-access/crm/contacts?[redacted] HTTP/1.1" 200'
    assert line("/api/v1/content-access/crm/today") == '1.2.3.4:5 - "GET /api/v1/content-access/crm/today HTTP/1.1" 200'
    assert "?page=2" in line("/api/v1/other?page=2")  # other routes untouched


@pytest.mark.asyncio
async def test_bad_cursor_or_sort_is_400(crm_app):
    for code in ("invalid_cursor", "invalid_sort"):
        listing = AsyncMock(side_effect=CrmError(code, 400))
        response = await _call(crm_app, "GET", "/contacts?cursor=zzz", extra=[patch("app.crm.routes.list_contacts", listing)])
        assert response.status_code == 400 and response.json()["error"] == code
    response = await _call(crm_app, "GET", "/contacts?limit=abc")
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_pipeline_and_column(crm_app):
    board = {"columns": [{"status": "new", "title": "Новый контакт", "count": 1, "items": [CONTACT], "next_cursor": None}],
             "total": 1}
    board_call = AsyncMock(return_value=board)
    response = await _call(crm_app, "GET", "/pipeline", extra=[patch("app.crm.routes.pipeline", board_call)])
    assert response.status_code == 200 and response.json()["columns"][0]["count"] == 1
    assert board_call.await_args.kwargs == {"tag": None}
    await _call(crm_app, "GET", "/pipeline?tag=vip", extra=[patch("app.crm.routes.pipeline", board_call)])
    assert board_call.await_args.kwargs == {"tag": "vip"}
    column = AsyncMock(return_value={"status": "new", "title": "Новый контакт", **PAGE})
    response = await _call(crm_app, "GET", "/pipeline/new?cursor=abc&limit=20&tag=vip",
                           extra=[patch("app.crm.routes.pipeline_column", column)])
    assert response.status_code == 200
    assert column.await_args.args[2] == "new"
    assert column.await_args.kwargs == {"cursor": "abc", "limit": 20, "tag": "vip"}
    bad = AsyncMock(side_effect=CrmError("invalid_status", 400))
    response = await _call(crm_app, "GET", "/pipeline/lost", extra=[patch("app.crm.routes.pipeline_column", bad)])
    assert response.status_code == 400 and response.json()["error"] == "invalid_status"


@pytest.mark.asyncio
async def test_activities_and_tags(crm_app):
    feed = AsyncMock(return_value={"items": [ACTIVITY], "next_cursor": None})
    response = await _call(crm_app, "GET", "/contacts/c1/activities?limit=10",
                           extra=[patch("app.crm.routes.list_activities", feed)])
    assert response.status_code == 200 and response.json()["items"] == [ACTIVITY]
    assert feed.await_args.args[2] == "c1" and feed.await_args.kwargs == {"cursor": None, "limit": 10}
    missing = AsyncMock(side_effect=CrmError("contact_not_found", 404))
    response = await _call(crm_app, "GET", "/contacts/other/activities", extra=[patch("app.crm.routes.list_activities", missing)])
    assert response.status_code == 404
    tags = AsyncMock(return_value=[{"tag": "vip", "count": 3}])
    response = await _call(crm_app, "GET", "/tags", extra=[patch("app.crm.routes.list_tags", tags)])
    assert response.json() == {"ok": True, "items": [{"tag": "vip", "count": 3}]}


@pytest.mark.asyncio
async def test_log_a_tap(crm_app):
    log = AsyncMock(return_value={"contact": CONTACT, "activity": ACTIVITY})
    response = await _call(crm_app, "POST", "/contacts/c1/log",
                           json={"kind": "message", "channel": "whatsapp", "template_id": "t1"},
                           extra=[patch("app.crm.routes.log_contact", log)])
    assert response.status_code == 201 and response.json()["activity"] == ACTIVITY
    assert log.await_args.kwargs == {"kind": "message", "channel": "whatsapp", "template_id": "t1"}
    bad = AsyncMock(side_effect=CrmError("invalid_channel", 400))
    response = await _call(crm_app, "POST", "/contacts/c1/log", json={"kind": "message"},
                           extra=[patch("app.crm.routes.log_contact", bad)])
    assert response.status_code == 400 and response.json()["error"] == "invalid_channel"


@pytest.mark.asyncio
async def test_snooze_by_days_or_date(crm_app):
    snooze = AsyncMock(return_value=CONTACT)
    response = await _call(crm_app, "POST", "/contacts/c1/snooze", json={"days": 3},
                           extra=[patch("app.crm.routes.snooze_contact", snooze)])
    assert response.status_code == 200 and snooze.await_args.kwargs == {"days": 3, "on": None}
    response = await _call(crm_app, "POST", "/contacts/c1/snooze", json={"date": "2026-10-05"},
                           extra=[patch("app.crm.routes.snooze_contact", snooze)])
    assert snooze.await_args.kwargs == {"days": None, "on": date(2026, 10, 5)}
    bad = AsyncMock(side_effect=CrmError("snooze_required", 400))
    response = await _call(crm_app, "POST", "/contacts/c1/snooze", json={},
                           extra=[patch("app.crm.routes.snooze_contact", bad)])
    assert response.status_code == 400 and response.json()["error"] == "snooze_required"


@pytest.mark.asyncio
async def test_done_passes_meeting_and_only_a_sent_date(crm_app):
    done = AsyncMock(return_value=CONTACT)
    response = await _call(crm_app, "POST", "/contacts/c1/done", json={},
                           extra=[patch("app.crm.routes.done_contact", done)])
    assert response.status_code == 200
    assert done.await_args.kwargs == {"meeting_at": None, "next_at": None, "next_at_given": False}
    await _call(crm_app, "POST", "/contacts/c1/done", json={"meeting_at": "2026-10-05T14:00:00+03:00", "next_at": None},
                extra=[patch("app.crm.routes.done_contact", done)])
    assert done.await_args.kwargs["meeting_at"] == datetime(2026, 10, 5, 11, tzinfo=timezone.utc)
    assert done.await_args.kwargs["next_at_given"] is True
    needs_meeting = AsyncMock(side_effect=CrmError("meeting_at_required", 400))
    response = await _call(crm_app, "POST", "/contacts/c1/done", json={},
                           extra=[patch("app.crm.routes.done_contact", needs_meeting)])
    assert response.status_code == 400 and response.json()["error"] == "meeting_at_required"


@pytest.mark.asyncio
async def test_soft_delete_and_restore(crm_app):
    response = await _call(crm_app, "DELETE", "/contacts/c1",
                           extra=[patch("app.crm.routes.delete_contact", AsyncMock(return_value=None))])
    assert response.status_code == 204
    restore = AsyncMock(return_value=CONTACT)
    response = await _call(crm_app, "POST", "/contacts/c1/restore", extra=[patch("app.crm.routes.restore_contact", restore)])
    assert response.status_code == 200 and response.json()["contact"] == CONTACT
    for error in (CrmError("contact_not_found", 404), CrmError("duplicate", 409, contact_id="c2")):
        response = await _call(crm_app, "POST", "/contacts/c1/restore",
                               extra=[patch("app.crm.routes.restore_contact", AsyncMock(side_effect=error))])
        assert response.status_code == error.status and response.json()["error"] == error.code
    assert response.json()["contact_id"] == "c2"


@pytest.mark.asyncio
async def test_create_and_patch_take_tags_and_priority(crm_app):
    create = AsyncMock(return_value=CONTACT)
    response = await _call(crm_app, "POST", "/contacts", json={"name": "Анна", "tags": ["vip"], "priority": 1},
                           extra=[patch("app.crm.routes.create_contact", create)])
    assert response.status_code == 201
    assert create.await_args.kwargs == {"name": "Анна", "phone": None, "source": None, "tags": ["vip"], "priority": 1}
    update = AsyncMock(return_value=CONTACT)
    await _call(crm_app, "PATCH", "/contacts/c1", json={"tags": None, "priority": True},
                extra=[patch("app.crm.routes.update_contact", update)])
    assert update.await_args.args[3] == {"tags": None, "priority": 1}
    response = await _call(crm_app, "PATCH", "/contacts/c1", json={"tags": ["x" * 201]})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_templates_crud(crm_app):
    response = await _call(crm_app, "GET", "/templates",
                           extra=[patch("app.crm.routes.list_templates", AsyncMock(return_value=[TEMPLATE]))])
    assert response.json() == {"ok": True, "items": [TEMPLATE]}
    create = AsyncMock(return_value=TEMPLATE)
    response = await _call(crm_app, "POST", "/templates", json={"title": "Привет", "body": "{имя}!"},
                           extra=[patch("app.crm.routes.create_template", create)])
    assert response.status_code == 201
    assert create.await_args.kwargs == {"title": "Привет", "body": "{имя}!", "position": None}
    update = AsyncMock(return_value=TEMPLATE)
    await _call(crm_app, "PATCH", "/templates/t1", json={"position": 3},
                extra=[patch("app.crm.routes.update_template", update)])
    assert update.await_args.args[2:] == ("t1", {"position": 3})
    response = await _call(crm_app, "DELETE", "/templates/t1",
                           extra=[patch("app.crm.routes.delete_template", AsyncMock(return_value=None))])
    assert response.status_code == 204 and response.headers["cache-control"] == "private, no-store"
    for error in (CrmError("too_many_templates", 400, limit=30), CrmError("template_not_found", 404)):
        response = await _call(crm_app, "POST", "/templates", json={"title": "x", "body": "y"},
                               extra=[patch("app.crm.routes.create_template", AsyncMock(side_effect=error))])
        assert response.status_code == error.status and response.json()["error"] == error.code


@pytest.mark.asyncio
async def test_me_has_install_flag_and_my_name(crm_app):
    named = CrmViewer(telegram_user_id=42, is_preview_admin=False, partner_paid=True, ref_code="igor",
                      public_profile={"display_name": "  Игорь   Иванов "})
    response = await _call(crm_app, "GET", "/me", viewer=named)
    assert response.json()["install_hint_dismissed"] is False
    assert response.json()["my_name"] == "Игорь Иванов"
    assert (await _call(crm_app, "GET", "/me", viewer=PAID)).json()["my_name"] == ""

    update = AsyncMock(return_value={**ACCOUNT, "install_hint_dismissed": True})
    response = await _call(crm_app, "PATCH", "/me", json={"install_hint_dismissed": True},
                           extra=[patch("app.crm.routes.update_account", update)])
    assert response.status_code == 200 and response.json()["install_hint_dismissed"] is True
    assert update.await_args.kwargs == {"timezone_name": None, "install_hint_dismissed": True}

    timezone_only = AsyncMock(return_value={**ACCOUNT, "timezone": "Europe/Minsk"})
    response = await _call(crm_app, "PATCH", "/me", json={"timezone": "Europe/Minsk"},
                           extra=[patch("app.crm.routes.set_timezone", timezone_only)])
    assert response.json()["timezone"] == "Europe/Minsk"
    assert timezone_only.await_args.args[2] == "Europe/Minsk"
