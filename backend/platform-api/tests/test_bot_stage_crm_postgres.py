"""CRM → бот на настоящем PostgreSQL (V49, роль API без bypassrls, 04.11.2026).

Карточка CRM переходит на этап «Партнёр» (PATCH) → у человека карточки есть бот (заявка, открытая в боте) → ведущему
уходит вопрос «Да» / «Нет» строкой platform_outbox → «Да» ставит человеку partner_whieda и пишет ему, владельцу — уведомление:

  * вопрос ставится один раз на карточку (повторный перевод и повтор «Да» ничего не дублируют);
  * карточка без человека в боте, человек уже партнёр (WHIEDA или WWC), три меню выключены — вопроса нет;
  * человек, которого в боте ещё не было, при открытии бота по ссылке партнёра: карточка, уже стоявшая на «Партнёр»,
    даёт вопрос ведущему;
  * «Нет» ничего не меняет; чужой партнёр кнопку нажать не может; человек в Max подтверждается так же.

Telegram и Max не вызываются: отправка — мок. Переход выполняет настоящий crm.service.update_contact.
"""

from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, SQL_DIR, temporary_database
from tests.test_crm_postgres import LEAD_TABLES, SEED
from tests.test_meeting_registrations_postgres import _env

V44 = "platform_website_leads_meeting_v44.sql"
HOST = 7001
OWNER = 5000
TENANT_ID = "whieda"

# (имя, telegram id в заявке, max id в заявке): кто есть в боте
PEOPLE = {
    "anna": ("Анна Петрова", 8800, None),
    "boris": ("Борис Иванов", None, None),      # заявка без бота
    "clara": ("Клара Смирнова", None, 9900),    # человек в Max
    "dina": ("Дина Орлова", 8803, None),        # откажем
    "petr": ("Пётр Сидоров", 7002, None),       # уже партнёр WWC (профиль сайта)
    "egor": ("Егор Белов", 8804, None),         # уже партнёр WHIEDA
    "zhanna": ("Жанна Волкова", None, None),    # карточка переведена до того, как человек открыл бота
}


