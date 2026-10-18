"""Напоминания о вебинаре 24.10 на настоящем PostgreSQL — по часам, как пройдёт день.

Анна открыла бота 22.10, Борис — 24.10 в 10:00, Светлана — по ссылке ig_web24, но её заявка на 31.10,
Дарья оставила заявку на 24.10 и бота не открыла.

  * за сутки — только Анне (Борис открыл бота позже, Светлана — на 31.10); повтор опроса — без дублей;
  * за 2 часа — Анне и Борису; ссылки нет: в 16:00 администратору одна строка, «за час» не уходит;
  * ссылку добавили — «за час» и «за 5 минут» со ссылкой и кнопкой;
  * в 17:00 администратору — «Не в боте» с Дарьей (телефон, WhatsApp, Telegram по номеру).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from tests.postgres_testkit import temporary_database
from tests.test_academy_ad_leads import HEADER

ADMIN = 5035800725
ANNA, BORIS, SVETA = 3001, 3002, 3003
MINSK = timezone(timedelta(hours=3))
NO_LINK = {
    "admin_telegram_user_id": ADMIN,
    "leads_sheet": {"id": "1-HNjlo54nJvCDNwJ6H-6PNGiOF2xdSKVNwIzp_0Lb5g", "gid": "1983230657", "since": "2026-10-20T00:00:00+03:00"},
    "webinars": {
        "ig_web24": {"at": "2026-10-24T17:30:00+03:00", "date": "24 октября", "url": ""},
        "ig_web31": {"at": "2026-10-31T17:30:00+03:00", "date": "31 октября", "url": ""},
    },
}
SEED = f"""
insert into academy_showcases (tenant_id, showcase_id, hosts, owner_actor_id, brand_name, academy_title,
                               telegram_binding_id, more_mode, support)
values ('whieda', 'almira', array['academy.almira.by'], null, 'ALMIRA', 'Онлайн-академия ALMIRA',
        'almira-academy-bot', 'none', '{json.dumps(NO_LINK, ensure_ascii=False)}');
insert into academy_ad_opens (tenant_id, showcase_id, telegram_user_id, telegram_username, name, tag, lead_id, opened_at) values
  ('whieda', 'almira', {ANNA}, 'anna_a', 'Анна', 'ig_web24', '701', '2026-10-22T12:00:00+03:00'),
  ('whieda', 'almira', {BORIS}, null, 'Борис', 'ig_web24', '702', '2026-10-24T10:00:00+03:00'),
  ('whieda', 'almira', {SVETA}, null, 'Светлана', 'ig_web24', '704', '2026-10-22T12:00:00+03:00');
