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

SCHEMA = """
create extension if not exists pgcrypto;

create table lead_actors (
  actor_id text primary key,
  tenant_id text not null,
  display_name text not null,
  telegram_user_id bigint,
  active boolean not null default true
);

create table referral_profiles (
  ref_code text primary key,
  tenant_id text not null,
  owner_id text not null references lead_actors(actor_id),
  display_mode text not null default 'named',
  public_profile jsonb not null default '{}'::jsonb,
  country_code text,
  region_code text,
  enabled boolean not null default true,
  profile_version integer not null default 1
);

create table service_locations (
  service_location_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  country_code text not null,
  city text not null,
  operator_actor_id text,
  enabled boolean not null default false,
  verified_at timestamptz,
  created_at timestamptz not null default now()
);

create table website_leads (
  lead_id uuid primary key default gen_random_uuid(),
  public_id text not null default ('L-' || upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 8))),
  tenant_id text not null,
  name text not null,
  contact text not null,
  comment text,
  product_name text not null,
  product_sku text,
  product_variant text,
  page_url text,
  initial_ref_code text,
  first_ref_code text,
  active_ref_code text,
  attributed_owner_id text not null,
  assigned_owner_id text not null,
  ref_profile_version integer,
  service_location_id uuid,
  country_code text,
  city text,
  idempotency_key text not null,
  consent_version text not null,
  metadata jsonb not null default '{}'::jsonb,
  marketing_consent boolean not null default false,
  marketing_consent_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, idempotency_key)
);

create table website_lead_owner_history (
  id bigserial primary key,
  lead_id uuid not null,
  tenant_id text not null,
  owner_id text not null,
  action text not null,
  changed_by text not null,
  reason text
);

create table website_lead_status_history (
  id bigserial primary key,
  lead_id uuid not null,
  tenant_id text not null,
  old_status text,
  new_status text not null,
  changed_by_actor_id text,
  reason text
);
"""


def local_admin_dsn() -> str:
    dsn = os.getenv("PARTNER_SUBSCRIPTIONS_TEST_ADMIN_DSN", "").strip()
    if not dsn:
        pytest.skip("PARTNER_SUBSCRIPTIONS_TEST_ADMIN_DSN is not configured")
    params = conninfo_to_dict(dsn)
    if params.get("host") not in {"127.0.0.1", "localhost"}:
        pytest.fail("lead-routing integration test refuses non-local PostgreSQL")
    return dsn


@pytest.mark.integration
def test_active_grace_and_suspended_lead_routing_and_public_ref():
    admin_dsn = local_admin_dsn()
    dbname = f"whieda_partner_routing_{uuid.uuid4().hex[:10]}"
    params = conninfo_to_dict(admin_dsn)
    params["dbname"] = dbname
    database_dsn = make_conninfo(**params)

    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("create database {}").format(sql.Identifier(dbname)))

    old_database_url = os.environ.get("PLATFORM_DATABASE_URL")
    old_owner = os.environ.get("PLATFORM_ORGANIC_OWNER_ID")
    try:
        with psycopg.connect(database_dsn, autocommit=True) as conn:
            conn.execute((SQL_DIR / "platform_tenant_registry_v1.sql").read_text(encoding="utf-8"))
            conn.execute((SQL_DIR / "platform_tenant_rls_v1.sql").read_text(encoding="utf-8"))
            conn.execute(SCHEMA)
            conn.execute(
                (SQL_DIR / "platform_partner_subscriptions_v1.sql").read_text(encoding="utf-8")
            )
            conn.execute(
                """
                insert into lead_actors (actor_id, tenant_id, display_name) values
                  ('organic-owner', 'whieda', 'Organic'),
                  ('active-owner', 'whieda', 'Active'),
                  ('grace-owner', 'whieda', 'Grace'),
                  ('suspended-owner', 'whieda', 'Suspended');

                insert into referral_profiles (
                  ref_code, tenant_id, owner_id, public_profile, profile_version
                ) values
                  ('active-ref', 'whieda', 'active-owner', '{"subdomain":"active"}', 1),
                  ('grace-ref', 'whieda', 'grace-owner', '{"subdomain":"grace"}', 2),
                  ('suspended-ref', 'whieda', 'suspended-owner', '{"subdomain":"suspended"}', 3);

                insert into partner_subscriptions (tenant_id, ref_code, paid_until) values
                  ('whieda', 'active-ref', now() + interval '1 day'),
                  ('whieda', 'grace-ref', now() - interval '1 day'),
                  ('whieda', 'suspended-ref', now() - interval '4 days');
                """
            )

        async def proof() -> None:
            os.environ["PLATFORM_DATABASE_URL"] = database_dsn
            os.environ["PLATFORM_ORGANIC_OWNER_ID"] = "organic-owner"
            from app.db import close_pool, init_pool, tenant_connection
            from app.leads.service import parse_lead_body, save_lead
            from app.ref.service import load_public_ref
            from app.settings import get_settings

            get_settings.cache_clear()
            await close_pool()
            await init_pool()
            try:
                for ref_code in ("active-ref", "grace-ref", "suspended-ref"):
                    lead = parse_lead_body(
                        {
                            "name": ref_code,
                            "contact": "test",
                            "product": "test product",
                            "idempotency_key": f"routing-{ref_code}",
                            "initial_ref": ref_code,
                            "active_ref": ref_code,
                            # Галочка рассылки (38-ФЗ ст. 18): только у одной заявки.
                            **({"marketing_consent": "on"} if ref_code == "active-ref" else {}),
                        },
                        tenant_id="whieda",
                    )
                    await save_lead(lead)

                async with tenant_connection("whieda") as conn:
                    rows = await conn.execute(
                        """
                        select name, initial_ref_code, first_ref_code, active_ref_code,
                               attributed_owner_id, assigned_owner_id,
                               marketing_consent, marketing_consent_at
                        from website_leads
                        order by name
                        """
                    )
                    saved = {row["name"]: row async for row in rows}

                assert saved["active-ref"]["assigned_owner_id"] == "active-owner"
                assert saved["grace-ref"]["assigned_owner_id"] == "grace-owner"
                suspended = saved["suspended-ref"]
                assert suspended["initial_ref_code"] == "suspended-ref"
                assert suspended["first_ref_code"] == "suspended-ref"
                assert suspended["active_ref_code"] is None
                assert suspended["assigned_owner_id"] == "organic-owner"
                assert suspended["attributed_owner_id"] == "organic-owner"

                assert saved["active-ref"]["marketing_consent"] is True
                assert saved["active-ref"]["marketing_consent_at"] is not None
                for name in ("grace-ref", "suspended-ref"):
                    assert saved[name]["marketing_consent"] is False
                    assert saved[name]["marketing_consent_at"] is None

                assert await load_public_ref("whieda", "active-ref") is not None
                assert await load_public_ref("whieda", "grace-ref") is not None
                assert await load_public_ref("whieda", "suspended-ref") is None
            finally:
                await close_pool()

        asyncio.run(proof(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    finally:
        if old_database_url is None:
            os.environ.pop("PLATFORM_DATABASE_URL", None)
        else:
            os.environ["PLATFORM_DATABASE_URL"] = old_database_url
        if old_owner is None:
            os.environ.pop("PLATFORM_ORGANIC_OWNER_ID", None)
        else:
            os.environ["PLATFORM_ORGANIC_OWNER_ID"] = old_owner
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                "select pg_terminate_backend(pid) from pg_stat_activity "
                "where datname = %s and pid <> pg_backend_pid()",
                (dbname,),
            )
            admin.execute(sql.SQL("drop database if exists {}").format(sql.Identifier(dbname)))
