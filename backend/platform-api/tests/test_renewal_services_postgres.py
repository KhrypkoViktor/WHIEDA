"""Продление в боте — все услуги, а не только сайт (V13, 24.09.2026).

Красочко оплатила 105 WUSD пакетом «сайт + клуб», а диалог продления знал
только «платформу на 3/6/12 месяцев» и записал 30 WUSD за сайт. Теперь:
  * пакет раскладывается на сайт и клуб, оба срока продлеваются;
  * курс — разовая покупка, добавляется одной строкой в тарифы, даёт доступ
    в Академии (course_slug тарифа → academy_access, полка V15 25.09.2026);
  * клуб отдельно не предлагается, только пакетом с сайтом.
"""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import temporary_database


@pytest.mark.integration
def test_bundle_and_course_renewals_record_every_line():
    with temporary_database("whieda_renewal_services") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(
                """
                insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) values
                  ('proof-partner', 'whieda', 'Валентина', '6001');
                alter table lead_actors add column if not exists telegram_user_id bigint;
                update lead_actors set telegram_user_id = 6001 where actor_id = 'proof-partner';
                insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, enabled) values
                  ('petrovna', 'whieda', 'proof-partner', 'named', true);
                insert into partner_subscriptions (tenant_id, ref_code, paid_until) values
                  ('whieda', 'petrovna', now() - interval '1 day');
                -- Новый курс — одна строка в тарифах, без правки кода.
                insert into partner_subscription_plans
                  (tenant_id, plan_code, product_code, access_months, price_wusd_minor, price_rub_minor, active, valid_from, title, sort_order, course_slug)
                values
                  ('whieda', 'course_neuro', 'course_neuro', 0, 15000, 1500000, true, now() - interval '1 day', 'Курс «Нейросети для запуска»', 110, 'neuro');
                insert into academy_courses (tenant_id, slug, title, access_rule, status)
                values ('whieda', 'neuro', 'Нейросети для запуска', 'purchase', 'published');
                """
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, tenant_connection
            from app.renewal_requests.service import (
                RenewalRequestError,
                begin_renewal_request,
                confirm_renewal_request,
                list_renewal_offers,
                set_renewal_country,
                set_renewal_plan,
                submit_renewal_payment_proof,
            )

            async def rows(sql: str, params: tuple = ()) -> list[dict]:
                async with tenant_connection("whieda") as conn:
                    return [dict(r) for r in await fetch_all(conn, sql, params)]

            offers = [o["plan_code"] for o in await list_renewal_offers("whieda")]
            assert offers[:4] == ["platform_3m", "platform_6m", "platform_12m", "bundle_pro_club_3m"]
            assert "course_neuro" in offers
            # Клуба отдельно и подключения сайта в продлении нет.
            assert "club_3m" not in offers and "site_setup" not in offers

            # Пакет «сайт + клуб» за рубли.
            req = await begin_renewal_request("whieda", "proof-partner")
            with pytest.raises(RenewalRequestError):
                await set_renewal_plan("whieda", "proof-partner", "club_3m")
            req = await set_renewal_plan("whieda", "proof-partner", "bundle_pro_club_3m")
            assert req["status"] == "awaiting_country" and req["access_months"] == 3
            req = await set_renewal_country("whieda", "proof-partner", "RU")
            assert req["currency"] == "RUB" and req["amount_minor"] == 1_050_000
            req = await submit_renewal_payment_proof("whieda", "proof-partner", chat_id=6001, message_id=1, file_id="r1")
            done = await confirm_renewal_request("whieda", request_id=str(req["request_id"]), admin_telegram_user_id=1)
            assert done["status"] == "confirmed"

            ledger = await rows(
                "select product_code, amount_minor, access_months from partner_payment_ledger"
                " where ref_code = 'petrovna' order by product_code"
            )
            assert [(r["product_code"], r["amount_minor"], r["access_months"]) for r in ledger] == [
                ("club_subscription", 750_000, 3),
                ("platform_subscription", 300_000, 3),
            ]
            received = await rows("select received_amount_minor, currency from partner_payments where ref_code = 'petrovna'")
            assert received == [{"received_amount_minor": 1_050_000, "currency": "RUB"}]
            club = await rows(
                "select paid_until > now() + interval '80 days' as ok from partner_product_access"
                " where ref_code = 'petrovna' and product_code = 'club_subscription'"
            )
            assert club == [{"ok": True}]

            # Курс за W$: разовая строка без срока и доступ в Академии.
            req = await begin_renewal_request("whieda", "proof-partner")
            req = await set_renewal_plan("whieda", "proof-partner", "course_neuro")
            assert req["access_months"] == 0
            req = await set_renewal_country("whieda", "proof-partner", "BY")
            assert req["currency"] == "WUSD" and req["amount_minor"] == 15_000
            req = await submit_renewal_payment_proof("whieda", "proof-partner", chat_id=6001, message_id=2, file_id="r2")
            await confirm_renewal_request("whieda", request_id=str(req["request_id"]), admin_telegram_user_id=1)
            course = await rows(
                "select a.source, a.telegram_user_id, a.revoked_at from academy_access a"
                " join academy_courses c on c.tenant_id = a.tenant_id and c.course_id = a.course_id"
                " where c.slug = 'neuro'"
            )
            assert course == [{"source": "purchase", "telegram_user_id": 6001, "revoked_at": None}]
            assert await rows(
                "select 1 from partner_product_access where ref_code = 'petrovna' and product_code = 'course_neuro'"
            ) == []

        db.run_with_app(proof)
