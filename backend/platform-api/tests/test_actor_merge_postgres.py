"""A person who wrote to the bot before being registered as a partner.

The bot created ``telegram:<tenant>:<id>`` for them (with their invite code,
attribution and maybe bonuses). Later the owner registers them as a partner with
the same @username. The next message must fold the anonymous row into the
partner row instead of leaving two records for one human (Kira, 2026-09-14).
"""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database


@pytest.mark.integration
def test_anonymous_bot_actor_is_merged_into_partner_on_next_message():
    with temporary_database("whieda_actor_merge") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, MIGRATIONS)
            conn.execute(
                """
                -- inviter partner
                insert into lead_actors (actor_id, tenant_id, display_name, telegram_username, telegram_chat_id, telegram_user_id)
                values ('olesya', 'whieda', 'Олеся', 'olesya_tg', '50001', 50001);
                insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode)
                values ('olesya', 'whieda', 'olesya', 'named');
                -- the anonymous row the bot made when Kira pressed Olesya's link
                insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id, telegram_user_id)
                values ('telegram:whieda:6261', 'whieda', '@Kira_tg', '6261', 6261);
                insert into partner_referral_attributions (tenant_id, invitee_actor_id, inviter_actor_id, invite_code, source)
                values ('whieda', 'telegram:whieda:6261', 'olesya', null, 'telegram_deeplink');
                insert into referral_invite_codes (tenant_id, invite_code, inviter_actor_id)
                values ('whieda', 'kiracode12345', 'telegram:whieda:6261');
                insert into partner_bonus_ledger (tenant_id, actor_id, entry_type, amount_minor, currency, product_code, idempotency_key, description)
                values ('whieda', 'telegram:whieda:6261', 'credit', 300, 'WUSD', 'platform_subscription', 'seed-1', 'seed');
                -- the partner row the owner registered afterwards (registry sync: username only)
                insert into lead_actors (actor_id, tenant_id, display_name, telegram_username)
                values ('kira', 'whieda', 'Кира Гусельникова', 'Kira_tg');
                insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode)
                values ('kira', 'whieda', 'kira', 'named');
                """
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_one, tenant_connection
            from app.leads.actor_link import link_lead_actor_by_username, merge_anonymous_actor_into_partner

            async def value(sql: str, params: tuple = ()) -> dict | None:
                async with tenant_connection("whieda") as conn:
                    row = await fetch_one(conn, sql, params)
                return dict(row) if row else None

            # Plain link by username refuses: the chat is owned by another row.
            assert await link_lead_actor_by_username("whieda", username="Kira_tg", telegram_user_id=6261, telegram_chat_id=6261) is None

            merged = await merge_anonymous_actor_into_partner("whieda", username="Kira_tg", telegram_user_id=6261, telegram_chat_id=6261)
            assert merged == {"partner_actor_id": "kira", "anonymous_actor_id": "telegram:whieda:6261"}

            partner = await value("select telegram_chat_id, telegram_user_id, active from lead_actors where actor_id = 'kira'")
            assert partner == {"telegram_chat_id": "6261", "telegram_user_id": 6261, "active": True}
            anon = await value("select telegram_chat_id, telegram_user_id, active from lead_actors where actor_id = 'telegram:whieda:6261'")
            assert anon == {"telegram_chat_id": None, "telegram_user_id": None, "active": False}
            # Everything the person earned or was given follows them.
            assert (await value("select inviter_actor_id from partner_referral_attributions where invitee_actor_id = 'kira'"))["inviter_actor_id"] == "olesya"
            assert await value("select 1 as x from partner_referral_attributions where invitee_actor_id = 'telegram:whieda:6261'") is None
            assert (await value("select inviter_actor_id from referral_invite_codes where invite_code = 'kiracode12345'"))["inviter_actor_id"] == "kira"
            assert (await value("select sum(amount_minor)::int as s from partner_bonus_ledger where actor_id = 'kira'"))["s"] == 300

            # Second call is a no-op: nothing left to merge.
            assert await merge_anonymous_actor_into_partner("whieda", username="Kira_tg", telegram_user_id=6261, telegram_chat_id=6261) is None

            # A partner who already has an attribution keeps it; the anonymous one is dropped.
            async with tenant_connection("whieda") as conn:
                await fetch_one(conn, """
                    insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id, telegram_user_id)
                    values ('telegram:whieda:7777', 'whieda', '@zina_tg', '7777', 7777) returning actor_id""")
                await fetch_one(conn, """
                    insert into partner_referral_attributions (tenant_id, invitee_actor_id, inviter_actor_id, invite_code, source)
                    values ('whieda', 'telegram:whieda:7777', 'olesya', null, 'telegram_deeplink') returning invitee_actor_id""")
                await fetch_one(conn, """
                    insert into lead_actors (actor_id, tenant_id, display_name, telegram_username)
                    values ('zina', 'whieda', 'Зина', 'zina_tg') returning actor_id""")
                await fetch_one(conn, """
                    insert into partner_referral_attributions (tenant_id, invitee_actor_id, inviter_actor_id, invite_code, source)
                    values ('whieda', 'zina', 'kira', null, 'admin_manual') returning invitee_actor_id""")
            assert (await merge_anonymous_actor_into_partner("whieda", username="ZINA_TG", telegram_user_id=7777, telegram_chat_id=7777))["partner_actor_id"] == "zina"
            assert (await value("select inviter_actor_id from partner_referral_attributions where invitee_actor_id = 'zina'"))["inviter_actor_id"] == "kira"

        db.run_with_app(proof)
