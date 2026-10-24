"""«/content-access/me» одной транзакцией на настоящей PostgreSQL (24.10.2026).

Настоящий SQL четырёх чтений (подписка, счёт, способы входа, почта для заявок) под RLS роли
приложения; считаются подключения, взятые из пула на весь запрос. Ответ совпадает с тем, что дают
четыре отдельные транзакции; а когда чтение способов входа падает (таблицы нет), общая транзакция
обрывается, и почта для заявок всё равно доходит отдельным чтением."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

from tests.postgres_testkit import MIGRATIONS, temporary_database

USER = 72001

SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id) values
  ('omango', 'whieda', 'Ольга', 72001, '72001');
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, public_profile, enabled) values
  ('omango', 'whieda', 'omango', 'named', '{"display_name": "Ольга", "lead_email": "leads@example.org"}', true);
insert into partner_subscriptions (tenant_id, ref_code, paid_until) values ('whieda', 'omango', now() + interval '20 days');
insert into partner_bonus_ledger (tenant_id, actor_id, entry_type, amount_minor, currency, product_code, idempotency_key, description)
  values ('whieda', 'omango', 'credit', 600, 'WUSD', 'platform_subscription', 'k1', 'Referral bonus');
insert into account_identities (tenant_id, telegram_user_id, provider, external_id, email, linked_by) values
  ('whieda', 72001, 'google', 'g-1', 'olga@example.org', 'proof');
"""


class _CountingPool:
    """Пул приложения, который считает, сколько подключений взяли."""

    def __init__(self, pool) -> None:
        self._pool = pool
        self.checkouts = 0

    def connection(self, *args, **kwargs):
        self.checkouts += 1
        return self._pool.connection(*args, **kwargs)


def _seed(db) -> None:
    with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
        db.apply_migrations(conn, MIGRATIONS)
        conn.execute(SEED)
        db.grant_api_role(conn)


async def _me(counting: _CountingPool) -> tuple[dict, int]:
    from app.main import create_app
    from app.tenancy import TenantContext

    async def resolve(_host: str) -> TenantContext:
        return TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                             entitlements={"structure_basic": True})

    async def session(*_args, **_kwargs) -> dict:
        return {"session_id": "s1", "tenant_id": "whieda", "scope": "telegram_verified",
                "expires_at": datetime.now(timezone.utc) + timedelta(days=1), "telegram_user_id": USER}

    with (
        patch("app.tenancy.resolve_tenant_from_host", resolve),
        patch("app.content_access.routes.read_session_cookie", return_value="raw"),
        patch("app.content_access.routes.validate_content_session", session),
        patch("app.db.get_pool", return_value=counting),
    ):
        app = create_app()
        app.state.http_client = AsyncMock()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               headers={"host": "wwc.best"}) as client:
            before = counting.checkouts
            response = await client.get("/api/v1/content-access/me")
    assert response.status_code == 200, response.text
    return response.json(), counting.checkouts - before


@pytest.mark.integration
def test_me_in_one_transaction_matches_four_separate_ones():
    with temporary_database("whieda_me_one_tx") as db:
        _seed(db)

        async def proof() -> None:
            from fastapi.encoders import jsonable_encoder

            from app.content_access.account import load_site_account
            from app.content_access.service import format_me_payload
            from app.db import get_pool
            from app.lead_email.service import lead_email_of
            from app.login_identities.service import list_login_methods
            from app.subscriptions.service import resolve_partner_subscription_by_telegram_user_id

            counting = _CountingPool(get_pool())
            body, checkouts = await _me(counting)

            assert checkouts == 1
            assert body["partner_paid"] is True
            assert body["subscription_status"] == "active"
            assert body["account"]["balance"] == {"currency": "WWC$", "amount_minor": 600}
            assert [method["provider"] for method in body["login_methods"]] == ["telegram", "google"]
            assert body["lead_email"] == "leads@example.org"

            # То же самое, как оно считалось раньше: по транзакции на каждое чтение.
            session = {"session_id": "s1", "tenant_id": "whieda", "scope": "telegram_verified",
                       "expires_at": datetime.fromisoformat(body["expires_at"]), "telegram_user_id": USER}
            subscription = await resolve_partner_subscription_by_telegram_user_id("whieda", USER, on_ambiguous="best")
            account = await load_site_account("whieda", USER)
            old_way = format_me_payload(session, partner_subscription=subscription, account=account)
            old_way["login_methods"] = await list_login_methods("whieda", USER)
            old_way["lead_email"] = await lead_email_of("whieda", USER)
            assert body == jsonable_encoder(old_way)

        db.run_with_app(proof)


@pytest.mark.integration
def test_broken_login_methods_read_leaves_the_account_and_the_lead_email_intact():
    with temporary_database("whieda_me_one_tx_fail") as db:
        _seed(db)
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            conn.execute("drop table account_identities cascade")  # чтение способов входа упадёт на настоящей базе

        async def proof() -> None:
            from app.db import get_pool

            counting = _CountingPool(get_pool())
            body, checkouts = await _me(counting)

            assert body["login_methods"] == [{"provider": "telegram", "label": "Telegram", "linked_at": None}]
            assert body["partner_paid"] is True
            assert body["account"]["balance"]["amount_minor"] == 600
            assert body["lead_email"] == "leads@example.org"  # через отдельное чтение
            assert checkouts == 2

        db.run_with_app(proof)
