"""Throwaway PostgreSQL databases for the ``*_postgres.py`` integration tests.

Every test gets its own database and its own non-superuser, non-bypassrls role
(the shape of the production API user), the real migrations from
``postgres/sql``, and a teardown that drops both. The admin DSN comes from
``PARTNER_SUBSCRIPTIONS_TEST_ADMIN_DSN`` and must point at localhost;
``scripts/run_postgres_integration_tests.ps1`` provides one on a fresh cluster.
"""

from __future__ import annotations

import asyncio
import os
import selectors
import uuid
from collections.abc import Awaitable, Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[3]
SQL_DIR = ROOT / "postgres" / "sql"

# Order matters: tenant registry + RLS helpers first, then the legacy leads tables
# the platform migrations reference, then the platform migrations themselves.
MIGRATIONS = (
    "platform_tenant_registry_v1.sql",
    "platform_tenant_rls_v1.sql",
    "platform_partner_subscriptions_v1.sql",
    "platform_partner_subscription_currency_v2.sql",
    "platform_referral_bonuses_v1.sql",
    "platform_referral_bonus_redemptions_v2.sql",
    "platform_referral_admin_intents_v3.sql",
    "platform_partner_site_requests_v4.sql",
    "platform_partner_renewal_requests_v5.sql",
    "platform_partner_subscription_reminders_v6.sql",
    "platform_partner_products_v7.sql",
    "platform_partner_site_request_plans_v10.sql",
    "platform_lead_actor_channels_v11.sql",
    "platform_partner_site_request_contacts_v12.sql",
    "platform_renewal_services_v13.sql",
    "platform_academy_v1.sql",
    "platform_academy_shelf_v15.sql",
)
_PREREQUISITES_AFTER = 2  # LEADS_PREREQUISITES runs after this many migrations

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
  country_code text,
  region_code text,
  profile_version integer not null default 1,
  enabled boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
"""


def local_admin_dsn() -> str:
    dsn = os.getenv("PARTNER_SUBSCRIPTIONS_TEST_ADMIN_DSN", "").strip()
    if not dsn:
        pytest.skip("PARTNER_SUBSCRIPTIONS_TEST_ADMIN_DSN is not configured")
    params = conninfo_to_dict(dsn)
    if params.get("host") not in {"127.0.0.1", "localhost"}:
        pytest.fail("integration tests refuse non-local PostgreSQL")
    return dsn


def database_dsn(admin_dsn: str, dbname: str, *, user: str | None = None, password: str | None = None) -> str:
    params = conninfo_to_dict(admin_dsn)
    params["dbname"] = dbname
    if user is not None:
        params["user"] = user
    if password is not None:
        params["password"] = password
    return make_conninfo(**params)


@dataclass(frozen=True)
class DisposableDatabase:
    """One disposable database: ``admin_dsn`` owns it, ``api_dsn`` is the app role."""

    name: str
    role: str
    admin_dsn: str
    api_dsn: str

    def grant_api_role(self, conn: psycopg.Connection) -> None:
        for statement in (
            sql.SQL("grant connect on database {} to {}").format(sql.Identifier(self.name), sql.Identifier(self.role)),
            sql.SQL("grant usage on schema public to {}").format(sql.Identifier(self.role)),
            sql.SQL("grant select, insert, update, delete on all tables in schema public to {}").format(sql.Identifier(self.role)),
            sql.SQL("grant usage, select on all sequences in schema public to {}").format(sql.Identifier(self.role)),
            sql.SQL("grant execute on all functions in schema public to {}").format(sql.Identifier(self.role)),
        ):
            conn.execute(statement)

    def apply_migrations(self, conn: psycopg.Connection, names: Iterable[str] = MIGRATIONS, *, twice: bool = False) -> None:
        """Run the migrations in order; ``twice`` re-applies them to prove idempotence."""
        ordered = list(names)
        for index, name in enumerate(ordered):
            if index == _PREREQUISITES_AFTER:
                conn.execute(LEADS_PREREQUISITES)
            conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))
        if twice:
            for name in ordered[_PREREQUISITES_AFTER:]:
                conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))

    def run_with_app(self, proof: Callable[[], Awaitable[None]]) -> None:
        """Point the app's pool at ``api_dsn`` and run an async proof against it."""

        async def wrapped() -> None:
            os.environ["PLATFORM_DATABASE_URL"] = self.api_dsn
            from app.db import close_pool, init_pool
            from app.settings import get_settings

            get_settings.cache_clear()
            await close_pool()
            await init_pool()
            try:
                await proof()
            finally:
                await close_pool()

        asyncio.run(wrapped(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


@contextmanager
def temporary_database(prefix: str):
    admin_dsn = local_admin_dsn()
    suffix = uuid.uuid4().hex[:10]
    db = DisposableDatabase(
        name=f"{prefix}_{suffix}",
        role=f"{prefix}_api_{suffix}",
        admin_dsn=database_dsn(admin_dsn, f"{prefix}_{suffix}"),
        api_dsn=database_dsn(admin_dsn, f"{prefix}_{suffix}", user=f"{prefix}_api_{suffix}", password=f"local_{suffix}"),
    )
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(db.name)))
        admin.execute(
            sql.SQL(
                "create role {} login password {} nosuperuser nocreatedb nocreaterole noinherit nobypassrls"
            ).format(sql.Identifier(db.role), sql.Literal(f"local_{suffix}"))
        )
    try:
        yield db
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity where datname = %s and pid <> pg_backend_pid()",
                (db.name,),
            )
            admin.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(db.name)))
            admin.execute(sql.SQL("drop role if exists {}").format(sql.Identifier(db.role)))
