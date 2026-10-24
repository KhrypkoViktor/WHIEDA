"""/me/overview и чтения CRM одной транзакцией на настоящей PostgreSQL (24.10.2026).

Настоящий SQL под RLS роли приложения. Считаются подключения, взятые из пула на весь запрос.
Ответ совпадает с тем, что дают отдельные транзакции; записи запроса (актор, код приглашения,
аккаунт CRM) остаются после ответа; а когда необязательное чтение падает на настоящей базе,
главная собирается заново по-старому и ничего из записанного не теряется."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import psycopg
import pytest
from fastapi.encoders import jsonable_encoder
from httpx import ASGITransport, AsyncClient

from tests.postgres_testkit import MIGRATIONS, temporary_database

PRO, GUEST, NEWBIE, SECOND_PRO = 72001, 72002, 72003, 72005
ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
CONTACT_ID = "22222222-2222-4222-8222-222222222221"
SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id) values
  ('omango', 'whieda', 'Ольга', 72001, '72001'),
  ('olga', 'whieda', 'Ольга Самцова', 72005, '72005'),
  ('telegram:whieda:72002', 'whieda', '@guest', 72002, '72002');
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, public_profile, enabled) values
  ('omango', 'whieda', 'omango', 'named', '{"display_name": "Ольга"}', true),
  ('olga-samtsova', 'whieda', 'olga', 'named', '{"display_name": "Ольга Самцова"}', true);
insert into partner_subscriptions (tenant_id, ref_code, paid_until) values
  ('whieda', 'omango', now() + interval '20 days'), ('whieda', 'olga-samtsova', now() + interval '20 days');
insert into partner_bonus_ledger (tenant_id, actor_id, entry_type, amount_minor, currency, product_code, idempotency_key, description)
  values ('whieda', 'omango', 'credit', 600, 'WUSD', 'platform_subscription', 'k1', 'Referral bonus');
insert into referral_invite_codes (tenant_id, invite_code, inviter_actor_id) values
  ('whieda', 'invite-code-omango', 'omango'), ('whieda', 'invite-code-guest', 'telegram:whieda:72002');
insert into platform_accounts (tenant_id, account_id, telegram_user_id) values ('whieda', '11111111-1111-4111-8111-111111111111', 72001);
insert into crm_contacts (tenant_id, contact_id, account_id, name, status, next_step, next_at) values
  ('whieda', '22222222-2222-4222-8222-222222222221', '11111111-1111-4111-8111-111111111111', 'Анна', 'new', 'invite', current_date),
  ('whieda', '22222222-2222-4222-8222-222222222222', '11111111-1111-4111-8111-111111111111', 'Борис', 'invited', 'result', current_date - 1),
  ('whieda', '22222222-2222-4222-8222-222222222223', '11111111-1111-4111-8111-111111111111', 'Вера', 'presented', 'decide', current_date + 3);
"""


class _CountingPool:
    """Пул приложения, который считает, сколько подключений взяли."""

    def __init__(self, pool) -> None:
        self._pool = pool
        self.checkouts = 0

    def connection(self, *args, **kwargs):
        self.checkouts += 1
        return self._pool.connection(*args, **kwargs)


@pytest.fixture
def site_env(monkeypatch):
    from app.settings import get_settings

    monkeypatch.setenv("PLATFORM_TELEGRAM_BOT_USERNAME", "WHIEDA_Advisor_bot")  # без него код приглашения не читается
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "*")
    monkeypatch.setenv("PLATFORM_CONTENT_COOKIE_SECURE", "false")
    monkeypatch.delenv("PLATFORM_DISABLED_FEATURES", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _seed(db) -> None:
    with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
        db.apply_migrations(conn, MIGRATIONS)
        conn.execute(SEED)
        db.grant_api_role(conn)


def _admin(db, query: str, params: tuple = ()) -> list[tuple]:
    with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else []


async def _get(counting: _CountingPool, path: str, user: int) -> tuple[int, dict, int]:
    from app.main import create_app
    from app.tenancy import TenantContext

    async def resolve(_host: str) -> TenantContext:
        return TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                             entitlements={"structure_basic": True, "crm": True})

    async def session(*_args, **_kwargs) -> dict:
        return {"session_id": "s1", "tenant_id": "whieda", "scope": "telegram_verified",
                "expires_at": datetime.now(timezone.utc) + timedelta(days=1), "telegram_user_id": user}

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
            response = await client.get(path)
    body = response.json() if response.content else {}
    return response.status_code, body, counting.checkouts - before


