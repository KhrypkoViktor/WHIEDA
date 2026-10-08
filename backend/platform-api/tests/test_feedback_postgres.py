"""«💡 Пожелание» для всех (V25, 08.10.2026) — шаг за шагом через бота, Telegram и Max."""

from __future__ import annotations

import itertools
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

SITE_MIGRATIONS = (*MIGRATIONS, "platform_service_sales_v10.sql")
BINDING = "whieda-advisor-bot"
OWNER = 688931415
OLGA = 72001
SITE_FORUM = -1004000000001
WISHES_THREAD = 90

SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id) values
  ('omango', 'whieda', 'Ольга', 72001, '72001');
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, enabled) values
  ('omango', 'whieda', 'omango', 'named', true);
"""


def _private(user: int, message_id: int, **body) -> dict:
    sender = {"id": user, "first_name": "Ольга", "last_name": "Манько", "username": "Olga_Manko08"}
    return {"message": {"message_id": message_id, "chat": {"id": user, "type": "private"}, "from": sender, **body}}


def _callback(user: int, data: str, *, chat: int | None = None, chat_type: str = "private", thread: int | None = None) -> dict:
    message = {"message_id": 5, "chat": {"id": chat or user, "type": chat_type}}
    if thread:
        message.update({"is_topic_message": True, "message_thread_id": thread})
    sender = {"id": user, "first_name": "Ольга", "last_name": "Манько", "username": "Olga_Manko08"} if user == OLGA else {"id": user, "first_name": "Виктор"}
    return {"callback_query": {"id": f"cb-{data}", "data": data, "from": sender, "message": message}}


def _group(user: int, message_id: int, thread: int | None, **body) -> dict:
    message = {"message_id": message_id, "chat": {"id": SITE_FORUM, "type": "supergroup", "title": "WWC Support", "is_forum": True},
               "from": {"id": user, "first_name": "Виктор", "is_bot": False}, **body}
    if thread:
        message.update({"is_topic_message": True, "message_thread_id": thread})
    return {"message": message}


@pytest.mark.integration
def test_anyone_leaves_a_wish_and_hears_back(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    with temporary_database("whieda_feedback") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SITE_MIGRATIONS)
            conn.execute(SEED)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, tenant_connection
            from app.max.processor import process_max_event
            from app.max.update_parser import parse_max_update
            from app.telegram.bindings import BotBindingContext
            from app.telegram.processor import process_core_telegram_update
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                                   entitlements={"structure_basic": True, "partner_leads": True, "site_support": True})
            other = TenantContext(tenant_id="whieda", status="active", display_name="NSP", entitlements={"structure_basic": True})
            binding = BotBindingContext(
                binding_id=BINDING, tenant=tenant, bot_token_ref="env:PROOF", webhook_secret_ref="env:PROOF",
                bot_username="WHIEDA_Advisor_bot", status="active", processing_mode="core",
                bot_token="proof-token", webhook_secret="proof-secret",
            )
            ids = itertools.count(3000)

            async def fake_send(**kwargs):
                return {"ok": True, "message_id": next(ids)}

            send = AsyncMock(side_effect=fake_send)
            copy = AsyncMock(side_effect=fake_send)
            max_send = AsyncMock(return_value={"ok": True})
            advisor = AsyncMock(return_value={"ok": True, "route": "advisor"})
            quiet = {
                "app.telegram.processor.link_lead_actor_by_username": AsyncMock(return_value=None),
                "app.telegram.processor.fill_lead_actor_user_id": AsyncMock(return_value=None),
                "app.telegram.processor.handle_onboarding": AsyncMock(return_value=None),
                "app.telegram.processor.handle_navigation_text": AsyncMock(return_value=None),
                "app.telegram.processor.try_handle_academy_text": AsyncMock(return_value=None),
                "app.telegram.processor.try_handle_crm_text": AsyncMock(return_value=None),
                "app.telegram.processor.try_handle_wwc_service_text": AsyncMock(return_value=None),
                "app.telegram.processor.handle_advisor_query": advisor,
                "app.telegram.support.send_telegram_text": send,
                "app.telegram.support.ensure_service_topics": AsyncMock(return_value=None),
                "app.telegram.feedback.send_telegram_text": send,
                "app.telegram.feedback.copy_telegram_message": copy,
                "app.telegram.feedback.create_forum_topic": AsyncMock(return_value={"ok": True, "message_thread_id": WISHES_THREAD}),
                "app.telegram.feedback.answer_callback_query": AsyncMock(return_value={"ok": True}),
                "app.max.processor.send_max_text": max_send,
                "app.max.client.send_max_text": max_send,
                "app.max.processor.handle_structured_query": AsyncMock(return_value={"answer_mode": "none"}),
                "app.telegram.bindings.resolve_bot_binding_context": AsyncMock(return_value=binding),
            }
            patches = [patch(target, mock) for target, mock in quiet.items()]
            for item in patches:
                item.start()

            async def step(update: dict, *, as_tenant: TenantContext = tenant) -> dict:
                send.reset_mock()
                copy.reset_mock()
                return await process_core_telegram_update(as_tenant, update, "wish", binding=binding) or {}

            def texts(chat: int) -> list[str]:
                return [c.kwargs["text"] for c in send.await_args_list if c.kwargs["chat_id"] == str(chat)]

            try:
                await step(_group(OWNER, 1, None, text="/forum site"))

                # 1. Кнопка «💡 Пожелание» → вопрос; следующий текст — пожелание в тему «Пожелания».
                started = await step(_callback(OLGA, "wish:start"))
                assert started["status"] == "awaiting" and "Что улучшить в WWC" in texts(OLGA)[0]
                wish = await step(_private(OLGA, 10, text="Визитки на выбранном языке, картинки из европейского каталога"))
                assert wish["route"] == "wish" and wish["no"] == 1
                post = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(SITE_FORUM)][0]
                assert post["message_thread_id"] == WISHES_THREAD
                assert post["text"].startswith("💡 Пожелание №1 — Ольга Манько (@Olga_Manko08) · omango")
                assert [b["text"] for b in post["reply_markup"]["inline_keyboard"][0]] == ["В работу", "Сделано", "Не будем"]
                assert texts(OLGA) == ["Спасибо! Пожелание №1 записали и передали команде WWC. Сообщим, когда сделаем."]
                done_button = post["reply_markup"]["inline_keyboard"][0][1]["callback_data"]

                # 2. Пожелание голосом — копия голосового в тему.
                await step(_private(OLGA, 11, text="пожелание"))
                voiced = await step(_private(OLGA, 12, voice={"file_id": "v-1", "duration": 9}))
                assert voiced["no"] == 2 and copy.await_args.kwargs["chat_id"] == str(SITE_FORUM)

                # 3. Без «Пожелание» обычный текст — не пожелание.
                plain = await step(_private(OLGA, 13, text="сколько стоят стельки"))
                assert plain.get("route") != "wish"

                # 4. Reply владельца на пост — человеку; «Сделано» — человек слышит «сделали».
                post_id = int((await fetch_rows(tenant_connection, "select forum_message_id from partner_feedback where feedback_no = 1"))[0]["forum_message_id"])
                replied = await step(_group(OWNER, 300, WISHES_THREAD, text="Записали, сделаем к ноябрю", reply_to_message={"message_id": post_id, "from": {"is_bot": True}}))
                assert replied["route"] == "wish_reply"
                assert texts(OLGA) == ["Ответ команды WWC на ваше пожелание №1:\nЗаписали, сделаем к ноябрю"]
                decided = await step(_callback(OWNER, done_button, chat=SITE_FORUM, chat_type="supergroup", thread=WISHES_THREAD))
                assert decided["status"] == "done" and decided["changed"] is True
                assert texts(OLGA)[0].startswith("Ваше пожелание №1 «Визитки на выбранном языке") and "сделали" in texts(OLGA)[0]
                again = await step(_callback(OWNER, done_button, chat=SITE_FORUM, chat_type="supergroup", thread=WISHES_THREAD))
                assert again["changed"] is False and not texts(OLGA)
                stranger = await step(_callback(OLGA, done_button.replace("done", "no"), chat=SITE_FORUM, chat_type="supergroup", thread=WISHES_THREAD))
                assert stranger["status"] == "forbidden"

                # 5. Max: то же словом «пожелание» — пост в Telegram с пометкой «Max».
                start = parse_max_update({"update_type": "message_created", "message": {"sender": {"user_id": 555, "name": "Ирина"},
                                          "recipient": {"chat_id": 777, "chat_type": "dialog"}, "body": {"text": "Пожелание"}}})
                await process_max_event(tenant, start, "m")
                assert "Что улучшить" in max_send.await_args.kwargs["text"]
                send.reset_mock()
                text = parse_max_update({"update_type": "message_created", "message": {"sender": {"user_id": 555, "name": "Ирина"},
                                         "recipient": {"chat_id": 777, "chat_type": "dialog"}, "body": {"text": "Заявки с сайта — в Max"}}})
                result = await process_max_event(tenant, text, "m")
                assert result["route"] == "max_wish" and result["no"] == 3
                assert " · Max" in [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(SITE_FORUM)][0]["text"]
                assert max_send.await_args.kwargs["text"].startswith("Спасибо! Пожелание №3")

                # 6. Бот чужого тенанта (без site_support): «пожелание» — обычный вопрос советнику.
                advisor.reset_mock()
                foreign = await step(_private(OLGA, 20, text="пожелание"), as_tenant=other)
                assert foreign.get("route") != "wish" and advisor.await_count == 1
            finally:
                for item in patches:
                    item.stop()

            rows = await fetch_rows(tenant_connection, "select feedback_no, channel, status, ref_code, media_kind from partner_feedback order by feedback_no")
            assert [(int(r["feedback_no"]), r["channel"], r["status"]) for r in rows] == [
                (1, "telegram", "done"), (2, "telegram", "new"), (3, "max", "new"),
            ]
            assert rows[1]["media_kind"] == "voice" and rows[0]["ref_code"] == "omango"

        async def fetch_rows(tenant_connection, sql: str) -> list[dict]:
            from app.db import fetch_all

            async with tenant_connection("whieda") as conn:
                return [dict(r) for r in await fetch_all(conn, sql)]

        db.run_with_app(proof)
