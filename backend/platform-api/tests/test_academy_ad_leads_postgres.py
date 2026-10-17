"""Заявки с рекламы витрины на настоящем PostgreSQL: строка таблицы → одна запись outbox.

  * строки до ``since`` не шлются (старые заявки таблицы не дублируем);
  * повторный опрос и «рестарт» (сброс таймера опроса) новых записей не дают;
  * алерт уходит администратору витрины её ботом, с кнопкой таблицы;
  * без бота витрины в этом процессе (staging) таблица не читается.
"""

from __future__ import annotations

import json

import psycopg
import pytest

from tests.postgres_testkit import temporary_database
from tests.test_academy_ad_leads import CSV

SEED = """
insert into academy_showcases (tenant_id, showcase_id, hosts, owner_actor_id, brand_name, academy_title,
                               telegram_binding_id, more_mode, support)
values ('whieda', 'almira', array['academy.almira.by'], null, 'ALMIRA', 'Онлайн-академия ALMIRA',
        'almira-academy-bot', 'none',
        '{"admin_telegram_user_id": 5035800725,
          "leads_sheet": {"id": "1-HNjlo54nJvCDNwJ6H-6PNGiOF2xdSKVNwIzp_0Lb5g", "gid": "1983230657",
                          "since": "2026-10-16T16:45:00+03:00"}}');
"""


@pytest.mark.integration
def test_ad_leads_step_queues_each_new_row_once(monkeypatch):
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    with temporary_database("whieda_ad_leads") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy import ad_leads
            from app.academy import showcases as sc
            from app.academy.ad_leads import parse_leads
            from app.db import fetch_all, tenant_connection

            sc._reset_cache()
            ad_leads._last_poll.clear()
            calls = []

            async def fake_fetch(config):
                calls.append(config["gid"])
                return parse_leads(CSV)

            monkeypatch.setattr(ad_leads, "fetch_leads", fake_fetch)
            bindings = {"almira-academy-bot": object()}

            # Staging: бот витрины не разрешился — таблицу не читаем.
            assert await ad_leads.ad_leads_step({"whieda"}, {}, now=0.0) == 0 and calls == []

            # 16:40 — до since (16:45): не шлём; 16:54 и 17:10 — шлём.
            assert await ad_leads.ad_leads_step({"whieda"}, bindings, now=100.0) == 2
            # Тот же опрос через 30 с — интервал не прошёл, таблицу не дёргаем.
            assert await ad_leads.ad_leads_step({"whieda"}, bindings, now=130.0) == 0 and calls == ["1983230657"]
            # «Рестарт»: таймер сброшен, таблица прочитана снова — дублей нет.
            ad_leads._last_poll.clear()
            assert await ad_leads.ad_leads_step({"whieda"}, bindings, now=500.0) == 0 and len(calls) == 2

            async with tenant_connection("whieda") as conn:
                rows = await fetch_all(
                    conn,
                    "select event_type, idempotency_key, status, payload from platform_outbox order by idempotency_key",
                )
            assert [(r["event_type"], r["idempotency_key"], r["status"]) for r in rows] == [
                ("showcase_ad_lead_row", "ad_lead:almira:222", "scheduled"),
                ("showcase_ad_lead_row", "ad_lead:almira:333", "scheduled"),
            ]
            payload = rows[0]["payload"] if isinstance(rows[0]["payload"], dict) else json.loads(rows[0]["payload"])
            assert payload["chat_id"] == "5035800725" and payload["binding_id"] == "almira-academy-bot"
            assert payload["text"].startswith("🔔 Новая заявка · вебинар 24 октября\n<b>Мария Иванова</b>")
            assert payload["reply_markup"]["inline_keyboard"][0][0]["url"].startswith("https://docs.google.com/spreadsheets/d/")

        db.run_with_app(proof)


@pytest.mark.integration
def test_alert_waits_for_bot_open_and_carries_the_flag(monkeypatch):
    """Открыл бота — алерт сразу с «✅ Бот открыт» и ссылкой на чат; не открыл — ждём OPEN_GRACE."""
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    with temporary_database("whieda_ad_opens") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from datetime import datetime, timezone

            from app.academy import ad_leads
            from app.academy import showcases as sc
            from app.academy.ad_leads import parse_leads
            from app.db import fetch_all, tenant_connection

            sc._reset_cache()
            ad_leads._last_poll.clear()

            async def fake_fetch(config):
                return parse_leads(CSV)

            monkeypatch.setattr(ad_leads, "fetch_leads", fake_fetch)
            bindings = {"almira-academy-bot": object()}
            [showcase] = [s for s in await sc.load_showcases("whieda", fresh=True) if s.showcase_id == "almira"]
            [_, maria, organic] = parse_leads(CSV)

            # Мария открыла бота (в Telegram — «Мария Иванова», ника нет), строку при открытии не нашли.
            await ad_leads.record_open(
                "whieda", "almira", telegram_user_id=777, username=None, name="Мария Иванова", tag="ig_web24",
                lead_id=None,
            )
            # Повторное открытие: ник появился, строка нашлась — запись одна, ничего не теряется.
            await ad_leads.record_open(
                "whieda", "almira", telegram_user_id=777, username="maria_i", name="Мария Иванова", tag="ig_web24",
                lead_id="222",
            )
            assert await ad_leads.alert_pending("whieda", showcase, maria) is True

            # 14:11 UTC: строке 333 (14:10) минута, бота не открывали — ждём; 222 открыт — шлём.
            early = datetime(2026, 10, 16, 14, 11, tzinfo=timezone.utc)
            assert await ad_leads.ad_leads_step({"whieda"}, bindings, now=100.0, clock=early) == 1
            assert await ad_leads.alert_pending("whieda", showcase, maria) is False
            assert await ad_leads.alert_pending("whieda", showcase, organic) is True
            # 14:14 UTC: три минуты прошли — 333 уходит с «Бот не открыт».
            late = datetime(2026, 10, 16, 14, 14, tzinfo=timezone.utc)
            assert await ad_leads.ad_leads_step({"whieda"}, bindings, now=300.0, clock=late) == 1

            async with tenant_connection("whieda") as conn:
                rows = await fetch_all(conn, "select idempotency_key, payload from platform_outbox order by idempotency_key")
                opens = await fetch_all(conn, "select telegram_user_id, telegram_username, lead_id from academy_ad_opens")
            assert [dict(r) for r in opens] == [{"telegram_user_id": 777, "telegram_username": "maria_i", "lead_id": "222"}]
            texts = {
                r["idempotency_key"]: (r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"]))["text"]
                for r in rows
            }
            assert '<b><a href="tg://user?id=777">Мария Иванова</a></b>\n✅ Бот открыт' in texts["ad_lead:almira:222"]
            assert "⚠️ Бот не открыт — свяжитесь напрямую" in texts["ad_lead:almira:333"]

        db.run_with_app(proof)