@pytest.mark.integration
def test_crm_card_to_partner_asks_the_host_and_yes_promotes_the_person(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("PLATFORM_BOT_STAGES", "true")
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    from app.settings import get_settings

    get_settings.cache_clear()

    with temporary_database("whieda_stage_crm") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, MIGRATIONS, twice=True)
            conn.execute(LEAD_TABLES)
            conn.execute("alter table website_leads add column deleted_at timestamptz")  # есть на бою
            conn.execute((SQL_DIR / V44).read_text(encoding="utf-8"))
            conn.execute(SEED)
            conn.execute(
                "insert into tenant_bot_bindings (binding_id, tenant_id, webhook_secret_ref, status) "
                "values ('whieda-advisor-bot', 'whieda', 'env:X', 'active')"
            )
            conn.execute("insert into platform_accounts (tenant_id, telegram_user_id) values ('whieda', %s), ('whieda', 7002)", (HOST,))
            account_id = conn.execute("select account_id from platform_accounts where telegram_user_id = %s", (HOST,)).fetchone()[0]
            for key, (name, tg, mx) in PEOPLE.items():
                lead_id = conn.execute(
                    "insert into website_leads (tenant_id, name, contact, product_name, attributed_owner_id, assigned_owner_id, "
                    "idempotency_key, consent_version, lead_kind, telegram_user_id, max_user_id) "
                    "values ('whieda', %s, 'tel', 'Живая встреча', 'igor-actor', 'igor-actor', %s, 'v1', 'meeting', %s, %s) "
                    "returning lead_id",
                    (name, f"k-{key}", tg, mx),
                ).fetchone()[0]
                conn.execute(
                    "insert into crm_contacts (tenant_id, account_id, name, source, status, next_step, next_at, lead_id) "
                    "values ('whieda', %s, %s, 'встреча', 'deciding', 'ping', null, %s)",
                    (account_id, name, lead_id),
                )
            # Егор уже подтверждён раньше: актор с partner_whieda
            conn.execute(
                "insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id, telegram_user_id, bot_stage) "
                "values ('telegram:whieda:8804', 'whieda', 'Егор', '8804', 8804, 'partner_whieda')"
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.bot_stages import confirm, service
            from app.bot_stages import telegram as stages_tg
            from app.crm import service as crm
            from app.crm import stage_link
            from app.db import fetch_all, fetch_one, tenant_connection
            from app.telegram.bindings import BotBindingContext, binding_context_scope
            from app.telegram.update_parser import TelegramCallbackQuery
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id=TENANT_ID, status="active", display_name="WHIEDA", entitlements={"site_support": True})
            binding = BotBindingContext(
                binding_id="whieda-advisor-bot", tenant=tenant, bot_token_ref="env:T", webhook_secret_ref="env:S",
                bot_username="WHIEDA_Advisor_bot", status="active", processing_mode="core", bot_token="test-token",
                webhook_secret="test-secret",
            )
            sent: list[tuple[str, str]] = []
            max_sent: list[str] = []
            api_calls: list[tuple[str, dict]] = []

            async def send(*, chat_id, text, bot_token, reply_markup=None, **kwargs):
                sent.append((str(chat_id), text))
                return {"ok": True}

            async def call(method, payload, *, bot_token, timeout_sec=10.0):
                api_calls.append((method, payload))
                return {"ok": True}

            async def max_send(*, text, user_id=None, chat_id=None, buttons=None, **kwargs):
                max_sent.append(text)
                return {"ok": True}

            async def rows(query: str, params: tuple = ()) -> list[dict]:
                async with tenant_connection(TENANT_ID) as c:
                    return [dict(r) for r in await fetch_all(c, query, params)]

            async def card_of(name: str) -> str:
                (row,) = await rows("select contact_id::text as id from crm_contacts where name = %s", (name,))
                return row["id"]

            async def asks() -> dict[str, dict]:
                found = await rows(
                    "select idempotency_key, status, due_at <= now() as due, payload from platform_outbox "
                    "where event_type = 'bot_stage_confirm_asked' order by outbox_id"
                )
                return {row["idempotency_key"].split(":", 1)[1]: row for row in found}

            def pressed(user: int, data: str) -> TelegramCallbackQuery:
                return TelegramCallbackQuery(
                    chat_id=user, user_id=user, callback_query_id="cq", data=data, chat_type="private",
                    raw={"callback_query": {"from": {"id": user, "first_name": "Игорь", "username": "igor_v"},
                                            "message": {"message_id": 5, "chat": {"id": user, "type": "private"}}}},
                )

            async def answer(user: int, yes: bool, contact_id: str) -> dict | None:
                token = stage_link.card_token(contact_id)
                return await stages_tg.try_handle_stage_callback(
                    tenant, pressed(user, f"stg:{'y' if yes else 'n'}:{token}"), trace_id="t"
                )

            account = await crm.get_or_create_account(TENANT_ID, HOST)

            # --- перевод карточек на этап «Партнёр» настоящим PATCH ---
            ids = {key: await card_of(PEOPLE[key][0]) for key in PEOPLE}
            for key in PEOPLE:
                contact = await crm.update_contact(TENANT_ID, account, ids[key], {"status": "partner"})
                assert contact["status"] == "partner", key

            queued = await asks()
            # вопрос — только там, где у карточки есть человек в боте и он ещё не партнёр
            assert set(queued) == {ids["anna"], ids["clara"], ids["dina"]}
            ask = queued[ids["anna"]]
            assert ask["status"] == "scheduled" and ask["due"] is True
            payload = ask["payload"]
            assert payload["chat_id"] == str(HOST) and payload["binding_id"] == "whieda-advisor-bot"
            assert payload["text"] == "Анна Петрова стал партнёром WHIEDA — подтвердить? Он получит меню партнёра."
            token = stage_link.card_token(ids["anna"])
            assert payload["reply_markup"] == {"inline_keyboard": [[
                {"text": "Да", "callback_data": f"stg:y:{token}"}, {"text": "Нет", "callback_data": f"stg:n:{token}"},
            ]]}

            # повторный перевод карточки на «Партнёр» вопроса не дублирует
            await crm.update_contact(TENANT_ID, account, ids["anna"], {"status": "client"})
            await crm.update_contact(TENANT_ID, account, ids["anna"], {"status": "partner"})
            assert len(await asks()) == 3
            async with tenant_connection(TENANT_ID) as c:
                assert await stage_link.ask_host(c, TENANT_ID, ids["anna"]) == "duplicate"
                assert await stage_link.ask_host(c, TENANT_ID, ids["boris"]) == "no_bot"
                assert await stage_link.ask_host(c, TENANT_ID, ids["petr"]) == "already"   # партнёр WWC
                assert await stage_link.ask_host(c, TENANT_ID, ids["egor"]) == "already"   # партнёр WHIEDA

            # --- «Да» ---
            with patch("app.bot_stages.telegram.send_telegram_text", send), patch("app.bot_stages.confirm.send_telegram_text", send), \
                    patch("app.bot_stages.telegram._call_telegram", call), patch("app.bot_stages.confirm._call_telegram", call), \
                    patch("app.max.client.send_max_text", max_send), patch("app.bot_stages.telegram.answer_callback_query", AsyncMock()), \
                    binding_context_scope(binding):
                # чужой партнёр не отвечает за карточку
                stranger = await answer(7002, True, ids["anna"])
                assert stranger["status"] == "forbidden" and await service.person_stage(TENANT_ID, "telegram", 8800) == "guest"

                result = await answer(HOST, True, ids["anna"])
                assert result["status"] == "confirmed" and result["delivered"] is True
                assert await service.person_stage(TENANT_ID, "telegram", 8800) == "partner_whieda"
                (stored,) = await rows("select bot_stage from lead_actors where telegram_user_id = 8800")
                assert stored["bot_stage"] == "partner_whieda"
                to_person = [text for chat, text in sent if chat == "8800"]
                assert to_person == [
                    "Поздравляем — вы партнёр WHIEDA! Ниже ваше меню: сайт, курс, первые люди.",
                    "Вы партнёр WHIEDA. Следующий шаг — ваш сайт и первые люди.",
                ]
                assert (str(OWNER), "@igor_v подтвердил(а): Анна Петрова — партнёр WHIEDA.") in sent
                scopes = [payload["scope"]["chat_id"] for method, payload in api_calls if method == "setMyCommands"]
                assert scopes == [8800]   # синяя кнопка «Меню» человека переустановлена на меню партнёра WHIEDA

                # повторное «Да» идемпотентно: ничего человеку, ничего владельцу
                before = len(sent)
                again = await answer(HOST, True, ids["anna"])
                assert again["status"] == "already"
                assert [text for chat, text in sent[before:] if chat != str(HOST)] == []
                assert len([1 for chat, _ in sent if chat == "8800"]) == 2

                # --- «Нет» ---
                declined = await answer(HOST, False, ids["dina"])
                assert declined["status"] == "declined"
                assert await service.person_stage(TENANT_ID, "telegram", 8803) == "guest"
                assert not [1 for chat, _ in sent if chat == "8803"]

                # --- человек в Max ---
                max_result = await answer(HOST, True, ids["clara"])
                assert max_result["status"] == "confirmed"
                assert await service.person_stage(TENANT_ID, "max", 9900) == "partner_whieda"
                assert max_sent == [
                    "Поздравляем — вы партнёр WHIEDA! Ниже ваше меню: сайт, курс, первые люди.",
                    "Вы партнёр WHIEDA. Следующий шаг — ваш сайт и первые люди.",
                ]

                # карточка, которую ведущий уже вернул с «Партнёра», подтверждения не даёт
                await crm.update_contact(TENANT_ID, account, ids["zhanna"], {"status": "client"})
                moved = await answer(HOST, True, ids["zhanna"])
                assert moved["status"] == "card_moved"

                # --- человек открыл бота позже: карточка уже стоит на «Партнёр» ---
                await crm.update_contact(TENANT_ID, account, ids["zhanna"], {"status": "partner"})
                assert ids["zhanna"] not in await asks()                      # человека в боте ещё не было: вопроса нет
                assert await stage_link.check_partner_cards(TENANT_ID, "telegram", 8802) == 0
                async with tenant_connection(TENANT_ID) as c:                 # то, что делает mark_bot_opened
                    await c.execute(
                        "update website_leads set telegram_user_id = 8802, bot_opened_at = now() where name = 'Жанна Волкова'"
                    )
                assert await stage_link.check_partner_cards(TENANT_ID, "telegram", 8802) == 1
                assert ids["zhanna"] in await asks()
                assert await stage_link.check_partner_cards(TENANT_ID, "telegram", 8802) == 0   # второй раз — тот же вопрос
                assert await stage_link.check_partner_cards(TENANT_ID, "telegram", 12345) == 0  # чужой человек: ничего
                assert await stage_link.check_partner_cards(TENANT_ID, "max", 9900) == 0        # Клара уже партнёр

            # --- три меню выключены: вопроса нет ---
            os.environ["PLATFORM_BOT_STAGES"] = "false"
            get_settings.cache_clear()
            try:
                await crm.update_contact(TENANT_ID, account, ids["boris"], {"status": "client"})
                async with tenant_connection(TENANT_ID) as c:
                    await c.execute("update website_leads set telegram_user_id = 8805 where name = 'Борис Иванов'")
                await crm.update_contact(TENANT_ID, account, ids["boris"], {"status": "partner"})
                assert ids["boris"] not in await asks()
                assert await stage_link.check_partner_cards(TENANT_ID, "telegram", 8805) == 0
            finally:
                os.environ["PLATFORM_BOT_STAGES"] = "true"
                get_settings.cache_clear()

            # --- сбой в хуке карточку не ломает ---
            with patch("app.crm.stage_link.ask_host", AsyncMock(side_effect=RuntimeError("db down"))):
                await crm.update_contact(TENANT_ID, account, ids["egor"], {"status": "client"})
                updated = await crm.update_contact(TENANT_ID, account, ids["egor"], {"status": "partner"})
            assert updated["status"] == "partner"

        db.run_with_app(proof)
