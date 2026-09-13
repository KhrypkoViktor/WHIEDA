"""Telegram actor registration against the real lead_actors schema.

Production keeps ``(tenant_id, telegram_user_id)`` unique through a *partial*
index (``where telegram_user_id is not null``).  A plain ``on conflict`` target
does not match that index and PostgreSQL rejects the insert — which is exactly
how every new bot user failed on 2026-09-12.  Static tests cannot catch this;
only a real database can.
"""

from __future__ import annotations

import asyncio

import psycopg
import pytest

from tests.postgres_testkit import temporary_database


@pytest.mark.integration
def test_new_telegram_users_register_and_attribute_on_live_schema():
    with temporary_database("whieda_referral_start") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
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
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_one, tenant_connection
            from app.leads.actor_link import fill_lead_actor_user_id
            from app.referral_bonus.service import (
                TelegramIdentityConflictError,
                accept_referral_start,
                ensure_telegram_actor,
            )

            raw = {"message": {"from": {"id": 60001, "first_name": "Inna"}}}

            async def value(query: str, params: tuple = ()) -> object:
                async with tenant_connection("whieda") as conn:
                    row = await fetch_one(conn, query, params)
                return dict(row) if row else None

            # 1. First-touch: a person Telegram has never shown us before.
            first = await accept_referral_start(
                "whieda", telegram_user_id=60001, telegram_chat_id=60001,
                invite_code="proofinvite_code1", raw_update=raw,
            )
            assert first.status == "attributed"
            assert first.inviter_actor_id == "proof-inviter"
            actor = await value(
                "select actor_id, telegram_chat_id, display_name from lead_actors where telegram_user_id = %s",
                (60001,),
            )
            assert actor == {"actor_id": "telegram:whieda:60001", "telegram_chat_id": "60001", "display_name": "Inna"}
            attribution = await value(
                "select inviter_actor_id, source from partner_referral_attributions "
                "where tenant_id = 'whieda' and invitee_actor_id = 'telegram:whieda:60001'"
            )
            assert attribution == {"inviter_actor_id": "proof-inviter", "source": "telegram_deeplink"}

            # 2. Pressing the link again never rewrites the inviter.
            again = await accept_referral_start(
                "whieda", telegram_user_id=60001, telegram_chat_id=60001,
                invite_code="proofinvite_code1", raw_update=raw,
            )
            assert again.status == "already_registered"

            # 3. Two simultaneous starts from one new person: exactly one attribution.
            raced = await asyncio.gather(
                *(
                    accept_referral_start(
                        "whieda", telegram_user_id=60002, telegram_chat_id=60002,
                        invite_code="proofinvite_code1", raw_update=raw,
                    )
                    for _ in range(2)
                )
            )
            assert sorted(result.status for result in raced) == ["already_registered", "attributed"]

            # 4. A partner known only by chat id keeps their own actor row, and the
            #    /start that just revealed their Telegram user id fills it in.
            partner = await ensure_telegram_actor(
                "whieda", telegram_user_id=50001, telegram_chat_id=50001, raw_update=raw,
            )
            assert partner == "proof-inviter"
            assert await value("select telegram_user_id from lead_actors where actor_id = 'proof-inviter'") == {
                "telegram_user_id": 50001
            }

            # 4b. A Telegram user id that already belongs to someone else is never
            #     merged into a chat-matched row: loud failure, not a silent link.
            await value(
                "insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) "
                "values ('proof-other', 'whieda', 'Proof other', '50002') returning actor_id"
            )
            with pytest.raises(TelegramIdentityConflictError):
                await ensure_telegram_actor(
                    "whieda", telegram_user_id=60001, telegram_chat_id=50002, raw_update=raw,
                )
            assert await value("select telegram_user_id from lead_actors where actor_id = 'proof-other'") == {
                "telegram_user_id": None
            }

            # 4c. Any private message completes a chat-only partner row the same way,
            #     and refuses (returns None, row untouched) when the id is taken.
            await value(
                "insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) "
                "values ('proof-chat-only', 'whieda', 'Proof chat only', '50003') returning actor_id"
            )
            assert await fill_lead_actor_user_id("whieda", telegram_user_id=50003, telegram_chat_id=50003) == "proof-chat-only"
            assert await fill_lead_actor_user_id("whieda", telegram_user_id=50003, telegram_chat_id=50003) is None
            assert await fill_lead_actor_user_id("whieda", telegram_user_id=60001, telegram_chat_id=50002) is None
            assert await value(
                "select (select telegram_user_id from lead_actors where actor_id = 'proof-chat-only') as chat_only, "
                "(select telegram_user_id from lead_actors where actor_id = 'proof-other') as other"
            ) == {"chat_only": 50003, "other": None}

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

            # Three newcomers got telegram:* rows; two partner rows were completed in
            # place; 'proof-other' stays without a user id; two attributions total.
            assert await value(
                "select count(*) filter (where actor_id like 'telegram:%%') as telegram_actors, "
                "count(*) filter (where telegram_user_id is not null) as with_user, "
                "(select count(*) from partner_referral_attributions where tenant_id = 'whieda') as attributions "
                "from lead_actors where tenant_id = 'whieda'"
            ) == {"telegram_actors": 3, "with_user": 5, "attributions": 2}

        db.run_with_app(proof)