"""
CSV = "\n".join([
    HEADER,
    "l:701,2026-10-22T11:59:00+03:00,,,,,,,f:4,Вебинар 24.10,false,ig,бесплатный_вебинар_24_октября,нет,anna_a,Анна Ли,p:+375291000001,CREATED,,",
    "l:702,2026-10-24T09:59:00+03:00,,,,,,,f:4,Вебинар 24.10,false,ig,бесплатный_вебинар_24_октября,нет,,Борис,p:+375291000002,CREATED,,",
    "l:703,2026-10-23T09:00:00+03:00,,,,,,,f:4,Вебинар 24.10,false,ig,бесплатный_вебинар_24_октября,нет,,Дарья Звонкова,p:+375291000003,CREATED,,",
    "l:704,2026-10-22T11:59:00+03:00,,,,,,,f:5,Вебинар 31.10,false,ig,бесплатный_вебинар_31_октября,нет,,Светлана,p:+375291000004,CREATED,,",
])


def at(day: int, hour: int, minute: int) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=MINSK)


@pytest.mark.integration
def test_webinar_day_reminders_link_gate_dedupe_and_call_list(monkeypatch):
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    with temporary_database("whieda_webinars") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy import showcases as sc
            from app.academy import webinars
            from app.academy.ad_leads import parse_leads
            from app.db import fetch_all, tenant_connection

            async def fake_fetch(config):
                return parse_leads(CSV)

            monkeypatch.setattr(webinars, "fetch_leads", fake_fetch)
            bindings = {"almira-academy-bot": object()}

            async def step(when: datetime) -> int:
                sc._reset_cache()
                return await webinars.webinar_step({"whieda"}, bindings, clock=when)

            async def sent() -> dict[str, dict]:
                async with tenant_connection("whieda") as conn:
                    rows = await fetch_all(conn, "select idempotency_key, payload from platform_outbox where event_type = %s", (webinars.EVENT,))
                return {r["idempotency_key"]: (r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])) for r in rows}

            # Без бота витрины в процессе (staging) — ничего.
            assert await webinars.webinar_step({"whieda"}, {}, clock=at(23, 17, 31)) == 0
            assert await step(at(23, 17, 31)) == 1  # за сутки — только Анне
            assert await step(at(23, 17, 40)) == 0  # повтор — без дублей
            assert await step(at(24, 15, 31)) == 2  # за 2 часа — Анне и Борису
            assert await step(at(24, 16, 1)) == 1  # нет ссылки — строка администратору
            assert await step(at(24, 16, 31)) == 0  # без ссылки «за час» не уходит
            keys = set(await sent())
            assert keys == {
                f"webinar:almira:ig_web24:day:{ANNA}",
                f"webinar:almira:ig_web24:two_hours:{ANNA}", f"webinar:almira:ig_web24:two_hours:{BORIS}",
                "webinar:almira:ig_web24:no_link",
            }

            with_link = {**NO_LINK, "webinars": {**NO_LINK["webinars"], "ig_web24": {
                **NO_LINK["webinars"]["ig_web24"], "url": "https://meet.google.com/abc-defg-hij"}}}
            async with tenant_connection("whieda") as conn:
                async with conn.cursor() as cur:
                    await cur.execute("update academy_showcases set support = %s::jsonb where showcase_id = 'almira'",
                                      (json.dumps(with_link, ensure_ascii=False),))
            assert await step(at(24, 16, 35)) == 2  # за час — со ссылкой
            assert await step(at(24, 17, 1)) == 1  # «Не в боте» — Дарья
            assert await step(at(24, 17, 26)) == 2  # за 5 минут
            assert await step(at(24, 17, 27)) == 0

            payloads = await sent()
            hour = payloads[f"webinar:almira:ig_web24:hour:{BORIS}"]
            assert hour["chat_id"] == str(BORIS) and "https://meet.google.com/abc-defg-hij" in hour["text"]
            assert hour["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://meet.google.com/abc-defg-hij"
            assert payloads["webinar:almira:ig_web24:no_link"]["chat_id"] == str(ADMIN)
            calls = payloads["webinar:almira:ig_web24:calls:0"]
            assert calls["chat_id"] == str(ADMIN) and "(1 чел.)" in calls["text"]
            assert "Дарья Звонкова" in calls["text"] and "https://wa.me/375291000003" in calls["text"]
            assert "Анна" not in calls["text"] and "Светлана" not in calls["text"]
            # Светлане (заявка на 31.10) напоминания 24-го не уходили.
            assert not [k for k in payloads if k.endswith(f":{SVETA}")]

        db.run_with_app(proof)


VITA = 3005
CONFIRM_CSV = CSV + "\n" + (
    "l:705,2026-10-23T11:59:30+03:00,,,,,,,f:4,Вебинар 24.10,false,ig,бесплатный_вебинар_31_октября,нет,,"
    "Вита Василюк,p:+375291000005,CREATED,,"
)


@pytest.mark.integration
def test_confirmation_by_form_choice_and_move_button(monkeypatch):
    """Вита открыла бота по ig_web24 через 30 с после формы с выбором «31 октября»: подтверждение — 31,
    кнопка — «Перенести на 24 октября»; после переноса напоминания идут по 24-му. Анна (открыла 22.10) —
    без подтверждения: больше 10 минут."""
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    with temporary_database("whieda_webinar_confirm") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(SEED)
            conn.execute(
                "insert into academy_ad_opens (tenant_id, showcase_id, telegram_user_id, telegram_username, name, tag, opened_at)"
                f" values ('whieda', 'almira', {VITA}, null, 'Вита', 'ig_web24', '2026-10-23T12:00:00+03:00')"
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy import showcases as sc
            from app.academy import webinars
            from app.academy.ad_leads import parse_leads
            from app.db import fetch_all, fetch_one, tenant_connection

            async def fake_fetch(config):
                return parse_leads(CONFIRM_CSV)

            monkeypatch.setattr(webinars, "fetch_leads", fake_fetch)
            webinars._last_confirm_fetch.clear()
            bindings = {"almira-academy-bot": object()}

            async def step(when: datetime) -> int:
                sc._reset_cache()
                return await webinars.webinar_step({"whieda"}, bindings, clock=when)

            assert await step(at(23, 12, 1)) == 1  # подтверждение Вите
            assert await step(at(23, 12, 3)) == 0  # повтор — без дубля
            async with tenant_connection("whieda") as conn:
                row = await fetch_one(conn, "select payload from platform_outbox where idempotency_key = %s", (f"webinar:almira:confirm:{VITA}",))
                stored = await fetch_one(conn, "select lead_id from academy_ad_opens where telegram_user_id = %s", (VITA,))
            payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
            assert payload["text"] == "Вы записаны на 31 октября, начало в 17:30 по Минску."
            assert payload["reply_markup"] == {"inline_keyboard": [[{"text": "Перенести на 24 октября", "callback_data": "wbn:ig_web24"}]]}
            assert stored["lead_id"] == "705"  # строка найдена по времени и запомнена

            [showcase] = [s for s in await sc.load_showcases("whieda", fresh=True) if s.showcase_id == "almira"]
            # Старая кнопка на прошедший эфир — на ближайший (31-е); неизвестный вебинар — нет.
            redirected, _ = await webinars.move_webinar("whieda", showcase, VITA, "ig_web24", now=at(25, 9, 0))
            assert redirected.tag == "ig_web31"
            assert await webinars.move_webinar("whieda", showcase, VITA, "ig_old", now=at(23, 12, 5)) is None
            webinar, opened = await webinars.move_webinar("whieda", showcase, VITA, "ig_web24", now=at(23, 12, 5))
            assert (webinar.tag, opened.webinar, opened.lead_id) == ("ig_web24", "ig_web24", "705")

            # За сутки до 24-го — Анне и теперь Вите (выбор в боте важнее «31 октября» в форме).
            assert await step(at(23, 17, 31)) == 2
            async with tenant_connection("whieda") as conn:
                keys = {r["idempotency_key"] for r in await fetch_all(conn, "select idempotency_key from platform_outbox")}
            assert f"webinar:almira:ig_web24:day:{VITA}" in keys and f"webinar:almira:ig_web24:day:{ANNA}" in keys
            # Вита в боте — в «Не в боте» 24-го её нет, а за сутки до 31-го ей ничего не уходит.
            assert await step(at(30, 17, 31)) == 1  # только Светлана (31 октября по форме)
            async with tenant_connection("whieda") as conn:
                keys = {r["idempotency_key"] for r in await fetch_all(conn, "select idempotency_key from platform_outbox")}
            assert f"webinar:almira:ig_web31:day:{VITA}" not in keys and f"webinar:almira:ig_web31:day:{SVETA}" in keys

        db.run_with_app(proof)


FOLLOWUP = {**NO_LINK, "webinars": {
    **NO_LINK["webinars"],
    "ig_web24": {**NO_LINK["webinars"]["ig_web24"], "followup": {"at": "2026-10-25T12:00:00+03:00", "to": "ig_web31"}},
}}
# Борис оплатил — Кирилл отметил в колонке «Статус».
PAID_CSV = CSV.replace(
    "Борис,p:+375291000002,CREATED,,", "Борис,p:+375291000002,CREATED,Оплатил,",
)


@pytest.mark.integration
def test_followup_to_repeat_skips_paid_and_old_button_goes_to_next(monkeypatch):
    """25.10 12:00 — «повтор 31 октября» записанным на 24.10, кроме оплативших; один раз. Нажатие — на 31.10,
    напоминания 31-го идут ему; старая кнопка «на 24 октября» после эфира — тоже на 31.10."""
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    with temporary_database("whieda_webinar_followup") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(SEED)
            conn.execute("update academy_showcases set support = %s::jsonb where showcase_id = 'almira'",
                         (json.dumps(FOLLOWUP, ensure_ascii=False),))
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy import showcases as sc
            from app.academy import webinars
            from app.academy.ad_leads import parse_leads
            from app.db import fetch_all, fetch_one, tenant_connection

            async def fake_fetch(config):
                return parse_leads(PAID_CSV)

            monkeypatch.setattr(webinars, "fetch_leads", fake_fetch)
            webinars._last_confirm_fetch.clear()
            bindings = {"almira-academy-bot": object()}

            async def step(when: datetime) -> int:
                sc._reset_cache()
                return await webinars.webinar_step({"whieda"}, bindings, clock=when)

            assert await step(at(25, 11, 59)) == 0
            assert await step(at(25, 12, 1)) == 1  # Анна; Борис оплатил, Светлана — на 31-е
            assert await step(at(25, 12, 30)) == 0  # один раз
            async with tenant_connection("whieda") as conn:
                row = await fetch_one(conn, "select payload from platform_outbox where idempotency_key = %s",
                                      (f"webinar:almira:ig_web24:followup:{ANNA}",))
            payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
            assert payload["text"].startswith("Не получилось прийти на вебинар 24 октября или хотите пересмотреть?")
            assert payload["reply_markup"]["inline_keyboard"][0][0] == {"text": "Записаться на 31 октября", "callback_data": "wbn:ig_web31"}

            [showcase] = [s for s in await sc.load_showcases("whieda", fresh=True) if s.showcase_id == "almira"]
            webinar, _ = await webinars.move_webinar("whieda", showcase, ANNA, "ig_web31", now=at(25, 12, 5))
            assert webinar.tag == "ig_web31"
            # Старая кнопка «Перенести на 24 октября» после эфира — на ближайший, 31-е.
            redirected, opened = await webinars.move_webinar("whieda", showcase, BORIS, "ig_web24", now=at(25, 13, 0))
            assert (redirected.tag, opened.webinar) == ("ig_web31", "ig_web31")

            assert await step(at(30, 17, 31)) == 3  # за сутки до 31-го: Светлана, Анна, Борис
            async with tenant_connection("whieda") as conn:
                keys = {r["idempotency_key"] for r in await fetch_all(conn, "select idempotency_key from platform_outbox")}
            assert {f"webinar:almira:ig_web31:day:{uid}" for uid in (ANNA, BORIS, SVETA)} <= keys

        db.run_with_app(proof)
