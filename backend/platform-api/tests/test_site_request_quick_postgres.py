"""«Заказать сайт» без тупиков (V23, 08.10.2026) — шаг за шагом через бота.

Наталья (sabimama) застряла на «о себе»: на «Наталья» бот снова просил 2–3
предложения, голосовое не принимал, кнопки «дальше» не было. Теперь обязательны
только имя с фамилией и адрес; фото, текст и контакты — «Пропустить»; о себе
можно голосом. Настоящая база и вся цепочка обработчиков, Telegram подменён.
"""

from __future__ import annotations

import itertools
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

SITE_MIGRATIONS = (*MIGRATIONS, "platform_service_sales_v10.sql")
BINDING = "whieda-advisor-bot"
OWNER = 688931415
NATA = 71001
OLD = 71002

SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id) values
  ('nata', 'whieda', 'наташа', 71001, '71001'),
  ('old', 'whieda', 'Ирина', 71002, '71002');
"""


def _private(user: int, message_id: int, **body) -> dict:
    sender = {"id": user, "first_name": "Наталья"}
    return {"message": {"message_id": message_id, "chat": {"id": user, "type": "private"}, "from": sender, **body}}


def _callback(user: int, data: str) -> dict:
    return {"callback_query": {"id": f"cb-{data}", "data": data, "from": {"id": user, "first_name": "Наталья"},
                               "message": {"message_id": 5, "chat": {"id": user, "type": "private"}}}}


@pytest.mark.integration
def test_site_request_needs_only_name_and_address(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    with temporary_database("whieda_site_quick") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SITE_MIGRATIONS)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_one, tenant_connection
            from app.telegram.bindings import BotBindingContext
            from app.telegram.processor import process_core_telegram_update
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                                   entitlements={"structure_basic": True, "partner_leads": True, "site_support": True})
            binding = BotBindingContext(
                binding_id=BINDING, tenant=tenant, bot_token_ref="env:PROOF", webhook_secret_ref="env:PROOF",
                bot_username="WHIEDA_Advisor_bot", status="active", processing_mode="core",
                bot_token="proof-token", webhook_secret="proof-secret",
            )
            ids = itertools.count(2000)

            async def fake_send(**kwargs):
                return {"ok": True, "message_id": next(ids)}

            send = AsyncMock(side_effect=fake_send)
            copy = AsyncMock(side_effect=fake_send)
            quiet = {
                "app.telegram.processor.link_lead_actor_by_username": AsyncMock(return_value=None),
                "app.telegram.processor.fill_lead_actor_user_id": AsyncMock(return_value=None),
                "app.telegram.processor.handle_onboarding": AsyncMock(return_value=None),
                "app.telegram.processor.handle_navigation_text": AsyncMock(return_value=None),
                "app.telegram.processor.try_handle_academy_text": AsyncMock(return_value=None),
                "app.telegram.processor.try_handle_crm_text": AsyncMock(return_value=None),
                "app.telegram.processor.try_handle_wwc_service_text": AsyncMock(return_value=None),
                "app.telegram.processor.handle_advisor_query": AsyncMock(return_value={"ok": True, "route": "advisor"}),
                "app.telegram.site_requests.send_telegram_text": send,
                "app.telegram.site_requests.copy_telegram_message": copy,
                "app.telegram.site_requests.answer_callback_query": AsyncMock(return_value={"ok": True}),
                "app.telegram.support.send_telegram_text": send,
                "app.telegram.support.copy_telegram_message": copy,
                "app.telegram.support.set_message_reaction": AsyncMock(return_value={"ok": True}),
            }
            patches = [patch(target, mock) for target, mock in quiet.items()]
            for item in patches:
                item.start()

            async def step(update: dict, user: int = NATA) -> tuple[dict, list[dict], list[dict]]:
                send.reset_mock()
                copy.reset_mock()
                result = await process_core_telegram_update(tenant, update, "quick", binding=binding)
                to_user = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(user)]
                to_owner = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(OWNER)]
                return result or {}, to_user, to_owner

            async def request(actor: str) -> dict:
                async with tenant_connection("whieda") as conn:
                    return dict(await fetch_one(conn, "select * from partner_site_requests where actor_id = %s order by created_at desc limit 1", (actor,)))

            def skip_button(messages: list[dict]) -> bool:
                markup = (messages[-1].get("reply_markup") or {}).get("inline_keyboard") or []
                return [b["callback_data"] for row in markup for b in row] == ["site:skip"]

            try:
                # Страну не спрашиваем (09.10.2026): «Заказать сайт» — сразу имя.
                _, to_user, _ = await step(_callback(NATA, "site:create"))
                assert "имя и фамилию" in to_user[-1]["text"]

                # Одно слово — не имя для сайта; два — да, и сразу адрес.
                _, to_user, _ = await step(_private(NATA, 10, text="Наталья"))
                assert "имя и фамилию" in to_user[-1]["text"] and (await request("nata"))["status"] == "awaiting_name"
                _, to_user, to_owner = await step(_private(NATA, 11, text="Наталья   Шаби"))
                assert (await request("nata"))["partner_name"] == "Наталья Шаби"
                assert "адрес сайта" in to_user[-1]["text"] and "Имя: Наталья Шаби" in to_owner[0]["text"]

                await step(_private(NATA, 12, text="sabimama"))
                req = await request("nata")
                assert req["status"] == "awaiting_photo"

                # Фото пропускаем — и сразу «о себе», тоже с «Пропустить».
                result, to_user, to_owner = await step(_callback(NATA, "site:skip"))
                assert result["skipped"] == "фото" and (await request("nata"))["status"] == "awaiting_text"
                assert "голосовым" in to_user[-1]["text"] and skip_button(to_user)
                assert "фото — пропущено" in to_owner[0]["text"]

                # О себе голосом: голосовое копируется владельцу, анкета идёт к контактам.
                result, to_user, to_owner = await step(_private(NATA, 13, voice={"file_id": "voice-about", "duration": 20}))
                req = await request("nata")
                assert result["route"] == "site_request" and req["status"] == "awaiting_contacts"
                assert req["intro_voice_file_id"] == "voice-about" and req["intro_text"] is None
                assert copy.await_args_list and copy.await_args_list[0].kwargs["from_chat_id"] == str(NATA)
                assert "текст о себе — голосовое" in to_owner[0]["text"] and "расшифровать" in to_owner[0]["text"]
                assert skip_button(to_user)

                # Контакты пропускаем — дальше выбор пакета.
                result, to_user, _ = await step(_callback(NATA, "site:skip"))
                req = await request("nata")
                assert result["skipped"] == "контакты" and req["status"] == "awaiting_plan" and req["contacts_text"] is None
                assert to_user[-1]["text"] == "Что оформляем?"

                # Пропустить можно не всё: на выборе пакета кнопка ничего не ломает.
                _, to_user, _ = await step(_callback(NATA, "site:skip"))
                assert to_user[-1]["text"] == "Этот шаг пропустить нельзя."
                assert (await request("nata"))["status"] == "awaiting_plan"

                # Заявка, начатая до V23 (как у Натальи), — короткий текст принимается.
                async with tenant_connection("whieda") as conn:
                    await conn.execute(
                        "insert into partner_site_requests (tenant_id, actor_id, status, country_code, requested_subdomain, profile_photo_file_id) "
                        "values ('whieda', 'old', 'awaiting_text', 'RU', 'irina-old', 'ph')"
                    )
                _, to_user, _ = await step(_private(OLD, 20, text="Наталья"), user=OLD)
                assert (await request("old"))["status"] == "awaiting_contacts"
            finally:
                for item in patches:
                    item.stop()

        db.run_with_app(proof)
