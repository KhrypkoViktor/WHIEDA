"""Site order in the bot with a plan choice (v10): «сайт + настройка» or
«Платформа + Клуб». On the owner's confirmation both plans are recorded as one
received payment with product lines — setup and club included — the terms are
extended, and the referrer earns 20 % of the PRO line."""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import temporary_database


@pytest.mark.integration
def test_bundle_site_request_records_pro_and_club_and_pays_referrer():
    with temporary_database("whieda_site_plans") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(
                """
                insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) values
                  ('proof-inviter', 'whieda', 'Олеся', '5001'),
                  ('proof-new', 'whieda', 'Анастасия', '5002');
                insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode) values
                  ('olesya', 'whieda', 'proof-inviter', 'named');
                insert into partner_referral_attributions (tenant_id, invitee_actor_id, inviter_actor_id, invite_code, source) values
                  ('whieda', 'proof-new', 'proof-inviter', null, 'admin_manual');
                alter table lead_actors add column if not exists telegram_user_id bigint;
                update lead_actors set telegram_user_id = 5002 where actor_id = 'proof-new';
                """
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, tenant_connection
            from app.site_requests.service import (
                SiteRequestError,
                begin_site_request,
                confirm_site_request,
                set_site_request_country,
                set_site_request_intro,
                set_site_request_photo,
                set_site_request_plan,
                set_site_request_subdomain,
                submit_site_payment_proof,
            )

            async def rows(sql: str, params: tuple = ()) -> list[dict]:
                async with tenant_connection("whieda") as conn:
                    return [dict(r) for r in await fetch_all(conn, sql, params)]

            req = await begin_site_request("whieda", "proof-new")
            assert req["status"] == "awaiting_country"
            await set_site_request_country("whieda", "proof-new", "RU")
            await set_site_request_subdomain("whieda", "proof-new", "anastasy")
            await set_site_request_photo("whieda", "proof-new", "file-1")
            req = await set_site_request_intro("whieda", "proof-new", "Косметолог-эстетист, семь лет в сфере красоты и омоложения.")
            # After the text comes the plan, not the payment.
            assert req["status"] == "awaiting_plan" and req["total_amount_minor"] is None
            with pytest.raises(SiteRequestError):
                await set_site_request_plan("whieda", "proof-new", "gold")
            req = await set_site_request_plan("whieda", "proof-new", "bundle")
            assert req["status"] == "awaiting_payment" and req["plan_code"] == "bundle"
            assert req["currency"] == "RUB" and req["total_amount_minor"] == 1_050_000 and req["subscription_amount_minor"] == 300_000
            req = await submit_site_payment_proof("whieda", "proof-new", chat_id=5002, message_id=77, file_id="receipt")
            assert req["status"] == "pending_confirmation"

            done = await confirm_site_request("whieda", request_id=str(req["request_id"]), admin_telegram_user_id=1)
            assert done["status"] == "pending_provisioning" and done["idempotent"] is False
            assert done["payment"]["club_paid_until"] is not None
            ledger = await rows("select product_code, amount_minor, access_months, promo_note from partner_payment_ledger where ref_code = 'anastasy' order by product_code")
            assert [(r["product_code"], r["amount_minor"], r["access_months"]) for r in ledger] == [("club_subscription", 750_000, 3), ("platform_subscription", 300_000, 3)]
            assert ledger[0]["promo_note"]
            header = await rows("select received_amount_minor, currency from partner_payments where ref_code = 'anastasy'")
            assert header == [{"received_amount_minor": 1_050_000, "currency": "RUB"}]
            access = await rows("select paid_until from partner_product_access where ref_code = 'anastasy' and product_code = 'club_subscription'")
            assert access and access[0]["paid_until"] is not None
            sub = await rows("select paid_until from partner_subscriptions where ref_code = 'anastasy'")
            assert sub and sub[0]["paid_until"] is not None
            bonus = await rows("select amount_minor, product_code from partner_bonus_ledger where actor_id = 'proof-inviter'")
            assert [(b["amount_minor"], b["product_code"]) for b in bonus] == [(600, "platform_subscription")]
            # Confirming again changes nothing.
            again = await confirm_site_request("whieda", request_id=str(req["request_id"]), admin_telegram_user_id=1)
            assert again["idempotent"] is True

            # The plain plan records PRO + site setup (setup used to be lost).
            async with tenant_connection("whieda") as conn:
                await conn.execute(
                    "insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id, telegram_user_id) values ('proof-two', 'whieda', 'Кира', '5003', 5003)"
                )
            await begin_site_request("whieda", "proof-two")
            await set_site_request_country("whieda", "proof-two", "BY")
            await set_site_request_subdomain("whieda", "proof-two", "kira2")
            await set_site_request_photo("whieda", "proof-two", "file-2")
            await set_site_request_intro("whieda", "proof-two", "Помогаю людям спокойно разбираться в продуктах и привычках.")
            req2 = await set_site_request_plan("whieda", "proof-two", "site")
            assert req2["currency"] == "WUSD" and req2["total_amount_minor"] == 5_000
            req2 = await submit_site_payment_proof("whieda", "proof-two", chat_id=5003, message_id=78, file_id="receipt-2")
            await confirm_site_request("whieda", request_id=str(req2["request_id"]), admin_telegram_user_id=1)
            ledger2 = await rows("select product_code, amount_minor, access_months from partner_payment_ledger where ref_code = 'kira2' order by product_code")
            assert [(r["product_code"], r["amount_minor"], r["access_months"]) for r in ledger2] == [("platform_subscription", 3_000, 3), ("site_setup", 2_000, 0)]

        db.run_with_app(proof)
