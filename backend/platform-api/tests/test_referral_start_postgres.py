"""Telegram actor registration against the real lead_actors schema.

Production keeps ``(tenant_id, telegram_user_id)`` unique through a *partial*
index (``where telegram_user_id is not null``).  A plain ``on conflict`` target
does not match that index and PostgreSQL rejects the insert — which is exactly
how every new bot user failed on 2026-09-12.  Static tests cannot catch this;
only a real database can.
"""

from __future__ import annotations

import asyncio
import os
import selectors
import uuid

import psycopg
import pytest
from psycopg import sql

from tests.test_partner_subscriptions_postgres import (
    LEADS_PREREQUISITES,
    MIGRATIONS,
    SQL_DIR,
    _database_dsn,
    _local_admin_dsn,
)


@pytest.mark.integration
def test_new_telegram_users_register_and_attribute_on_live_schema():
    admin_dsn = _local_admin_dsn()
    suffix = uuid.uuid4().hex[:10]
    dbname = f"whieda_referral_start_{suffix}"
    role = f"whieda_referral_api_{suffix}"
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

            index = conn.execute(
                """
                select pg_get_expr(i.indpred, i.indrelid)
                from pg_index i join pg_class c on c.oid = i.indexrelid
                where c.relname = 'idx_lead_actors_tenant_telegram_user'
                """
            ).fetchone()
            assert index == ("(telegram_user_id IS NOT NULL)",), "schema drifted from production"

            # A partner linked by chat id only — the shape of every real partner row.
            conn.execute(
                """
                insert into lead_actors (actor_id, tenant_id, display_name, telegram_username, telegram_chat_id)
                values ('proof-inviter', 'whieda', 'Proof inviter', 'proof_inviter', '50001');

                insert into referral_invite_codes (tenant_id, invite_code, inviter_actor_id)
                values ('whieda', 'proofinvite_code1', 'proof-inviter');
                """
            )
            for grant in (
                sql.SQL("grant connect on database {} to {}").format(sql.Identifier(dbname), sql.Identifier(role)),
                sql.SQL("grant usage on schema public to {}").format(sql.Identifier(role)),
                sql.SQL("grant select, insert, update, delete on all tables in schema public to {}").format(sql.Identifier(role)),
                sql.SQL("grant usage, select on all sequences in schema public to {}").format(sql.Identifier(role)),
                sql.SQL("grant execute on all functions in schema public to {}").format(sql.Identifier(role)),
            ):
                conn.execute(grant)

        async def service_proof() -> None:
            os.environ["PLATFORM_DATABASE_URL"] = api_dsn
            from app.db import close_pool, fetch_one, init_pool, tenant_connection
            from app.referral_bonus.service import accept_referral_start, ensure_telegram_actor
            from app.settings import get_settings

            get_settings.cache_clear()
            await close_pool()
            await init_pool()
            try:
                raw = {"message": {"from": {"id": 60001, "first_name": "Inna"}}}

                # 1. First-touch: a person Telegram has never shown us before.
                first = await accept_referral_start(
                    "whieda", telegram_user_id=60001, telegram_chat_id=60001,
                    invite_code="proofinvite_code1", raw_update=raw,
                )
                assert first.status == "attributed"
                assert first.inviter_actor_id == "proof-inviter"

                async with tenant_connection("whieda") as conn:
                    actor = await fetch_one(
                        conn,
                        "select actor_id, telegram_chat_id, telegram_user_id, display_name "
                        "from lead_actors where telegram_user_id = %s",
                        (60001,),
                    )
                    attribution = await fetch_one(
                        conn,
                        "select inviter_actor_id, invite_code, source from partner_referral_attributions "
                        "where tenant_id = 'whieda' and invitee_actor_id = %s",
                        ("telegram:whieda:60001",),
                    )
                assert actor["actor_id"] == "telegram:whieda:60001"
                assert actor["telegram_chat_id"] == "60001"
                assert actor["display_name"] == "Inna"
                assert attribution["inviter_actor_id"] == "proof-inviter"
                assert attribution["source"] == "telegram_deeplink"

                # 2. Pressing the link again never rewrites the inviter.
                again = await accept_referral_start(
                    "whieda", telegram_user_id=60001, telegram_chat_id=60001,
                    invite_code="proofinvite_code1", raw_update=raw,
                )
                assert again.status == "already_registered"

                # 3. Two simultaneous starts from one new person: exactly one attribution.
                raced = await asyncio.gather(
                    accept_referral_start(
                        "whieda", telegram_user_id=60002, telegram_chat_id=60002,
                        invite_code="proofinvite_code1", raw_update=raw,
                    ),
                    accept_referral_start(
                        "whieda", telegram_user_id=60002, telegram_chat_id=60002,
                        invite_code="proofinvite_code1", raw_update=raw,
                    ),
                )
                assert sorted(result.status for result in raced) == ["already_registered", "attributed"]

                # 4. A partner known only by chat id keeps their own actor row.
                partner = await ensure_telegram_actor(
                    "whieda", telegram_user_id=50001, telegram_chat_id=50001, raw_update=raw,
                )
                assert partner == "proof-inviter"

                # 5. Any other newcomer gets one stable actor, no matter how often they write.
                created = await ensure_telegram_actor(
                    "whieda", telegram_user_id=60003, telegram_chat_id=60003, raw_update=raw,
                )
                repeated = await ensure_telegram_actor(
                    "whieda", telegram_user_id=60003, telegram_chat_id=60003, raw_update=raw,
                )
                assert created == repeated == "telegram:whieda:60003"

                # 6. The partner's own link from their own account is a no-op.
                own = await accept_referral_start(
                    "whieda", telegram_user_id=50001, telegram_chat_id=50001,
                    invite_code="proofinvite_code1", raw_update=raw,
                )
                assert own.status == "already_registered"

                # Only the three newcomers got telegram:* rows; the partner row is untouched
                # (matched by chat id, its telegram_user_id stays as it was) and the
                # inviter earned exactly two attributions.
                async with tenant_connection("whieda") as conn:
                    totals = await fetch_one(
                        conn,
                        "select count(*) filter (where actor_id like 'telegram:%%') as telegram_actors, "
                        "count(*) filter (where telegram_user_id is not null) as with_user, "
                        "(select telegram_user_id from lead_actors where actor_id = 'proof-inviter') as partner_user_id, "
                        "(select count(*) from partner_referral_attributions where tenant_id = 'whieda') as attributions "
                        "from lead_actors where tenant_id = 'whieda'",
                    )
                assert dict(totals) == {
                    "telegram_actors": 3, "with_user": 3, "partner_user_id": None, "attributions": 2,
                }
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
