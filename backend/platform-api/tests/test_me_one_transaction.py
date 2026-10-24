"""«/content-access/me»: одна транзакция вместо четырёх (24.10.2026).

Четыре чтения по одному telegram_user_id (подписка, счёт, способы входа, почта для заявок)
идут на одном подключении. Подмена базы считает транзакции; ответ сравнивается с тем, что
давали четыре отдельные транзакции. Настоящий SQL — в ``test_me_one_transaction_postgres.py``."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.encoders import jsonable_encoder

import app.content_access.account as account_module
from app.content_access.account import load_site_account
from app.content_access.service import format_me_payload
from app.lead_email.service import lead_email_of
from app.login_identities.service import list_login_methods
from app.subscriptions.service import resolve_partner_subscription_by_telegram_user_id
from tests.fake_db import FakeDb

HOST = {"host": "wwc.best"}
USER = 999001
NOW = datetime.now(timezone.utc)
PAID_UNTIL = NOW + timedelta(days=20)


class _Me(FakeDb):
    def __init__(self) -> None:
        super().__init__(self._answer)
        self.identities_fail = False

    def _answer(self, query: str, params) -> list[dict]:
        if "to_regclass" in query:
            return [{"name": "partner_product_access"}]
        if "partner_bonus_ledger" in query:
            return [
                {
                    "actor_id": "omango",
                    "bonus_minor": 600,
                    "pro_paid_until": PAID_UNTIL,
                    "pro_ref_code": "omango",
                    "club_paid_until": None,
                }
            ]
        if "from account_identities" in query:
            if self.identities_fail:
                raise RuntimeError('relation "account_identities" does not exist')
            return [{"provider": "google", "email": "olga@example.org", "display_name": None, "linked_at": NOW}]
        if "lead_email" in query:
            return [{"ref_code": "omango", "lead_email": "leads@example.org"}]
        assert "partner_subscriptions" in query, query
        return [
            {
                "actor_id": "omango",
                "telegram_user_id": USER,
                "ref_code": "omango",
                "public_profile": {},
                "paid_until": PAID_UNTIL,
            }
        ]


def _session(telegram_user_id: int | None = USER) -> dict:
    return {
        "session_id": "s1",
        "tenant_id": "whieda",
        "scope": "telegram_verified",
        "expires_at": NOW + timedelta(days=30),
        "telegram_user_id": telegram_user_id,
    }


@pytest.fixture
def db(monkeypatch):
    fake = _Me()
    # Любая попытка открыть своё подключение у помощников считается — иначе «одна транзакция» ничего не значит.
    for module in (
        "app.content_access.routes",
        "app.content_access.account",
        "app.subscriptions.service",
        "app.login_identities.service",
        "app.lead_email.service",
    ):
        monkeypatch.setattr(f"{module}.tenant_connection", fake.tenant_connection)
    monkeypatch.setattr(account_module, "_club_table_present", None)
    return fake


async def _get_me(client, session: dict) -> dict:
    with patch("app.content_access.routes.read_session_cookie", return_value="raw"), patch(
        "app.content_access.routes.validate_content_session", AsyncMock(return_value=session)
    ):
        response = await client.get("/api/v1/content-access/me", headers=HOST)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    return response.json()


async def _four_separate_transactions(session: dict) -> dict:
    """Что отдавал «/me» до этого изменения: каждый помощник открывал свою транзакцию."""
    subscription = await resolve_partner_subscription_by_telegram_user_id("whieda", USER, on_ambiguous="best")
    account = await load_site_account("whieda", USER)
    payload = format_me_payload(session, partner_subscription=subscription, account=account)
    payload["login_methods"] = await list_login_methods("whieda", USER)
    payload["lead_email"] = await lead_email_of("whieda", USER)
    return jsonable_encoder(payload)


@pytest.mark.asyncio
async def test_me_reads_everything_in_one_transaction(client, db):
    body = await _get_me(client, _session())

    assert db.opened == 1
    assert len(db.statements) == 5  # подписка, to_regclass (раз за процесс), счёт, способы входа, почта
    assert body["partner_paid"] is True
    assert body["account"]["balance"] == {"currency": "WWC$", "amount_minor": 600}
    assert [method["provider"] for method in body["login_methods"]] == ["telegram", "google"]
    assert body["lead_email"] == "leads@example.org"
    assert "telegram_user_id" not in body


@pytest.mark.asyncio
async def test_me_answer_is_the_same_as_with_four_separate_transactions(client, db):
    session = _session()
    one_transaction = await _get_me(client, session)
    db_before = db.opened

    old_way = await _four_separate_transactions(session)

    assert db.opened - db_before == 4  # так было: по транзакции на помощника
    assert one_transaction == old_way
    assert list(one_transaction) == list(old_way)  # и порядок ключей тот же


@pytest.mark.asyncio
async def test_helpers_keep_their_own_transaction_when_called_without_conn(db):
    await resolve_partner_subscription_by_telegram_user_id("whieda", USER, on_ambiguous="best")
    await load_site_account("whieda", USER)
    await list_login_methods("whieda", USER)
    await lead_email_of("whieda", USER)

    assert db.opened == 4
    assert all(conn.tenant_id == "whieda" for conn in db.conns)


@pytest.mark.asyncio
async def test_helpers_use_the_connection_they_are_given(db):
    conn = db.open("whieda")
    opened_before = db.opened

    assert await resolve_partner_subscription_by_telegram_user_id("whieda", USER, on_ambiguous="best", conn=conn)
    assert await load_site_account("whieda", USER, conn=conn)
    assert [m["provider"] for m in await list_login_methods("whieda", USER, conn=conn)] == ["telegram", "google"]
    assert await lead_email_of("whieda", USER, conn=conn) == "leads@example.org"

    assert db.opened == opened_before  # новых транзакций нет
    assert len(conn.statements) == 5


@pytest.mark.asyncio
async def test_me_without_telegram_id_opens_no_transaction(client, db):
    body = await _get_me(client, _session(telegram_user_id=None))

    assert db.opened == 0
    assert body["account"] is None
    assert body["subscription_status"] == "no_subscription"
    assert body["login_methods"] == [{"provider": "telegram", "label": "Telegram", "linked_at": None}]
    assert body["lead_email"] is None


@pytest.mark.asyncio
async def test_me_survives_a_failing_lead_email_read(client, db):
    with patch("app.content_access.routes.lead_email_of", AsyncMock(side_effect=RuntimeError("boom"))):
        body = await _get_me(client, _session())

    assert body["lead_email"] is None
    assert [method["provider"] for method in body["login_methods"]] == ["telegram", "google"]
    assert body["partner_paid"] is True
    assert db.opened == 1


@pytest.mark.asyncio
async def test_failing_login_methods_do_not_take_the_lead_email_down_with_them(client, db):
    """Упавший запрос обрывает общую транзакцию: почту читаем отдельно, как раньше она читалась независимо."""
    db.identities_fail = True

    body = await _get_me(client, _session())

    assert body["login_methods"] == [{"provider": "telegram", "label": "Telegram", "linked_at": None}]
    assert body["lead_email"] == "leads@example.org"
    assert body["partner_paid"] is True
    assert db.opened == 2  # общая транзакция и отдельное чтение почты
    assert "lead_email" in db.conns[-1].statements[-1]


@pytest.mark.asyncio
async def test_failing_subscription_read_is_still_an_error(client, db):
    with patch(
        "app.content_access.routes.resolve_partner_subscription_by_telegram_user_id",
        AsyncMock(side_effect=RuntimeError("db down")),
    ), patch("app.content_access.routes.read_session_cookie", return_value="raw"), patch(
        "app.content_access.routes.validate_content_session", AsyncMock(return_value=_session())
    ):
        with pytest.raises(RuntimeError, match="db down"):
            await client.get("/api/v1/content-access/me", headers=HOST)
