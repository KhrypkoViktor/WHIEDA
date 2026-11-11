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
                  ('proof-new', 'whieda', 'Анастасия', '5002'),
                  ('proof-club', 'whieda', 'Клуб', '5004'),
                  ('proof-course', 'whieda', 'Курс', '5005');
                insert into academy_courses (tenant_id, slug, title, access_rule, status)
                  values ('whieda', 'online-start-4w', 'Онлайн-старт: 4 недели практики', 'purchase', 'published');
                insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode) values
                  ('olesya', 'whieda', 'proof-inviter', 'named');
                insert into partner_referral_attributions (tenant_id, invitee_actor_id, inviter_actor_id, invite_code, source) values
                  ('whieda', 'proof-new', 'proof-inviter', null, 'admin_manual');
                alter table lead_actors add column if not exists telegram_user_id bigint;
                update lead_actors set telegram_user_id = 5002 where actor_id = 'proof-new';
                update lead_actors set telegram_user_id = 5004 where actor_id = 'proof-club';
                update lead_actors set telegram_user_id = 5005 where actor_id = 'proof-course';
                """
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, tenant_connection
            from app.site_requests.service import (
                SiteRequestError,
                begin_site_request,
                confirm_site_request,
                set_site_request_contacts,
                set_site_request_intro,
                set_site_request_name,
                set_site_request_lang,
                set_site_request_photo,
                set_site_request_plan,
                set_site_request_subdomain,
                submit_site_payment_proof,
            )

            async def rows(sql: str, params: tuple = ()) -> list[dict]:
                async with tenant_connection("whieda") as conn:
                    return [dict(r) for r in await fetch_all(conn, sql, params)]

            req = await begin_site_request("whieda", "proof-new")
            # Страну больше не спрашиваем — оплата только в рублях (09.10.2026); первым — имя (V23).
            assert req["status"] == "awaiting_name" and req["country_code"] == "RU"
            await set_site_request_name("whieda", "proof-new", "Анастасия Сошникова")
            await set_site_request_subdomain("whieda", "proof-new", "anastasy")
            await set_site_request_lang("whieda", "proof-new", "ru")
            await set_site_request_photo("whieda", "proof-new", "file-1")
            req = await set_site_request_intro("whieda", "proof-new", "Косметолог-эстетист, семь лет в сфере красоты и омоложения.")
            # V12: после текста — контакты одним сообщением, потом пакет.
            assert req["status"] == "awaiting_contacts"
            req = await set_site_request_contacts("whieda", "proof-new", "Телефон и WhatsApp: 8 928 672 92 88")
            assert req["contacts"]["whatsapp"] == ["+79286729288"]
            # After the text comes the plan, not the payment.
            assert req["status"] == "awaiting_plan" and req["total_amount_minor"] is None
            with pytest.raises(SiteRequestError):
                await set_site_request_plan("whieda", "proof-new", "gold")
            req = await set_site_request_plan("whieda", "proof-new", "bundle")
            assert req["status"] == "awaiting_payment" and req["plan_code"] == "bundle"
            assert req["currency"] == "RUB" and req["total_amount_minor"] == 1_100_000 and req["subscription_amount_minor"] == 300_000
            req = await submit_site_payment_proof("whieda", "proof-new", chat_id=5002, message_id=77, file_id="receipt")
            assert req["status"] == "pending_confirmation"

            done = await confirm_site_request("whieda", request_id=str(req["request_id"]), admin_telegram_user_id=1)
            assert done["status"] == "pending_provisioning" and done["idempotent"] is False
            assert done["payment"]["club_paid_until"] is not None
            ledger = await rows("select product_code, amount_minor, access_months, promo_note from partner_payment_ledger where ref_code = 'anastasy' order by product_code")
            assert [(r["product_code"], r["amount_minor"], r["access_months"]) for r in ledger] == [
                ("club_subscription", 800_000, 2), ("platform_subscription", 300_000, 3),
            ]
            assert ledger[0]["promo_note"]
            header = await rows("select received_amount_minor, currency from partner_payments where ref_code = 'anastasy'")
            assert header == [{"received_amount_minor": 1_100_000, "currency": "RUB"}]
            access = await rows("select paid_until from partner_product_access where ref_code = 'anastasy' and product_code = 'club_subscription'")
            assert access and access[0]["paid_until"] is not None
            sub = await rows("select paid_until from partner_subscriptions where ref_code = 'anastasy'")
            assert sub and sub[0]["paid_until"] is not None
            # Клуб на месяц короче сайта: 2 месяца против 3 (акция ноября, V48).
            assert sub[0]["paid_until"] > access[0]["paid_until"]
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
            await set_site_request_name("whieda", "proof-two", "Ольга Новикова")
            await set_site_request_subdomain("whieda", "proof-two", "kira2")
            await set_site_request_lang("whieda", "proof-two", "de")
            await set_site_request_photo("whieda", "proof-two", "file-2")
            await set_site_request_intro("whieda", "proof-two", "Помогаю людям спокойно разбираться в продуктах и привычках.")
            # «нет» — тоже ответ на шаг контактов (V12).
            req2 = await set_site_request_contacts("whieda", "proof-two", "нет")
            assert req2["status"] == "awaiting_plan" and req2["contacts"] == {}
            req2 = await set_site_request_plan("whieda", "proof-two", "site")
            assert req2["currency"] == "RUB" and req2["total_amount_minor"] == 600_000
            req2 = await submit_site_payment_proof("whieda", "proof-two", chat_id=5003, message_id=78, file_id="receipt-2")
            await confirm_site_request("whieda", request_id=str(req2["request_id"]), admin_telegram_user_id=1)
            ledger2 = await rows("select product_code, amount_minor, access_months from partner_payment_ledger where ref_code = 'kira2' order by product_code")
            assert [(r["product_code"], r["amount_minor"], r["access_months"]) for r in ledger2] == [("platform_subscription", 300_000, 3), ("site_setup", 300_000, 0)]

            # Прайс 11.11.2026: «сайт + клуб» на 6 месяцев и «сайт + курс» на 3 (в БД — коды site/bundle, вариант по сумме).
            async def order(actor: str, sub: str, plan: str, chat: int, msg: int) -> dict:
                await begin_site_request("whieda", actor)
                await set_site_request_name("whieda", actor, "Иван Петров")
                await set_site_request_subdomain("whieda", actor, sub)
                await set_site_request_lang("whieda", actor, "ru")
                await set_site_request_photo("whieda", actor, "f")
                await set_site_request_intro("whieda", actor, "Помогаю людям спокойно разбираться в продуктах и привычках.")
                await set_site_request_contacts("whieda", actor, "нет")
                paid = await set_site_request_plan("whieda", actor, plan)
                paid = await submit_site_payment_proof("whieda", actor, chat_id=chat, message_id=msg, file_id="r")
                return await confirm_site_request("whieda", request_id=str(paid["request_id"]), admin_telegram_user_id=1)

            club = await order("proof-club", "klub6", "club_6", 5004, 79)
            assert club["plan"] == "club_6" and club["course_access_granted"] is False
            led = await rows("select product_code, amount_minor, access_months from partner_payment_ledger where ref_code = 'klub6' order by product_code")
            assert [(r["product_code"], r["amount_minor"], r["access_months"]) for r in led] == [
                ("club_subscription", 2_400_000, 6), ("platform_subscription", 540_000, 6),
            ]
            assert (await rows("select received_amount_minor from partner_payments where ref_code = 'klub6'"))[0]["received_amount_minor"] == 2_940_000

            course = await order("proof-course", "kurs3", "course_3", 5005, 80)
            assert course["plan"] == "course_3" and course["course_access_granted"] is True
            led = await rows("select product_code, amount_minor, access_months from partner_payment_ledger where ref_code = 'kurs3' order by product_code")
            assert [(r["product_code"], r["amount_minor"], r["access_months"]) for r in led] == [
                ("course_academy", 1_000_000, 0), ("platform_subscription", 300_000, 3),
            ]
            acc = await rows("select telegram_user_id, source from academy_access where telegram_user_id = 5005")
            assert acc == [{"telegram_user_id": 5005, "source": "purchase"}]

        db.run_with_app(proof)
