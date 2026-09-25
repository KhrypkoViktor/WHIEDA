"""Academy «полка» on real PostgreSQL (V15, 25.09.2026).

The author pays for the shelf, students get access by the author's key, and a
paid ``course_<код>`` line opens the course (``partner_subscription_plans.course_slug``):
  * the author's course lands as a draft — students do not see it; ``publish`` shows it locked;
  * the shelf is paid through the renewal flow → ``academy_shelf.paid_until`` (+3 months),
    the partner's site term is untouched;
  * three keys; one opens the course; the same person again — «уже открыт», no use spent;
    a second person on the same one-time key — refused;
  * the shelf belongs to the person: paid on a chat-only legacy row of the author
    (telegram_user_id is unique per tenant), it still counts; a batch asked by the
    same message is issued once;
  * a lapsed shelf — no new keys and no redeeming (nothing spent), the student keeps learning;
  * a paid course line with ``course_slug`` → ``academy_access``; without it — the payment stays.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import psycopg
import pytest

from tests.postgres_testkit import temporary_database

LOADER = Path(__file__).resolve().parents[1] / "scripts" / "academy" / "load_bundle.py"

BUNDLE = {
    "course": {"slug": "kurs-igorya", "title": "Курс Игоря", "subtitle": "Проба полки", "access_rule": "pro"},
    "lessons": [
        {"slug": "vvedenie", "position": 1, "title": "Введение", "body_html": "<p>1</p>"},
        {"slug": "praktika", "position": 2, "title": "Практика", "body_html": "<p>2</p>"},
    ],
}
STUDENT, SECOND_STUDENT = 9001, 9002


def _loader():
    spec = importlib.util.spec_from_file_location("academy_load_bundle", LOADER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.integration
def test_academy_shelf_keys_and_payments(monkeypatch):
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", "1")
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", "1")
    loader = _loader()
    with temporary_database("whieda_academy_shelf") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, twice=True)
            conn.execute(
                """
                insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id, telegram_username) values
                  ('igor', 'whieda', 'Игорь', null, 'igor_wwc'),
                  ('proof-partner', 'whieda', 'Валентина', '6001', null),
                  ('no-telegram', 'whieda', 'Без телеграма', null, null),
                  -- Старая строка того же человека (Игорь): только chat id, без user id —
                  -- telegram_user_id уникален в тенанте. Её профиль igor2ref.
                  ('igor2', 'whieda', 'Игорь (старая строка)', '7001', null),
                  ('bare-actor', 'whieda', 'Без профиля', '7201', null);
                update lead_actors set telegram_user_id = 7001 where actor_id = 'igor';
                update lead_actors set telegram_user_id = 7201 where actor_id = 'bare-actor';
                update lead_actors set telegram_user_id = 6001 where actor_id = 'proof-partner';
                insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, enabled) values
                  ('igoref', 'whieda', 'igor', 'named', true),
                  ('petrovna', 'whieda', 'proof-partner', 'named', true),
                  ('nobody', 'whieda', 'no-telegram', 'named', true),
                  ('igor2ref', 'whieda', 'igor2', 'named', true);
                insert into partner_subscriptions (tenant_id, ref_code, paid_until) values
                  ('whieda', 'igoref', now() + interval '40 days'),
                  ('whieda', 'petrovna', now() - interval '1 day'),
                  ('whieda', 'nobody', now() - interval '1 day'),
                  ('whieda', 'igor2ref', now() + interval '10 days');
                -- Тестовые цены только в этой одноразовой базе: у боевой полки цену назначит владелец.
                insert into partner_subscription_plans
                  (tenant_id, plan_code, product_code, access_months, price_wusd_minor, price_rub_minor,
                   active, valid_from, title, sort_order, course_slug)
                values
                  ('whieda', 'academy_shelf_3m', 'academy_shelf', 3, 1111, 111100, true, now() - interval '1 day',
                   'Полка Академии на 3 месяца', 200, null),
                  ('whieda', 'course_neuro', 'course_neuro', 0, 15000, 1500000, true, now() - interval '1 day',
                   'Курс «Нейросети»', 110, 'neuro'),
                  ('whieda', 'course_nocourse', 'course_nocourse', 0, 10000, 1000000, true, now() - interval '1 day',
                   'Курс без курса', 120, null);
                insert into academy_courses (tenant_id, slug, title, access_rule, status)
                values ('whieda', 'neuro', 'Нейросети', 'purchase', 'published');
                """
            )
            db.grant_api_role(conn)

        # Владелец грузит курс автора: черновик, продаёт автор (purchase).
        loaded = loader.load("whieda", BUNDLE, author="igor", dsn=db.admin_dsn)
        assert (loaded["status"], loaded["access_rule"], loaded["author"]) == ("draft", "purchase", "igor")
        # Нет actor / нет включённого профиля / профиль есть, но нет Telegram (не заплатит в боте).
        for not_an_author in ("stranger", "bare-actor", "no-telegram"):
            with pytest.raises(SystemExit):
                loader.load("whieda", BUNDLE, author=not_an_author, dsn=db.admin_dsn)

        async def proof() -> None:
            from app.academy.keys import (
                AcademyKeyError,
                author_course_stats,
                issue_keys,
                issue_keys_for_telegram,
                redeem_key,
            )
            from app.academy.service import AcademyError, course_outline, list_courses, load_viewer
            from app.db import fetch_all, tenant_connection
            from app.renewal_requests.service import (
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

            async def pay(actor_id: str, plan_code: str, message_id: int) -> dict:
                await begin_renewal_request("whieda", actor_id)
                await set_renewal_plan("whieda", actor_id, plan_code)
                req = await set_renewal_country("whieda", actor_id, "RU")
                chat = {"igor": 7001, "igor2": 7001, "proof-partner": 6001}[actor_id]
                req = await submit_renewal_payment_proof(
                    "whieda", actor_id, chat_id=chat, message_id=message_id, file_id=f"proof-{message_id}"
                )
                return await confirm_renewal_request("whieda", request_id=str(req["request_id"]), admin_telegram_user_id=1)

            student = await load_viewer("whieda", STUDENT)
            assert not student.partner_paid and not student.is_preview_admin

            # 1. Черновик автора ученик не видит.
            assert [c["slug"] for c in await list_courses("whieda", student)] == ["neuro"]
            with pytest.raises(AcademyError) as missing:
                await course_outline("whieda", "kurs-igorya", student)
            assert missing.value.code == "course_not_found"

            # 2. Публикация → курс виден с замком и контактом автора.
            published = loader.publish("whieda", "kurs-igorya", dsn=db.admin_dsn)
            assert published["status"] == "published"
            # Перезаливка без флагов (опечатка): курс автора не прячется и не открывается всем PRO.
            reloaded = loader.load("whieda", BUNDLE, dsn=db.admin_dsn)
            assert (reloaded["status"], reloaded["access_rule"]) == ("published", "purchase")
            course = next(c for c in await list_courses("whieda", student) if c["slug"] == "kurs-igorya")
            assert course["locked"] and course["lock_reason"] == "purchase_required"
            assert course["author_contact"] == {"telegram": "igor_wwc", "site_url": "https://igoref.wwc.best"}
            with pytest.raises(AcademyError) as locked:
                await course_outline("whieda", "kurs-igorya", student)
            assert locked.value.status == 403 and locked.value.extra["author_contact"]["telegram"] == "igor_wwc"

            # 3. Полка не оплачена — ключей нет.
            with pytest.raises(AcademyKeyError) as unpaid:
                await issue_keys("whieda", "kurs-igorya", "igor", 3)
            assert unpaid.value.code == "shelf_expired"

            # 4. Полка оплачивается продлением (полка — на referral_profiles.owner_id = igor);
            #    сайт автора не продлевается.
            assert "academy_shelf_3m" in [o["plan_code"] for o in await list_renewal_offers("whieda")]
            site_before = await rows("select paid_until from partner_subscriptions where ref_code = 'igoref'")
            done = await pay("igor", "academy_shelf_3m", 11)
            assert done["status"] == "confirmed"
            shelf = await rows(
                "select actor_id, status, paid_until > now() + interval '85 days' as ok,"
                " paid_until < now() + interval '95 days' as not_too_far from academy_shelf"
            )
            assert shelf == [{"actor_id": "igor", "status": "active", "ok": True, "not_too_far": True}]
            ledger = await rows(
                "select l.product_code, l.amount_minor, l.access_months, l.period_end = s.paid_until as same_end"
                " from partner_payment_ledger l join academy_shelf s on s.actor_id = 'igor'"
                " where l.ref_code = 'igoref'"
            )
            assert ledger == [{"product_code": "academy_shelf", "amount_minor": 111100, "access_months": 3, "same_end": True}]
            assert await rows("select paid_until from partner_subscriptions where ref_code = 'igoref'") == site_before
            # Вторая оплата продлевает от конца, а не от сегодня; приостановку оплата не снимает.
            await pay("igor", "academy_shelf_3m", 12)
            extended = await rows("select paid_until > now() + interval '175 days' as ok from academy_shelf")
            assert extended == [{"ok": True}]
            async with tenant_connection("whieda") as conn:
                await conn.execute("update academy_shelf set status = 'suspended'")
            await pay("igor", "academy_shelf_3m", 13)
            assert await rows("select status from academy_shelf") == [{"status": "suspended"}]
            with pytest.raises(AcademyKeyError) as suspended:
                await issue_keys("whieda", "kurs-igorya", "igor", 1)
            assert suspended.value.code == "shelf_expired"

            # 4b. Полка на старой строке того же человека (профиль igor2ref, оплата владельцем):
            #     своя полка igor приостановлена, а ключи всё равно выдаются — полка у человека.
            from app.subscriptions.pricing import PaymentLine
            from app.subscriptions.service import record_payment_lines_in_connection

            async with tenant_connection("whieda") as conn:
                await record_payment_lines_in_connection(
                    conn, tenant_id="whieda", ref_code="igor2ref",
                    lines=[PaymentLine("academy_shelf", 1111, "WUSD", 3, False, "")],
                    received_minor=1111, currency="WUSD", telegram_chat_id=1, telegram_message_id=14,
                    telegram_user_id=1,
                )
            assert await rows("select actor_id, status from academy_shelf order by actor_id") == [
                {"actor_id": "igor", "status": "suspended"}, {"actor_id": "igor2", "status": "active"},
            ]

            # 5. Автор (course.author = igor, полка на igor2 — один человек) выдаёт 3 ключа.
            #    Повтор того же сообщения (inbox-воркер) — та же пачка, новых ключей нет.
            batch = "whieda-test-binding:7001:55"
            issued = await issue_keys_for_telegram(
                "whieda", "kurs-igorya", 7001, 3, is_admin=False, issued_for_message=batch
            )
            assert len(issued.codes) == 3 and len(set(issued.codes)) == 3 and not issued.reused
            retried = await issue_keys_for_telegram(
                "whieda", "kurs-igorya", 7001, 3, is_admin=False, issued_for_message=batch
            )
            assert retried.reused and sorted(retried.codes) == sorted(issued.codes)
            assert await rows("select count(*)::int as n from academy_access_keys") == [{"n": 3}]
            first, second, third = issued.codes

            # 6. Ученик гасит ключ — курс открыт.
            opened = await redeem_key("whieda", first, STUDENT)
            assert (opened.status, opened.course_slug) == ("opened", "kurs-igorya")
            course = next(c for c in await list_courses("whieda", student) if c["slug"] == "kurs-igorya")
            assert not course["locked"] and "author_contact" not in course
            outline = await course_outline("whieda", "kurs-igorya", student)
            assert [lesson["slug"] for lesson in outline["lessons"]] == ["vvedenie", "praktika"]
            access = await rows(
                "select source, payment_ref from academy_access where telegram_user_id = %s", (STUDENT,)
            )
            assert access == [{"source": "key", "payment_ref": first}]

            # 7. Тот же человек повторно — «уже открыт», использование не списано.
            again = await redeem_key("whieda", first, STUDENT)
            assert again.status == "already_open"
            also = await redeem_key("whieda", second, STUDENT)  # чужой ключ ему не нужен — не тратим
            assert also.status == "already_open"
            used = await rows("select code, used_count from academy_access_keys order by code")
            assert {r["code"]: r["used_count"] for r in used} == {first: 1, second: 0, third: 0}

            # 8. Второй человек на одноразовый ключ — отказ.
            with pytest.raises(AcademyKeyError) as exhausted:
                await redeem_key("whieda", first, SECOND_STUDENT)
            assert exhausted.value.code == "key_exhausted"
            with pytest.raises(AcademyKeyError) as unknown:
                await redeem_key("whieda", "zzzzzzzzzzzz", SECOND_STUDENT)
            assert unknown.value.code == "key_not_found"

            # 9. «Мои курсы»: 1 из 3, один ученик.
            stats = await author_course_stats("whieda", ["igor"])
            assert [(s["slug"], s["keys_used"], s["keys_capacity"], s["students"]) for s in stats] == [
                ("kurs-igorya", 1, 3, 1)
            ]

            # 10. Полка истекла: ключи не выдаются и не гасятся (ключ не тратится),
            #     у ученика с доступом курс остаётся открыт.
            async with tenant_connection("whieda") as conn:
                await conn.execute("update academy_shelf set paid_until = now() - interval '1 day'")
            with pytest.raises(AcademyKeyError) as lapsed:
                await issue_keys("whieda", "kurs-igorya", "igor", 1)
            assert lapsed.value.code == "shelf_expired"
            assert (await course_outline("whieda", "kurs-igorya", student))["course"]["slug"] == "kurs-igorya"
            with pytest.raises(AcademyKeyError) as late:
                await redeem_key("whieda", second, SECOND_STUDENT)
            assert late.value.code == "author_shelf_expired"
            assert late.value.extra["author_contact"]["telegram"] == "igor_wwc"
            assert await rows("select used_count from academy_access_keys where code = %s", (second,)) == [
                {"used_count": 0}
            ]
            # Владелец выдаёт ключи без полки.
            owner_keys = await issue_keys_for_telegram("whieda", "kurs-igorya", 1, 2, is_admin=True)
            assert len(owner_keys.codes) == 2

            # 11. Оплата курса с course_slug → доступ владельцу профиля.
            buyer = await load_viewer("whieda", 6001)
            neuro = next(c for c in await list_courses("whieda", buyer) if c["slug"] == "neuro")
            assert neuro["lock_reason"] == "purchase_required"
            await pay("proof-partner", "course_neuro", 21)
            bought = await rows(
                "select a.source, a.payment_ref = l.payment_id::text as ref_ok from academy_access a"
                " join partner_payment_ledger l on l.product_code = 'course_neuro'"
                " where a.telegram_user_id = 6001"
            )
            assert bought == [{"source": "purchase", "ref_ok": True}]
            neuro = next(c for c in await list_courses("whieda", buyer) if c["slug"] == "neuro")
            assert not neuro["locked"]
            # Строк course_* в partner_product_access больше нет: доступ — в Академии.
            assert await rows("select 1 from partner_product_access where left(product_code, 7) = 'course_'") == []

            # 12. Курс без course_slug и владелец без Telegram: платёж проходит, доступа нет.
            done = await pay("proof-partner", "course_nocourse", 22)
            assert done["status"] == "confirmed"
            from app.subscriptions.pricing import PaymentLine
            from app.subscriptions.service import record_payment_lines_in_connection

            async with tenant_connection("whieda") as conn:
                paid = await record_payment_lines_in_connection(
                    conn, tenant_id="whieda", ref_code="nobody",
                    lines=[PaymentLine("course_neuro", 15000, "WUSD", 0, False, "")],
                    received_minor=15000, currency="WUSD", telegram_chat_id=1, telegram_message_id=23,
                    telegram_user_id=1,
                )
            assert [line["product_code"] for line in paid["lines"]] == ["course_neuro"]
            assert await rows("select count(*)::int as n from academy_access where source = 'purchase'") == [{"n": 1}]

        db.run_with_app(proof)