def _same(left: object, right: object) -> bool:
    """Ответы сравниваются без времени: «сейчас» у двух чтений разное."""
    def plain(value: object) -> str:
        text = json.dumps(jsonable_encoder(value), ensure_ascii=False, sort_keys=True)
        return re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)?", "<ts>", text)

    return plain(left) == plain(right)


OVERVIEW = "/api/v1/content-access/me/overview"
CRM = "/api/v1/content-access/crm"


# ---- /me/overview ----------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("user", [PRO, GUEST])
def test_overview_in_one_transaction_matches_separate_ones(site_env, user):
    with temporary_database("whieda_overview_one_tx") as db:
        _seed(db)

        async def proof() -> None:
            from app.cabinet.service import load_overview, load_person
            from app.db import get_pool

            counting = _CountingPool(get_pool())
            status, body, checkouts = await _get(counting, OVERVIEW, user)

            assert status == 200, body
            assert checkouts == 1
            assert body["invite"]["link"] == (
                "https://t.me/WHIEDA_Advisor_bot?start=ref_" + ("invite-code-omango" if user == PRO else "invite-code-guest")
            )
            if user == PRO:
                assert body["tier"] == "pro"
                assert body["counters"]["crm_contacts"] == 3 and body["counters"]["crm_today"] == 2
                assert body["settings"]["timezone"] == "Europe/Moscow"
            else:
                assert body["tier"] == "free" and body["counters"]["crm_today"] is None

            # То же самое, как оно считалось раньше: транзакция на каждое чтение.
            person = await load_person("whieda", user)
            old_way = await load_overview("whieda", person, bot_username="WHIEDA_Advisor_bot", crm_entitled=True)
            assert _same(body, old_way)

        db.run_with_app(proof)


@pytest.mark.integration
def test_a_person_created_by_the_overview_is_committed_with_the_invite_code(site_env):
    with temporary_database("whieda_overview_new") as db:
        _seed(db)

        async def proof() -> None:
            from app.db import get_pool

            counting = _CountingPool(get_pool())
            status, body, checkouts = await _get(counting, OVERVIEW, NEWBIE)
            assert status == 200, body
            assert checkouts == 1

            actor = f"telegram:whieda:{NEWBIE}"
            assert _admin(db, "select actor_id from lead_actors where telegram_user_id = %s", (NEWBIE,)) == [(actor,)]
            codes = _admin(db, "select invite_code from referral_invite_codes where inviter_actor_id = %s", (actor,))
            assert len(codes) == 1
            assert body["invite"]["link"] == f"https://t.me/WHIEDA_Advisor_bot?start=ref_{codes[0][0]}"

            # Повторный запрос ничего не создаёт заново.
            status, again, _ = await _get(counting, OVERVIEW, NEWBIE)
            assert status == 200 and again["invite"] == body["invite"]
            assert _admin(db, "select count(*) from lead_actors where telegram_user_id = %s", (NEWBIE,)) == [(1,)]

        db.run_with_app(proof)


@pytest.mark.integration
def test_a_failed_optional_read_does_not_lose_the_new_actor_and_the_invite_code(site_env):
    """Таблицы согласия на рассылку нет: чтение падает на настоящей базе, общая транзакция
    обрывается, её COMMIT был бы ROLLBACK. Ответ собирается заново, а актор и код — в базе."""
    with temporary_database("whieda_overview_fail") as db:
        _seed(db)
        _admin(db, "drop table telegram_marketing_consents cascade")

        async def proof() -> None:
            from app.db import get_pool

            counting = _CountingPool(get_pool())
            status, body, checkouts = await _get(counting, OVERVIEW, NEWBIE)
            assert status == 200, body
            assert body["settings"]["marketing_opt_in"] is None  # не прочиталось — как и раньше, не ошибка
            assert checkouts > 1  # повтор по-старому, отдельными транзакциями

            actor = f"telegram:whieda:{NEWBIE}"
            assert _admin(db, "select actor_id from lead_actors where telegram_user_id = %s", (NEWBIE,)) == [(actor,)]
            codes = _admin(db, "select invite_code from referral_invite_codes where inviter_actor_id = %s", (actor,))
            assert len(codes) == 1  # ссылка в ответе указывает на код, который есть в базе
            assert body["invite"]["link"] == f"https://t.me/WHIEDA_Advisor_bot?start=ref_{codes[0][0]}"

        db.run_with_app(proof)


