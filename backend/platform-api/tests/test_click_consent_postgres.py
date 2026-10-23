"""Согласие с офертой и политикой по нажатию на настоящей PostgreSQL (23.10.2026).

Вся цепочка обработчиков бота, Telegram подменён, миграции настоящие:

  * «Заказать сайт» без «Принимаю» не создаёт строку в ``partner_site_requests``;
    «Принимаю» пишет в ``telegram_consents`` строку ``site_order:<дата>`` и создаёт анкету;
  * старая запись «продолжая общение» (``policy_version`` — голая дата) не тронута и
    согласием по клику не считается: человек с ней спрашивается один раз;
  * повторный заказ того же человека не переспрашивает, строка остаётся первой (время клика);
  * человек с открытой анкетой (начата до правила) продолжает без вопроса;
  * вход через бота пишет ``site_login:<дата>`` один раз на версию текста; через бот витрины
    автора Академии (ALMIRA) — ни строки об оферте, ни записи ``site_login`` (документы WWC не её);
  * чужой тенант этих строк не видит (RLS).
"""

from __future__ import annotations

import itertools
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

MIGRATION_SET = (*MIGRATIONS, "platform_service_sales_v10.sql", "platform_telegram_consent_v1.sql")
BINDING = "whieda-advisor-bot"
NEW = 72001  # ни разу не нажимал «Принимаю»; старая запись «продолжая общение»
OPEN = 72002  # анкета открыта до правила
BROKEN = 72003  # запись согласия падает
STUDENT = 72004  # входит через бот витрины ALMIRA

SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id) values
  ('new', 'whieda', 'Анна', 72001, '72001'),
  ('open', 'whieda', 'Ирина', 72002, '72002'),
  ('broken', 'whieda', 'Вера', 72003, '72003');
insert into partner_site_requests (tenant_id, actor_id, status, country_code, requested_subdomain)
  values ('whieda', 'open', 'awaiting_photo', 'RU', 'irina-open');
insert into telegram_consents (tenant_id, telegram_user_id, telegram_chat_id, policy_version)
  values ('whieda', 72001, 72001, '2026-09-18');
