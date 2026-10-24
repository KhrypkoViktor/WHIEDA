"""/me/overview и чтения CRM одной транзакцией (24.10.2026), без базы.

Подмена базы считает подключения. Здесь проверяется проводка: на маршруте открывается одно
подключение, оно же уходит и в загрузку человека, и в главную, и в запросы CRM; необязательные
чтения идут после обязательных; а когда общая транзакция оборвана, главная собирается заново
отдельными транзакциями. Настоящий SQL, обрыв на настоящей базе и сохранность записей — в
``test_overview_one_transaction_postgres.py`` и ``test_crm_one_transaction_postgres.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from psycopg import pq

from app.cabinet import service
from app.cabinet.service import CabinetPerson
from app.db import transaction_failed
from tests.fake_db import FakeDb
from tests.test_cabinet_overview import _facts, _person
from tests.test_crm_routes import ACCOUNT, PAID, UNPAID, _call, crm_app  # noqa: F401 (fixture)

HOST = {"host": "wwc.best"}
NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)


def _failed(conn) -> None:
    conn.info = SimpleNamespace(transaction_status=pq.TransactionStatus.INERROR)


def test_transaction_failed_reads_the_driver_status_and_tolerates_fakes():
    assert transaction_failed(object()) is False  # у подмены нет info
    assert transaction_failed(SimpleNamespace(info=SimpleNamespace(transaction_status=pq.TransactionStatus.INTRANS))) is False
    assert transaction_failed(SimpleNamespace(info=SimpleNamespace(transaction_status=pq.TransactionStatus.INERROR))) is True


# ---- /me/overview: порядок чтений ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_overview_reads_required_before_optional_on_a_shared_connection(monkeypatch):
    order: list[str] = []

    async def invite(*args, **kwargs):
        order.append("invite")
        return {"link": None, "text": None}

    async def facts(*args, **kwargs):
        order.append("facts")
        return _facts()

    async def settings(*args, **kwargs):
        order.append("settings")
        return {"timezone": None, "timezone_default": "Europe/Moscow", "marketing_opt_in": None}

    monkeypatch.setattr(service, "invite_block", invite)
    monkeypatch.setattr(service, "collect_facts", facts)
    monkeypatch.setattr(service, "settings_block", settings)
    result = await service.load_overview("whieda", _person(site=True), bot_username="bot", crm_entitled=True, conn=object())
    # Код приглашения — запись, ему нужна живая транзакция; CRM и настройки читаются в самом конце.
    assert order == ["invite", "facts", "settings"]
    assert list(result)[:4] == ["ok", "tier", "locks", "person"]  # порядок ключей ответа прежний
    assert list(result)[-2:] == ["links", "settings"]


@pytest.mark.asyncio
async def test_overview_passes_the_shared_connection_to_every_helper(monkeypatch):
    shared = object()
    invite = AsyncMock(return_value={"link": None, "text": None})
    facts = AsyncMock(return_value=_facts())
    settings = AsyncMock(return_value={"timezone": None, "marketing_opt_in": None})
    monkeypatch.setattr(service, "invite_block", invite)
    monkeypatch.setattr(service, "collect_facts", facts)
    monkeypatch.setattr(service, "settings_block", settings)
    await service.load_overview("whieda", _person(site=True), bot_username="bot", crm_entitled=True, conn=shared)
    for helper in (invite, facts, settings):
        assert helper.await_args.kwargs["conn"] is shared
    await service.load_overview("whieda", _person(site=True), bot_username="bot", crm_entitled=True)
    for helper in (invite, facts, settings):
        assert helper.await_args.kwargs["conn"] is None  # без conn — по-старому, у каждого своя транзакция


# ---- /me/overview: оборванная общая транзакция --------------------------------------------------


@pytest.fixture
def overview_route(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr("app.cabinet.routes.tenant_connection", db.tenant_connection)
    monkeypatch.setattr("app.cabinet.routes._current_session", AsyncMock(return_value={"telegram_user_id": 7001}))
    loader = AsyncMock(return_value=_person(site=True))
    monkeypatch.setattr("app.cabinet.routes.load_person", loader)
    return db, loader


@pytest.mark.asyncio
async def test_overview_is_built_again_the_old_way_when_the_shared_transaction_broke(client, overview_route, monkeypatch):
    db, loader = overview_route
    calls: list[object] = []

    async def overview(*_args, conn=None, **_kwargs):
        calls.append(conn)
        if conn is not None:
            _failed(conn)  # необязательное чтение упало: транзакция принимает только откат
            return {"ok": True, "source": "shared"}
        return {"ok": True, "source": "separate"}

    monkeypatch.setattr("app.cabinet.routes.load_overview", overview)
    response = await client.get("/api/v1/content-access/me/overview", headers=HOST)
    assert response.status_code == 200
    # Ответ собран заново, без общего подключения; общая транзакция при этом одна и закрыта.
    assert response.json() == {"ok": True, "source": "separate"}
    assert calls == [db.conns[0], None]
    assert [call.kwargs["conn"] for call in loader.await_args_list] == [db.conns[0], None]
    assert db.opened == 1


@pytest.mark.asyncio
async def test_overview_keeps_the_shared_answer_when_the_transaction_is_healthy(client, overview_route, monkeypatch):
    db, loader = overview_route
    overview = AsyncMock(return_value={"ok": True, "source": "shared"})
    monkeypatch.setattr("app.cabinet.routes.load_overview", overview)
    response = await client.get("/api/v1/content-access/me/overview", headers=HOST)
    assert response.json() == {"ok": True, "source": "shared"}
    assert overview.await_count == 1 and loader.await_count == 1 and db.opened == 1


@pytest.mark.asyncio
async def test_overview_without_a_session_takes_no_connection(client, monkeypatch):
    db = FakeDb()
    monkeypatch.setattr("app.cabinet.routes.tenant_connection", db.tenant_connection)
    response = await client.get("/api/v1/content-access/me/overview", headers=HOST)
    assert response.status_code == 401
    assert db.opened == 0


@pytest.mark.asyncio
async def test_identity_conflict_on_the_shared_connection_is_still_409(client, overview_route):
    from app.cabinet.service import CabinetError

    db, loader = overview_route
    loader.side_effect = CabinetError("identity_conflict", 409)
    response = await client.get("/api/v1/content-access/me/overview", headers=HOST)
    assert response.status_code == 409 and response.json()["error"] == "identity_conflict"
    assert loader.await_count == 1  # конфликт — не повод пересобирать ответ по-старому


# ---- CRM: контекст и чтение маршрута на одной транзакции ----------------------------------------


READ_ROUTES = [
    ("/me", None),
    ("/today", "today_view"),
    ("/pipeline", "pipeline"),
    ("/contacts/c1", "get_contact"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path, reader", READ_ROUTES)
async def test_crm_read_routes_share_one_connection_with_the_context(crm_app, path, reader):
    db = FakeDb()
    viewer = AsyncMock(return_value=PAID)
    account = AsyncMock(return_value=ACCOUNT)
    extra = [
        patch("app.crm.routes.tenant_connection", db.tenant_connection),
        patch("app.crm.routes.load_viewer", viewer),
        patch("app.crm.routes.get_or_create_account", account),
    ]
    read = None
    if reader:
        read = AsyncMock(return_value={"columns": [], "total": 0, "groups": [], "id": "c1"})
        extra.append(patch(f"app.crm.routes.{reader}", read))
    response = await _call(crm_app, "GET", path, extra=extra)
    assert response.status_code == 200
    assert db.opened == 1
    conn = db.conns[0]
    assert viewer.await_args.kwargs["conn"] is conn
    assert account.await_args.kwargs["conn"] is conn
    if read is not None:
        assert read.await_args.kwargs["conn"] is conn


@pytest.mark.asyncio
@pytest.mark.parametrize("path, reader", READ_ROUTES)
async def test_crm_read_routes_refuse_before_the_handler_reads_and_without_an_account(crm_app, path, reader):
    db = FakeDb()
    account = AsyncMock(return_value=ACCOUNT)
    read = AsyncMock(return_value={})
    extra = [
        patch("app.crm.routes.tenant_connection", db.tenant_connection),
        patch("app.crm.routes.load_viewer", AsyncMock(return_value=UNPAID)),
        patch("app.crm.routes.get_or_create_account", account),
    ]
    if reader:
        extra.append(patch(f"app.crm.routes.{reader}", read))
    response = await _call(crm_app, "GET", path, viewer=UNPAID, extra=extra)
    assert response.status_code == 402 and response.json()["error"] == "pro_required"
    account.assert_not_awaited()  # аккаунт CRM не заводится тому, кому отказано
    read.assert_not_awaited()


@pytest.mark.asyncio
async def test_crm_routes_without_a_session_take_no_connection(crm_app):
    db = FakeDb()
    response = await _call(crm_app, "GET", "/today", viewer=None, extra=[patch("app.crm.routes.tenant_connection", db.tenant_connection)])
    assert response.status_code == 401 and db.opened == 0


@pytest.mark.asyncio
async def test_crm_write_routes_keep_separate_transactions(crm_app):
    db = FakeDb()
    viewer = AsyncMock(return_value=PAID)
    account = AsyncMock(return_value=ACCOUNT)
    create = AsyncMock(return_value={"id": "c9", "name": "Анна"})
    response = await _call(
        crm_app, "POST", "/contacts", json={"name": "Анна"},
        extra=[
            patch("app.crm.routes.tenant_connection", db.tenant_connection),
            patch("app.crm.routes.load_viewer", viewer),
            patch("app.crm.routes.get_or_create_account", account),
            patch("app.crm.routes.create_contact", create),
        ],
    )
    assert response.status_code == 201
    # Запись и аккаунт под неё не делят транзакцию с проверкой PRO: поведение прежнее.
    assert db.opened == 0
    assert viewer.await_args.kwargs["conn"] is None and account.await_args.kwargs["conn"] is None
