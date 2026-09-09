from __future__ import annotations

import asyncio
import os
import selectors
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


ROOT = Path(__file__).resolve().parents[3]
SQL_DIR = ROOT / "postgres" / "sql"
MIGRATIONS = (
    "platform_tenant_registry_v1.sql",
    "platform_tenant_rls_v1.sql",
    "platform_partner_subscriptions_v1.sql",
)

LEADS_PREREQUISITES = """
create table lead_actors (
  actor_id text primary key,
  tenant_id text not null,
  display_name text not null,
  telegram_chat_id text,
  telegram_username text,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, telegram_chat_id)
);

create table referral_profiles (
  ref_code text primary key,
  tenant_id text not null,
  owner_id text not null references lead_actors(actor_id),
  display_mode text not null check (display_mode in ('anonymous', 'named')),
  public_profile jsonb not null default '{}'::jsonb,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
"""


def _local_admin_dsn() -> str:
    dsn = os.getenv("PARTNER_SUBSCRIPTIONS_TEST_ADMIN_DSN", "").strip()
    if not dsn:
        pytest.skip("PARTNER_SUBSCRIPTIONS_TEST_ADMIN_DSN is not configured")
    params = conninfo_to_dict(dsn)
    if params.get("host") not in {"127.0.0.1", "localhost"}:
        pytest.fail("subscription integration test refuses non-local PostgreSQL")
    return dsn


def _database_dsn(admin_dsn: str, dbname: str, *, user: str | None = None, password: str | None = None) -> str:
    params = conninfo_to_dict(admin_dsn)
    params["dbname"] = dbname
    if user is not None:
        params["user"] = user
    if password is not None:
        params["password"] = password
    return make_conninfo(**params)


@pytest.mark.integration
def test_partner_subscription_postgres_rls_idempotency_and_concurrency():
    admin_dsn = _local_admin_dsn()
    suffix = uuid.uuid4().hex[:10]
    dbname = f"whieda_partner_subscriptions_{suffix}"
    role = f"whieda_subscriptions_api_{suffix}"
    password = f"local_{suffix}"

    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(dbname)))
        admin.execute(
            sql.SQL(
                "create role {} login password {} nosuperuser nocreatedb nocreaterole "
                "noinherit nobypassrls"
            ).format(sql.Identifier(role), sql.Literal(password))
        )

    database_dsn = _database_dsn(admin_dsn, dbname)
    api_dsn = _database_dsn(admin_dsn, dbname, user=role, password=password)

    try:
        with psycopg.connect(database_dsn, autocommit=True) as conn:
            for name in MIGRATIONS[:2]:
                conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))
            conn.execute(LEADS_PREREQUISITES)
            for name in MIGRATIONS[2:]:
                conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))
            for name in MIGRATIONS[2:]:
                conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))

            conn.execute(
                """
                insert into lead_actors (actor_id, tenant_id, display_name)
                values
                  ('proof-whieda-owner', 'whieda', 'WHIEDA proof owner'),
                  ('proof-acme-owner', 'test-acme', 'Acme proof owner')
                on conflict (actor_id) do nothing;

                insert into referral_profiles (
                  ref_code, tenant_id, owner_id, display_mode, public_profile, enabled
                ) values
                  ('proof-whieda', 'whieda', 'proof-whieda-owner', 'named', '{}', true),
                  ('proof-acme', 'test-acme', 'proof-acme-owner', 'named', '{}', true)
                on conflict (ref_code) do nothing;

                insert into partner_subscriptions (tenant_id, ref_code, paid_until)
                values ('test-acme', 'proof-acme', now() + interval '3 months');
                """
            )
            conn.execute(
                sql.SQL("grant connect on database {} to {}").format(
                    sql.Identifier(dbname), sql.Identifier(role)
                )
            )
            conn.execute(sql.SQL("grant usage on schema public to {}").format(sql.Identifier(role)))
            conn.execute(
                sql.SQL(
                    "grant select, insert, update, delete on all tables in schema public to {}"
                ).format(sql.Identifier(role))
            )
            conn.execute(
                sql.SQL("grant usage, select on all sequences in schema public to {}").format(
                    sql.Identifier(role)
                )
            )
            conn.execute(
                sql.SQL("grant execute on all functions in schema public to {}").format(
                    sql.Identifier(role)
                )
            )

            states = conn.execute(
                """
                select
                  partner_subscription_state('2026-09-22 00:00:00+03', '2026-09-21 23:59:59.999999+03'),
                  partner_subscription_state('2026-09-22 00:00:00+03', '2026-09-22 00:00:00+03'),
                  partner_subscription_state('2026-09-22 00:00:00+03', '2026-09-25 00:00:00+03')
                """
            ).fetchone()
            assert states == ("active", "grace", "suspended")

        with psycopg.connect(api_dsn) as conn:
            conn.execute("select platform_set_tenant_context('whieda')")
            foreign_count = conn.execute(
                "select count(*) from partner_subscriptions where tenant_id = 'test-acme'"
            ).fetchone()[0]
            assert foreign_count == 0

        with psycopg.connect(api_dsn) as conn:
            conn.execute("select platform_set_tenant_context('whieda')")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "insert into partner_subscriptions (tenant_id, ref_code, paid_until) "
                    "values ('test-acme', 'proof-acme', now() + interval '3 months')"
                )

        async def service_proof() -> None:
            os.environ["PLATFORM_DATABASE_URL"] = api_dsn
            from app.db import close_pool, init_pool, tenant_connection
            from app.settings import get_settings
            from app.subscriptions.service import get_subscription, record_manual_payment

            get_settings.cache_clear()
            await close_pool()
            await init_pool()
            try:
                args = {
                    "ref_code": "proof-whieda",
                    "amount_minor": 300000,
                    "currency": "RUB",
                    "telegram_chat_id": 81001,
                    "telegram_message_id": 91001,
                    "telegram_user_id": 71001,
                }
                duplicate = await asyncio.gather(
                    record_manual_payment("whieda", **args),
                    record_manual_payment("whieda", **args),
                )
                assert sorted(row["idempotent"] for row in duplicate) == [False, True]

                distinct = await asyncio.gather(
                    record_manual_payment(
                        "whieda", **{**args, "telegram_message_id": 91002}
                    ),
                    record_manual_payment(
                        "whieda", **{**args, "telegram_message_id": 91003}
                    ),
                )
                periods = sorted(
                    ((row["period_start"], row["period_end"]) for row in distinct),
                    key=lambda value: value[0],
                )
                assert periods[0][1] == periods[1][0]
                subscription = await get_subscription("whieda", "proof-whieda")
                assert subscription is not None
                assert subscription["paid_until"] == periods[1][1]

                async with tenant_connection("whieda") as conn:
                    async with conn.cursor() as cur:
                        await cur.execute(
                            "select count(*) from partner_payment_ledger "
                            "where tenant_id = %s and ref_code = %s",
                            ("whieda", "proof-whieda"),
                        )
                        assert (await cur.fetchone())["count"] == 3
            finally:
                await close_pool()

        asyncio.run(
            service_proof(),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity "
                "where datname = %s and pid <> pg_backend_pid()",
                (dbname,),
            )
            admin.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(dbname)))
            admin.execute(sql.SQL("drop role if exists {}").format(sql.Identifier(role)))