"""


def _callback(user: int, data: str) -> dict:
    return {"callback_query": {"id": f"cb-{user}-{data}", "data": data, "from": {"id": user, "first_name": "Анна"},
                               "message": {"message_id": 5, "chat": {"id": user, "type": "private"}}}}


def _login(user: int, token: str) -> dict:
    return {"update_id": 800000 + user, "message": {"text": f"/start content_access_{token}",
                                                    "chat": {"id": user, "type": "private"}, "from": {"id": user}}}


@pytest.mark.integration
def test_click_consent_for_site_order_and_site_login():
    with temporary_database("whieda_click_consent") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, MIGRATION_SET)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, fetch_one, tenant_connection
            from app.telegram.bindings import BotBindingContext
            from app.telegram.consent import CLICK_CONSENT_VERSION, CONSENT_POLICY_VERSION
            from app.telegram.processor import process_core_telegram_update
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                                   entitlements={"structure_basic": True, "partner_leads": True, "site_support": True})
            binding = BotBindingContext(
                binding_id=BINDING, tenant=tenant, bot_token_ref="env:PROOF", webhook_secret_ref="env:PROOF",
                bot_username="WHIEDA_Advisor_bot", status="active", processing_mode="core",
                bot_token="proof-token", webhook_secret="proof-secret",
            )
            ids = itertools.count(3000)

            async def fake_send(**kwargs):
                return {"ok": True, "message_id": next(ids)}

            send = AsyncMock(side_effect=fake_send)
            deliver_login = AsyncMock()
            confirm = AsyncMock(return_value={"ok": True, "status": "approved", "challenge_id": "c-1",
                                              "return_to": "https://wwc.best/academy/"})
            quiet = {
                "app.telegram.processor.link_lead_actor_by_username": AsyncMock(return_value=None),
                "app.telegram.processor.fill_lead_actor_user_id": AsyncMock(return_value=None),
                "app.telegram.site_requests.send_telegram_text": send,
                "app.telegram.site_requests.answer_callback_query": AsyncMock(return_value={"ok": True}),
                "app.telegram.content_access.deliver_text": deliver_login,
                "app.telegram.content_access.confirm_content_from_telegram": confirm,
            }
            patches = [patch(target, mock) for target, mock in quiet.items()]
            for item in patches:
                item.start()

            async def press(user: int, data: str) -> str:
                send.reset_mock()
                await process_core_telegram_update(tenant, _callback(user, data), "click", binding=binding)
                sent = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(user)]
                return sent[-1]["text"] if sent else ""

            async def requests_of(actor: str) -> list[dict]:
                async with tenant_connection("whieda") as conn:
                    return await fetch_all(
                        conn, "select status from partner_site_requests where actor_id = %s order by created_at", (actor,)
                    )

            async def consents(user: int) -> dict[str, dict]:
                async with tenant_connection("whieda") as conn:
                    rows = await fetch_all(
                        conn,
                        "select policy_version, telegram_chat_id, consented_at from telegram_consents "
                        "where telegram_user_id = %s order by policy_version",
                        (user,),
                    )
                return {row["policy_version"]: row for row in rows}

            order_version = f"site_order:{CLICK_CONSENT_VERSION}"
            login_version = f"site_login:{CLICK_CONSENT_VERSION}"
            try:
                # 1. Без клика: вопрос с кнопкой, заявки нет, согласия по клику нет.
                text = await press(NEW, "site:create")
                assert "Оформляя заказ, вы принимаете оферту и политику обработки персональных данных" in text
                assert "https://wwc.best/offer/" in text and "https://wwc.best/privacy-policy/" in text
                assert [b["callback_data"] for row in send.await_args_list[-1].kwargs["reply_markup"]["inline_keyboard"]
                        for b in row] == ["site:accept"]
                assert await requests_of("new") == []
                assert set(await consents(NEW)) == {CONSENT_POLICY_VERSION}  # старая запись «продолжая общение»

                # Повторное «Заказать сайт» без клика — снова вопрос, заявки по-прежнему нет.
                assert "Оформляя заказ" in await press(NEW, "site:create")
                assert await requests_of("new") == []

                # 2. «Принимаю»: согласие с источником site_order, затем анкета.
                assert "имя и фамилию" in await press(NEW, "site:accept")
                assert [r["status"] for r in await requests_of("new")] == ["awaiting_name"]
                rows = await consents(NEW)
                assert set(rows) == {CONSENT_POLICY_VERSION, order_version}
                assert rows[order_version]["telegram_chat_id"] == NEW and rows[order_version]["consented_at"] is not None
                old_stamp = rows[CONSENT_POLICY_VERSION]["consented_at"]

                # Двойное нажатие: одна строка, анкета та же.
                assert "имя и фамилию" in await press(NEW, "site:accept")
                assert len(await requests_of("new")) == 1
                first_click = (await consents(NEW))[order_version]["consented_at"]

                # 3. Повторный заказ после закрытой анкеты — без вопроса; время клика прежнее.
                async with tenant_connection("whieda") as conn:
                    await conn.execute("update partner_site_requests set status = 'cancelled' where actor_id = 'new'")
                assert "имя и фамилию" in await press(NEW, "site:create")
                assert [r["status"] for r in await requests_of("new")] == ["cancelled", "awaiting_name"]
                after = await consents(NEW)
                assert after[order_version]["consented_at"] == first_click
                assert after[CONSENT_POLICY_VERSION]["consented_at"] == old_stamp  # старую не трогали

                # 4. Открытая анкета (до правила) продолжается без вопроса и без записи согласия.
                text = await press(OPEN, "site:create")
                assert "Оформляя заказ" not in text and "Пришлите ваше фото" in text  # шаг анкеты, где остановились
                assert await consents(OPEN) == {}

                # 5. Сбой записи согласия: заявки нет, человек видит причину.
                with patch("app.telegram.site_requests.record_click_consent",
                           AsyncMock(side_effect=RuntimeError("db down"))):
                    text = await press(BROKEN, "site:accept")
                assert "заявка не создана" in text
                assert await requests_of("broken") == [] and await consents(BROKEN) == {}

                # 6. Вход на сайт через бота: подтверждённый вход пишет site_login один раз.
                await process_core_telegram_update(tenant, _login(NEW, "tokA"), "login", binding=binding)
                message = deliver_login.await_args.args[1]
                assert "Вход подтверждён" in message and "Входя, вы принимаете оферту" in message
                assert "https://wwc.best/offer/" in message and "https://wwc.best/privacy-policy/" in message
                rows = await consents(NEW)
                assert login_version in rows and rows[login_version]["telegram_chat_id"] == NEW
                stamp = rows[login_version]["consented_at"]
                await process_core_telegram_update(tenant, _login(NEW, "tokB"), "login", binding=binding)
                rows = await consents(NEW)
                assert rows[login_version]["consented_at"] == stamp
                assert set(rows) == {CONSENT_POLICY_VERSION, order_version, login_version}

                # 6а. Бот витрины ALMIRA: тот же вход, но документы WWC не показываем и не записываем.
                from dataclasses import replace

                from app.academy.showcases import Showcase

                almira = Showcase(
                    tenant_id="whieda", showcase_id="almira", is_default=False, hosts=("academy.almira.by",),
                    owner_actor_id="almira", brand_name="ALMIRA", academy_title="Онлайн-академия ALMIRA",
                    links={"privacy_url": "https://almira.by/privacy-policy/"},
                    telegram_binding_id="almira-academy-bot",
                )
                almira_binding = replace(binding, binding_id="almira-academy-bot", showcase_id="almira")
                with patch("app.telegram.processor.showcase_for_binding", AsyncMock(return_value=almira)), patch(
                    "app.telegram.showcase_bot._first_policy_notice", AsyncMock()
                ), patch("app.telegram.showcase_bot._note_bot_open", AsyncMock()):
                    await process_core_telegram_update(tenant, _login(STUDENT, "tokC"), "login", binding=almira_binding)
                message = deliver_login.await_args.args[1]
                assert "Вход подтверждён" in message and "оферт" not in message.lower()
                assert await consents(STUDENT) == {}

                # 7. RLS: другой тенант этих строк не видит.
                async with tenant_connection("other-tenant") as conn:
                    assert await fetch_one(conn, "select 1 as x from telegram_consents") is None
            finally:
                for item in patches:
                    item.stop()

        db.run_with_app(proof)
