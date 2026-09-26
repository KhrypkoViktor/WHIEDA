"""CRM site API: session, entitlement, pilot, PRO, and the contact endpoints.

The app is built like ``content_app`` in tests/test_content_access.py, with the
``crm`` entitlement on the whieda tenant; storage calls are mocked (the real SQL
runs in tests/test_crm_postgres.py).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.crm.rules import CrmViewer
from app.crm.service import CrmError
from app.main import create_app
from app.settings import get_settings
from app.tenancy import TenantContext

HOST = {"host": "wwc.best"}
NO_CRM_HOST = {"host": "nocrm.test"}
BASE = "/api/v1/content-access/crm"
ACCOUNT = {"account_id": "9f0c3c1e-0000-4000-8000-000000000001", "telegram_user_id": 42,
           "timezone": "Europe/Moscow", "today": date(2026, 9, 25)}
PAID = CrmViewer(telegram_user_id=42, is_preview_admin=False, partner_paid=True, ref_code="igor")
UNPAID = CrmViewer(telegram_user_id=42, is_preview_admin=False, partner_paid=False, ref_code="igor")
CONTACT = {"id": "c1", "name": "Анна", "phone": "+79286729288", "status": "new", "next_step": "invite",
           "next_at": "2026-09-25"}


@pytest.fixture
def crm_app(monkeypatch):
    monkeypatch.setenv("PLATFORM_CONTENT_COOKIE_SECURE", "false")
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "*")
    monkeypatch.delenv("PLATFORM_DISABLED_FEATURES", raising=False)
    get_settings.cache_clear()
    monkeypatch.setattr("app.main.init_pool", AsyncMock())
    monkeypatch.setattr("app.main.close_pool", AsyncMock())
    monkeypatch.setattr("app.main.check_postgres", AsyncMock(return_value=True))

    async def resolve(host: str) -> TenantContext:
        from app.tenancy import normalize_host

        entitlements = {"structure_basic": True, "partner_leads": True}
        if normalize_host(host) != "nocrm.test":
            entitlements["crm"] = True
        return TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements=entitlements)

    monkeypatch.setattr("app.tenancy.resolve_tenant_from_host", resolve)
    application = create_app()
    application.state.http_client = AsyncMock()
    yield application
    get_settings.cache_clear()


def _session(user_id: int = 42) -> dict:
    return {
        "session_id": "s1",
        "tenant_id": "whieda",
        "scope": "telegram_verified",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
        "telegram_user_id": user_id,
    }


def _signed_in(viewer: CrmViewer = PAID):
    return (
        patch("app.content_access.routes.read_session_cookie", return_value="raw"),
        patch("app.content_access.routes.validate_content_session", AsyncMock(return_value=_session(viewer.telegram_user_id))),
        patch("app.crm.routes.load_viewer", AsyncMock(return_value=viewer)),
        patch("app.crm.routes.get_or_create_account", AsyncMock(return_value=ACCOUNT)),
    )


async def _call(app, method: str, path: str, *, host=HOST, viewer: CrmViewer | None = PAID, extra=(), **kwargs):
    patches = list(_signed_in(viewer)) if viewer is not None else []
    patches += list(extra)
    for item in patches:
        item.start()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, BASE + path, headers=host, **kwargs)
    finally:
        for item in reversed(patches):
            item.stop()


@pytest.mark.asyncio
async def test_without_session_401_and_private(crm_app):
    response = await _call(crm_app, "GET", "/today", viewer=None)
    assert response.status_code == 401
    assert response.json()["error"] == "content_session_required"
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_tenant_without_crm_entitlement_403(crm_app):
    response = await _call(crm_app, "GET", "/me", host=NO_CRM_HOST)
    assert response.status_code == 403
    assert response.json()["error"] == "feature_disabled"


@pytest.mark.asyncio
async def test_without_pro_402(crm_app):
    response = await _call(crm_app, "GET", "/today", viewer=UNPAID)
    assert response.status_code == 402
    assert response.json()["error"] == "pro_required"
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_outside_pilot_403_inside_pilot_ok(crm_app, monkeypatch):
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "7, 8")
    get_settings.cache_clear()
    response = await _call(crm_app, "GET", "/me")
    assert response.status_code == 403
    assert response.json()["error"] == "crm_pilot_only"

    pilot = CrmViewer(telegram_user_id=7, is_preview_admin=False, partner_paid=True)
    response = await _call(crm_app, "GET", "/me", viewer=pilot)
    assert response.status_code == 200
    assert response.json()["pilot"] is True
    assert response.json()["timezone"] == "Europe/Moscow"
    assert response.json()["today"] == "2026-09-25"


@pytest.mark.asyncio
async def test_empty_pilot_means_nobody(crm_app, monkeypatch):
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "")
    get_settings.cache_clear()
    response = await _call(crm_app, "GET", "/me")
    assert response.status_code == 403
    assert response.json()["error"] == "crm_pilot_only"


@pytest.mark.asyncio
async def test_data_the_database_refuses_is_400_without_the_row_in_logs(crm_app, caplog):
    import psycopg

    class Refused(psycopg.errors.CheckViolation):
        def __str__(self):
            return 'new row violates check constraint DETAIL: Failing row contains (Анна, +７９１６)'

    create = AsyncMock(side_effect=Refused())
    response = await _call(
        crm_app, "POST", "/contacts", json={"name": "Анна", "phone": "+７ ９１６"},
        extra=[patch("app.crm.routes.create_contact", create)],
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_input"
    assert "Анна" not in response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert "Анна" not in caplog.text and "Failing row" not in caplog.text


@pytest.mark.asyncio
async def test_preview_admin_without_pro_passes(crm_app):
    admin = CrmViewer(telegram_user_id=42, is_preview_admin=True, partner_paid=False)
    response = await _call(crm_app, "GET", "/me", viewer=admin)
    assert response.status_code == 200
    assert response.json()["pilot"] is False  # '*' — open to every PRO partner


@pytest.mark.asyncio
async def test_create_contact_201(crm_app):
    create = AsyncMock(return_value=CONTACT)
    response = await _call(
        crm_app, "POST", "/contacts", json={"name": "Анна", "phone": "8 928 672-92-88"},
        extra=[patch("app.crm.routes.create_contact", create)],
    )
    assert response.status_code == 201
    assert response.json()["contact"]["id"] == "c1"
    assert response.headers["cache-control"] == "private, no-store"
    assert create.await_args.kwargs == {"name": "Анна", "phone": "8 928 672-92-88", "source": None}


@pytest.mark.asyncio
async def test_duplicate_phone_409_names_the_existing_card(crm_app):
    create = AsyncMock(side_effect=CrmError("duplicate", 409, contact_id="c-old"))
    response = await _call(
        crm_app, "POST", "/contacts", json={"name": "Анна", "phone": "+79286729288"},
        extra=[patch("app.crm.routes.create_contact", create)],
    )
    assert response.status_code == 409
    assert response.json()["error"] == "duplicate"
    assert response.json()["contact_id"] == "c-old"


@pytest.mark.asyncio
async def test_foreign_contact_404(crm_app):
    missing = AsyncMock(side_effect=CrmError("contact_not_found", 404))
    for method, path, extra, kwargs in (
        ("GET", "/contacts/other", patch("app.crm.routes.get_contact", missing), {}),
        ("PATCH", "/contacts/other", patch("app.crm.routes.update_contact", missing), {"json": {"name": "x"}}),
        ("DELETE", "/contacts/other", patch("app.crm.routes.delete_contact", missing), {}),
        ("POST", "/contacts/other/notes", patch("app.crm.routes.add_note", missing), {"json": {"body": "x"}}),
    ):
        response = await _call(crm_app, method, path, extra=[extra], **kwargs)
        assert response.status_code == 404, (method, path)
        assert response.json()["error"] == "contact_not_found"


@pytest.mark.asyncio
async def test_patch_passes_only_sent_fields(crm_app):
    update = AsyncMock(return_value={**CONTACT, "status": "presented"})
    response = await _call(
        crm_app, "PATCH", "/contacts/c1", json={"status": "presented", "next_at": None},
        extra=[patch("app.crm.routes.update_contact", update)],
    )
    assert response.status_code == 200
    changes = update.await_args.args[3]
    assert changes == {"status": "presented", "next_at": None}


@pytest.mark.asyncio
async def test_patch_rule_error_is_400(crm_app):
    update = AsyncMock(side_effect=CrmError("meeting_at_required", 400))
    response = await _call(
        crm_app, "PATCH", "/contacts/c1", json={"status": "invited"},
        extra=[patch("app.crm.routes.update_contact", update)],
    )
    assert response.status_code == 400
    assert response.json()["error"] == "meeting_at_required"


@pytest.mark.asyncio
async def test_delete_is_204(crm_app):
    response = await _call(
        crm_app, "DELETE", "/contacts/c1", extra=[patch("app.crm.routes.delete_contact", AsyncMock(return_value=None))]
    )
    assert response.status_code == 204
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_bad_timezone_400(crm_app):
    response = await _call(
        crm_app, "PATCH", "/me", json={"timezone": "Mars/Olympus"},
        extra=[patch("app.crm.routes.set_timezone", AsyncMock(side_effect=CrmError("invalid_timezone", 400)))],
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_timezone"


@pytest.mark.asyncio
async def test_today_shape(crm_app):
    view = {"date": "2026-09-25", "groups": [{"step": "invite", "title": "Пригласить на встречу", "contacts": [CONTACT]}],
            "overdue": 0}
    response = await _call(crm_app, "GET", "/today", extra=[patch("app.crm.routes.today_view", AsyncMock(return_value=view))])
    assert response.status_code == 200
    assert response.json()["groups"][0]["contacts"][0]["name"] == "Анна"


@pytest.mark.asyncio
async def test_export_is_csv_attachment(crm_app):
    body = "﻿Имя;Телефон\r\nАнна;'+79286729288\r\n"
    response = await _call(crm_app, "GET", "/export.csv", extra=[patch("app.crm.routes.export_csv", AsyncMock(return_value=body))])
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="wwc-contacts-2026-09-25.csv"'
    assert response.headers["cache-control"] == "private, no-store"
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert "Анна" in response.content.decode("utf-8-sig")


@pytest.mark.asyncio
async def test_disabled_feature_unmounts_router(monkeypatch):
    monkeypatch.setenv("PLATFORM_DISABLED_FEATURES", "crm")
    get_settings.cache_clear()
    try:
        app = create_app()
        paths = {getattr(route, "path", "") for route in app.routes}
        assert not any(path.startswith(BASE) for path in paths)
    finally:
        get_settings.cache_clear()


# ---- bulk import (contacts from the phone book, .vcf, .csv) ------------------------


def _bulk_items(count: int) -> list[dict]:
    return [{"name": f"Имя {i}", "phone": f"+7999{i:07d}"} for i in range(count)]


@pytest.mark.asyncio
async def test_bulk_without_session_401(crm_app):
    response = await _call(crm_app, "POST", "/contacts/bulk", viewer=None, json={"contacts": _bulk_items(1)})
    assert response.status_code == 401
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_bulk_without_pro_402(crm_app):
    response = await _call(crm_app, "POST", "/contacts/bulk", viewer=UNPAID, json={"contacts": _bulk_items(1)})
    assert response.status_code == 402
    assert response.json()["error"] == "pro_required"


@pytest.mark.asyncio
async def test_bulk_201_returns_created_and_skipped(crm_app):
    result = {"created": 2, "skipped": [{"name": "Аня", "phone": "+79286729288", "contact_id": "c-old", "reason": "duplicate"}]}
    bulk = AsyncMock(return_value=result)
    response = await _call(
        crm_app, "POST", "/contacts/bulk",
        json={"contacts": [{"name": "Анна", "phone": "8 928 672-92-88"}, {"name": "Борис", "phone": "", "source": "спортзал"},
                           {"name": "Аня", "phone": "+79286729288"}]},
        extra=[patch("app.crm.routes.bulk_create_contacts", bulk)],
    )
    assert response.status_code == 201
    assert response.json() == {"ok": True, **result}
    assert response.headers["cache-control"] == "private, no-store"
    assert bulk.await_args.args[:2] == ("whieda", ACCOUNT)
    assert bulk.await_args.args[2] == [
        {"name": "Анна", "phone": "8 928 672-92-88", "source": None},
        {"name": "Борис", "phone": "", "source": "спортзал"},
        {"name": "Аня", "phone": "+79286729288", "source": None},
    ]


@pytest.mark.asyncio
async def test_bulk_over_500_is_400_and_nothing_is_written(crm_app):
    bulk = AsyncMock()
    response = await _call(
        crm_app, "POST", "/contacts/bulk", json={"contacts": _bulk_items(501)},
        extra=[patch("app.crm.routes.bulk_create_contacts", bulk)],
    )
    assert response.status_code == 400
    assert response.json()["error"] == "too_many_contacts"
    assert response.json()["limit"] == 500
    bulk.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_empty_list_is_400(crm_app):
    bulk = AsyncMock()
    response = await _call(
        crm_app, "POST", "/contacts/bulk", json={"contacts": []},
        extra=[patch("app.crm.routes.bulk_create_contacts", bulk)],
    )
    assert response.status_code == 400
    assert response.json()["error"] == "contacts_required"
    bulk.assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_data_the_database_refuses_is_400_without_the_rows_in_logs(crm_app, caplog):
    import psycopg

    class Refused(psycopg.errors.CheckViolation):
        def __str__(self):
            return 'new row violates check constraint DETAIL: Failing row contains (Анна, +７９１６)'

    bulk = AsyncMock(side_effect=Refused())
    response = await _call(
        crm_app, "POST", "/contacts/bulk", json={"contacts": [{"name": "Анна", "phone": "+７ ９１６"}]},
        extra=[patch("app.crm.routes.bulk_create_contacts", bulk)],
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_input"
    assert "Анна" not in response.text
    assert "Анна" not in caplog.text and "Failing row" not in caplog.text
