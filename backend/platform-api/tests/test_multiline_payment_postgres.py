"""One received transfer, several product lines — on the real schema (v7).

Zinaida's bundle (PRO 30 + CLUB 75 promo) and Kira's PRO + site setup are the
shapes this must record; the referrer earns 20 % of the PRO line only.
"""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import temporary_database


@pytest.mark.integration
def test_bundle_payment_records_lines_extends_club_and_pays_bonus_from_pro_only():
    with temporary_database("whieda_multiline") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(
                """
                insert into lead_actors (actor_id, tenant_id, display_name) values
                  ('proof-olesya', 'whieda', 'Олеся'), ('proof-zina', 'whieda', 'Зина'), ('proof-kira', 'whieda', 'Кира');
                insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode) values
                  ('olesya', 'whieda', 'proof-olesya', 'named'), ('zina', 'whieda', 'proof-zina', 'named'), ('kira', 'whieda', 'proof-kira', 'named');
                insert into partner_referral_attributions (tenant_id, invitee_actor_id, inviter_actor_id, invite_code, source) values
                  ('whieda', 'proof-zina', 'proof-olesya', null, 'admin_manual'), ('whieda', 'proof-kira', 'proof-olesya', null, 'admin_manual');
                """
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, fetch_one, tenant_connection
            from app.subscriptions.pricing import PaymentLine, effective_price_minor, parse_payment_command
            from app.subscriptions.service import PaymentIdempotencyConflictError, record_payment_lines

            async def rows(sql: str, params: tuple = ()) -> list[dict]:
                async with tenant_connection("whieda") as conn:
                    return [dict(r) for r in await fetch_all(conn, sql, params)]

            # Prices: list, and a personal one.
            async with tenant_connection("whieda") as conn:
                assert await effective_price_minor(conn, tenant_id="whieda", ref_code="zina", product_code="club_subscription", access_months=3, currency="WUSD") == (12000, None)
                assert await effective_price_minor(conn, tenant_id="whieda", ref_code="zina", product_code="site_setup", access_months=0, currency="RUB") == (200000, None)
                await fetch_one(conn, """
                    insert into partner_price_overrides (tenant_id, ref_code, product_code, price_wusd_minor, reason, approved_by_telegram_user_id)
                    values ('whieda', 'olesya', 'platform_subscription', 1500, 'скидка 50%%', 1) returning override_id""")
                assert await effective_price_minor(conn, tenant_id="whieda", ref_code="olesya", product_code="platform_subscription", access_months=3, currency="RUB") == (150000, "скидка 50%")

            # Zina: bundle 105 WWC$ in one transfer.
            parsed = parse_payment_command("оплата ref:zina\nпакет 105 WWC$\nполучено 105 WWC$")
            result = await record_payment_lines(
                "whieda", ref_code="zina", lines=parsed.lines, received_minor=10500, currency="WUSD",
                telegram_chat_id=1, telegram_message_id=101, telegram_user_id=1,
            )
            assert result["idempotent"] is False
            assert [l["product_code"] for l in result["lines"]] == ["platform_subscription", "club_subscription"]
            assert result["pro_paid_until"] is not None and result["club_paid_until"] is not None
            ledger = await rows("select product_code, amount_minor, access_months, promo_note, received_payment_id from partner_payment_ledger where ref_code = 'zina' order by product_code")
            assert [(r["product_code"], r["amount_minor"], r["access_months"]) for r in ledger] == [("club_subscription", 7500, 3), ("platform_subscription", 3000, 3)]
            assert len({r["received_payment_id"] for r in ledger}) == 1 and ledger[0]["promo_note"]
            access = await rows("select product_code, paid_until from partner_product_access where ref_code = 'zina'")
            assert access[0]["product_code"] == "club_subscription" and access[0]["paid_until"] == result["club_paid_until"]
            bonus = await rows("select amount_minor, product_code from partner_bonus_ledger where actor_id = 'proof-olesya'")
            assert [(b["amount_minor"], b["product_code"]) for b in bonus] == [(600, "platform_subscription")]

            # Same message again: nothing doubles.
            again = await record_payment_lines(
                "whieda", ref_code="zina", lines=parsed.lines, received_minor=10500, currency="WUSD",
                telegram_chat_id=1, telegram_message_id=101, telegram_user_id=1,
            )
            assert again["idempotent"] is True
            assert len(await rows("select 1 as x from partner_payment_ledger where ref_code = 'zina'")) == 2
            assert len(await rows("select 1 as x from partner_bonus_ledger where actor_id = 'proof-olesya'")) == 1
            # Same message, different content: refused.
            with pytest.raises(PaymentIdempotencyConflictError):
                await record_payment_lines(
                    "whieda", ref_code="zina", lines=parsed.lines[:1], received_minor=3000, currency="WUSD",
                    telegram_chat_id=1, telegram_message_id=101, telegram_user_id=1,
                )

            # Kira: PRO + site setup in roubles; setup is one-off (no term, no bonus).
            kira = parse_payment_command("оплата ref:kira\nPRO 3000 RUB 3\nнастройка 2000 RUB\nполучено 5000 RUB")
            k = await record_payment_lines(
                "whieda", ref_code="kira", lines=kira.lines, received_minor=500000, currency="RUB",
                telegram_chat_id=1, telegram_message_id=102, telegram_user_id=1,
            )
            assert k["club_paid_until"] is None and k["pro_paid_until"] is not None
            setup = await rows("select access_months, period_start, period_end from partner_payment_ledger where ref_code = 'kira' and product_code = 'site_setup'")
            assert setup[0]["access_months"] == 0 and setup[0]["period_start"] == setup[0]["period_end"]
            bonus = await rows("select amount_minor from partner_bonus_ledger where actor_id = 'proof-olesya' order by created_at")
            # Bonus base is the PRO list price in WWC$ (30) whatever the currency paid: 6 WWC$ each.
            assert [b["amount_minor"] for b in bonus] == [600, 600]

        db.run_with_app(proof)
