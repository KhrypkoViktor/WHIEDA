from __future__ import annotations

import asyncio
import os

import psycopg
import pytest

from tests.postgres_testkit import temporary_database


@pytest.mark.integration
def test_partner_subscription_postgres_rls_idempotency_and_concurrency():
    with temporary_database("whieda_partner_subscriptions") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, twice=True)

            conn.execute(
                """
                insert into lead_actors (actor_id, tenant_id, display_name)
                values
                  ('proof-whieda-owner', 'whieda', 'WHIEDA proof owner'),
                  ('proof-whieda-inviter', 'whieda', 'WHIEDA proof inviter'),
                  ('proof-acme-owner', 'test-acme', 'Acme proof owner')
                on conflict (actor_id) do nothing;

                insert into referral_invite_codes (tenant_id, invite_code, inviter_actor_id)
                values ('whieda', 'proofinvite123', 'proof-whieda-inviter');

                insert into partner_referral_attributions (
                  tenant_id, invitee_actor_id, inviter_actor_id, invite_code, source
                ) values (
                  'whieda', 'proof-whieda-owner', 'proof-whieda-inviter',
                  'proofinvite123', 'telegram_deeplink'
                );

                insert into referral_profiles (
                  ref_code, tenant_id, owner_id, display_mode, public_profile, enabled
                ) values
                  ('proof-whieda', 'whieda', 'proof-whieda-owner', 'named', '{}', true),
                  ('proof-acme', 'test-acme', 'proof-acme-owner', 'named', '{}', true)
                on conflict (ref_code) do nothing;

                insert into partner_subscriptions (tenant_id, ref_code, paid_until)
                values ('test-acme', 'proof-acme', now() + interval '3 months');

                insert into partner_payment_intents (
                  tenant_id, ref_code, amount_minor, currency,
                  telegram_chat_id, telegram_message_id, telegram_user_id, expires_at
                ) values (
                  'test-acme', 'proof-acme', 10000, 'RUB',
                  40001, 40002, 40003, now() + interval '10 minutes'
                );
                """
            )
            whieda_plan = conn.execute(
                """
                select access_months, price_wusd_minor, price_rub_minor
                from partner_subscription_plans
                where tenant_id = 'whieda' and plan_code = 'platform_6m'
                """
            ).fetchone()
            assert whieda_plan == (6, 5400, 540000)
            rule = conn.execute(
                """
                select first_payment_bps, renewal_payment_bps, reward_currency
                from referral_reward_rules
                where tenant_id = 'whieda'
                  and product_code = 'platform_subscription'
                  and active = true
                  and valid_until is null
                """
            ).fetchone()
            assert rule == (2000, 1000, "WUSD")
            db.grant_api_role(conn)

            states = conn.execute(
                """
                select
                  partner_subscription_state('2026-09-22 00:00:00+03', '2026-09-21 23:59:59.999999+03'),
                  partner_subscription_state('2026-09-22 00:00:00+03', '2026-09-22 00:00:00+03'),
                  partner_subscription_state('2026-09-22 00:00:00+03', '2026-09-25 00:00:00+03')
                """
            ).fetchone()
            assert states == ("active", "grace", "suspended")

        with psycopg.connect(db.api_dsn) as conn:
            conn.execute("select platform_set_tenant_context('whieda')")
            foreign_count = conn.execute(
                "select count(*) from partner_subscriptions where tenant_id = 'test-acme'"
            ).fetchone()[0]
            assert foreign_count == 0
            foreign_intents = conn.execute(
                "select count(*) from partner_payment_intents where tenant_id = 'test-acme'"
            ).fetchone()[0]
            assert foreign_intents == 0
            foreign_plans = conn.execute(
                "select count(*) from partner_subscription_plans where tenant_id = 'test-acme'"
            ).fetchone()[0]
            assert foreign_plans == 0

        with psycopg.connect(db.api_dsn) as conn:
            conn.execute("select platform_set_tenant_context('whieda')")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "insert into partner_subscriptions (tenant_id, ref_code, paid_until) "
                    "values ('test-acme', 'proof-acme', now() + interval '3 months')"
                )

        with psycopg.connect(db.api_dsn) as conn:
            conn.execute("select platform_set_tenant_context('whieda')")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "insert into partner_payment_intents ("
                    "tenant_id, ref_code, amount_minor, currency, telegram_chat_id, "
                    "telegram_message_id, telegram_user_id, expires_at) values ("
                    "'test-acme', 'proof-acme', 10000, 'RUB', 1, 2, 3, now() + interval '10 minutes')"
                )

        async def service_proof() -> None:
            from app.db import close_pool, init_pool, tenant_connection
            from app.settings import get_settings
            from app.subscriptions.service import (
                PaymentIntentCancelledError,
                PaymentIntentForbiddenError,
                cancel_payment_intent,
                confirm_payment_intent,
                create_payment_intent,
                get_subscription,
                record_manual_payment,
            )

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
                    await cur.execute(
                        """
                        select amount_minor
                        from partner_bonus_ledger
                        where tenant_id = %s and actor_id = %s
                        order by created_at, entry_id
                        """,
                        ("whieda", "proof-whieda-inviter"),
                    )
                    assert [row["amount_minor"] for row in await cur.fetchall()] == [600, 300, 300]

            await close_pool()
            os.environ["DATABASE_POOL_MIN"] = "1"
            os.environ["DATABASE_POOL_MAX"] = "1"
            get_settings.cache_clear()
            await init_pool()

            intent = await create_payment_intent(
                "whieda",
                identifier="ref:proof-whieda",
                amount_minor=3000,
                currency="WUSD",
                telegram_chat_id=81001,
                telegram_message_id=92001,
                telegram_user_id=71001,
            )
            confirmed = await confirm_payment_intent(
                "whieda",
                intent_id=str(intent["intent_id"]),
                telegram_chat_id=81001,
                telegram_user_id=71001,
            )
            repeated = await confirm_payment_intent(
                "whieda",
                intent_id=str(intent["intent_id"]),
                telegram_chat_id=81001,
                telegram_user_id=71001,
            )
            assert confirmed["payment_id"] == repeated["payment_id"]
            assert repeated["idempotent"] is True

            guarded = await create_payment_intent(
                "whieda",
                identifier="ref:proof-whieda",
                amount_minor=300000,
                currency="RUB",
                telegram_chat_id=81001,
                telegram_message_id=92002,
                telegram_user_id=71001,
            )
            with pytest.raises(PaymentIntentForbiddenError):
                await confirm_payment_intent(
                    "whieda",
                    intent_id=str(guarded["intent_id"]),
                    telegram_chat_id=99999,
                    telegram_user_id=71001,
                )
            assert (
                await cancel_payment_intent(
                    "whieda",
                    intent_id=str(guarded["intent_id"]),
                    telegram_chat_id=81001,
                    telegram_user_id=71001,
                )
                == "cancelled"
            )
            with pytest.raises(PaymentIntentCancelledError):
                await confirm_payment_intent(
                    "whieda",
                    intent_id=str(guarded["intent_id"]),
                    telegram_chat_id=81001,
                    telegram_user_id=71001,
                )

        db.run_with_app(service_proof)