@pytest.mark.integration
def test_a_failed_crm_read_leaves_the_rest_of_the_overview_intact(site_env):
    with temporary_database("whieda_overview_crm_fail") as db:
        _seed(db)
        _admin(db, "drop table crm_contacts cascade")

        async def proof() -> None:
            from app.db import get_pool

            counting = _CountingPool(get_pool())
            status, body, checkouts = await _get(counting, OVERVIEW, PRO)
            assert status == 200, body
            assert checkouts > 1
            assert body["counters"]["crm_contacts"] == 0 and body["counters"]["crm_today"] is None
            assert body["settings"]["timezone"] == "Europe/Moscow"  # читается не из упавшей таблицы
            assert body["invite"]["link"].endswith("ref_invite-code-omango")
            assert body["account"]["balance"]["amount_minor"] == 600

        db.run_with_app(proof)


# ---- CRM ---------------------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "path, reader",
    [("/me", None), ("/today", "today"), ("/pipeline", "pipeline"), (f"/contacts/{CONTACT_ID}", "contact")],
)
def test_crm_read_in_one_transaction_matches_separate_ones(site_env, path, reader):
    with temporary_database("whieda_crm_one_tx") as db:
        _seed(db)

        async def proof() -> None:
            from app.crm.queries import pipeline
            from app.crm.routes import _context_for
            from app.crm.service import get_contact, today_view
            from app.db import get_pool
            from app.tenancy import TenantContext

            counting = _CountingPool(get_pool())
            status, body, checkouts = await _get(counting, CRM + path, PRO)
            assert status == 200, body
            assert checkouts == 1

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"crm": True})
            ctx = await _context_for(tenant, PRO)  # прежний путь: транзакция на каждое чтение
            if reader == "today":
                expected = {"ok": True, **await today_view("whieda", ctx.account)}
                assert len(body["groups"]) >= 1
            elif reader == "pipeline":
                expected = {"ok": True, **await pipeline("whieda", ctx.account)}
                assert body["total"] == 3
            elif reader == "contact":
                expected = {"ok": True, "contact": await get_contact("whieda", ctx.account, CONTACT_ID)}
                assert body["contact"]["name"] == "Анна"
            else:
                assert body["account_id"] == ACCOUNT_ID and body["my_name"] == "Ольга"
                expected = None
            if expected is not None:
                assert _same(body, expected)

        db.run_with_app(proof)


@pytest.mark.integration
def test_the_first_crm_request_creates_the_account_and_commits_it(site_env):
    with temporary_database("whieda_crm_account") as db:
        _seed(db)
        assert _admin(db, "select count(*) from platform_accounts where telegram_user_id = %s", (SECOND_PRO,)) == [(0,)]

        async def proof() -> None:
            from app.db import get_pool

            counting = _CountingPool(get_pool())
            status, body, checkouts = await _get(counting, CRM + "/today", SECOND_PRO)
            assert status == 200, body
            assert checkouts == 1
            rows = _admin(db, "select account_id::text from platform_accounts where telegram_user_id = %s", (SECOND_PRO,))
            assert len(rows) == 1
            status, me, _ = await _get(counting, CRM + "/me", SECOND_PRO)
            assert status == 200 and me["account_id"] == rows[0][0]

        db.run_with_app(proof)


@pytest.mark.integration
def test_a_refusal_creates_no_account_and_a_missing_card_is_404(site_env):
    with temporary_database("whieda_crm_refuse") as db:
        _seed(db)
        # Гость без сайта и подписки: 402, аккаунт CRM ему не заводится.
        _admin(db, "delete from platform_accounts where telegram_user_id = %s", (GUEST,))

        async def proof() -> None:
            from app.db import get_pool

            counting = _CountingPool(get_pool())
            status, body, _ = await _get(counting, CRM + "/today", GUEST)
            assert status == 402 and body["error"] == "pro_required"
            assert _admin(db, "select count(*) from platform_accounts where telegram_user_id = %s", (GUEST,)) == [(0,)]

            # Чужая или несуществующая карточка — 404; аккаунт, заведённый этим же запросом, откатывается
            # вместе с ним (следующий запрос заведёт его снова), строк-сирот нет.
            status, body, checkouts = await _get(counting, CRM + "/contacts/33333333-3333-4333-8333-333333333333", SECOND_PRO)
            assert status == 404 and body["error"] == "contact_not_found"
            assert checkouts == 1
            assert _admin(db, "select count(*) from platform_accounts where telegram_user_id = %s", (SECOND_PRO,)) == [(0,)]

        db.run_with_app(proof)
