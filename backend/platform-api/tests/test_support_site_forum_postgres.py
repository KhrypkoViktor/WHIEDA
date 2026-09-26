"""The «site» support forum on a real database (owner, 26.09.2026).

Two forums per bot (support_forums.kind), a partner with a Gemini ticket and a
«site» ticket open at the same time, the partner-site lookup for the topic
header, and the bot path end to end with Telegram delivery mocked: «поддержка»
→ a topic named after the partner in the «site» forum → relay both ways, while
a Gemini ticket in the administrator's forum still carries no name.
"""

from __future__ import annotations

import itertools
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

# support_tickets_v8, support_forum_v9 and support_site_forum_v16 are part of MIGRATIONS.
SITE_MIGRATIONS = (*MIGRATIONS, "platform_service_sales_v10.sql")
BINDING = "whieda-advisor-bot"
OWNER = 688931415
KARINA = 2101187096
OLGA = 60001
IVAN = 60002
SITE_FORUM = -1004000000001
SERVICES_FORUM = -1004392604070

SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id) values
  ('proof-olga', 'whieda', 'Ольга', 60001, '60001'),
  ('proof-ivan', 'whieda', 'Иван', 60002, '60002');
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, public_profile) values
  ('olga', 'whieda', 'proof-olga', 'named', '{"subdomain": "olga-site"}');
-- The administrator's forum registered before v16: no kind column back then.
insert into support_forums (tenant_id, binding_id, chat_id, title, registered_by_telegram_user_id)
values ('whieda', 'whieda-advisor-bot', -1004392604070, 'WWC_Карина', 688931415);
"""


@pytest.mark.integration
def test_forum_kinds_parallel_tickets_and_partner_site_lookup():
    with temporary_database("whieda_support_site") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SITE_MIGRATIONS, twice=True)
            conn.execute(SEED)
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "insert into support_forums (tenant_id, binding_id, kind, chat_id, registered_by_telegram_user_id) "
                    "values ('whieda', 'whieda-advisor-bot', 'other', 1, 1)"
                )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.support.service import (
                get_forum,
                get_open_ticket_for_user,
                list_open_tickets_for_admin,
                open_or_reuse_ticket,
                partner_site_for_telegram_user,
                register_forum,
                set_forum_service_threads,
            )

            # 1. The pre-v16 row is the «services» forum; there is no «site» forum yet.
            karina = await get_forum("whieda", binding_id=BINDING)
            assert karina["kind"] == "services" and int(karina["chat_id"]) == SERVICES_FORUM
            assert await get_forum("whieda", binding_id=BINDING, kind="site") is None

            # 2. «/forum site» adds a second row for the same bot; the later one wins;
            #    the administrator's forum is untouched, service topics stay with it.
            await register_forum("whieda", binding_id=BINDING, chat_id=SITE_FORUM, title="WWC сайты", registered_by=OWNER, kind="site")
            await register_forum("whieda", binding_id=BINDING, chat_id=SITE_FORUM - 1, title="WWC сайты 2", registered_by=OWNER, kind="site")
            site_forum = await get_forum("whieda", binding_id=BINDING, kind="site")
            assert site_forum["kind"] == "site" and int(site_forum["chat_id"]) == SITE_FORUM - 1
            assert int((await get_forum("whieda", binding_id=BINDING))["chat_id"]) == SERVICES_FORUM
            await set_forum_service_threads("whieda", binding_id=BINDING, bonuses_thread_id=5, reports_thread_id=6)
            assert (await get_forum("whieda", binding_id=BINDING))["reports_thread_id"] == 6
            assert (await get_forum("whieda", binding_id=BINDING, kind="site"))["reports_thread_id"] is None
            assert await get_forum("whieda", binding_id="wwc-cabinet-staging-bot", kind="site") is None

            # 3. One person: a «site» ticket for the owner and a Gemini ticket for the
            #    administrator open side by side; each admin lists only their kind.
            site_ticket = await open_or_reuse_ticket(
                "whieda", channel_code="site", offer_code=None, offer_title=None,
                user_telegram_user_id=OLGA, user_chat_id=OLGA, user_display="Ольга (@olga)", admin_telegram_user_id=OWNER,
            )
            gemini_ticket = await open_or_reuse_ticket(
                "whieda", channel_code="gemini", offer_code="gemini_6m", offer_title="Gemini Pro, 6 мес",
                user_telegram_user_id=OLGA, user_chat_id=OLGA, user_display="Ольга (@olga)", admin_telegram_user_id=KARINA,
            )
            assert site_ticket["created"] and gemini_ticket["created"] and site_ticket["ticket_id"] != gemini_ticket["ticket_id"]
            again = await open_or_reuse_ticket(
                "whieda", channel_code="site", offer_code=None, offer_title=None,
                user_telegram_user_id=OLGA, user_chat_id=OLGA, user_display="Ольга (@olga)", admin_telegram_user_id=OWNER,
            )
            assert again["created"] is False and again["ticket_id"] == site_ticket["ticket_id"]
            owner_site = await list_open_tickets_for_admin("whieda", admin_telegram_user_id=OWNER, forum_kind="site")
            assert [t["ticket_id"] for t in owner_site] == [site_ticket["ticket_id"]]
            assert await list_open_tickets_for_admin("whieda", admin_telegram_user_id=OWNER, forum_kind="services") == []
            karina_services = await list_open_tickets_for_admin("whieda", admin_telegram_user_id=KARINA, forum_kind="services")
            assert [t["ticket_id"] for t in karina_services] == [gemini_ticket["ticket_id"]]
            assert (await get_open_ticket_for_user("whieda", user_telegram_user_id=OLGA, channel_code="site"))["ticket_id"] == site_ticket["ticket_id"]

            # 4. The partner's site for the topic header comes from referral_profiles by owner.
            assert await partner_site_for_telegram_user("whieda", telegram_user_id=OLGA) == {"ref_code": "olga", "url": "https://olga-site.wwc.best/"}
            assert await partner_site_for_telegram_user("whieda", telegram_user_id=IVAN) is None

        db.run_with_app(proof)


def _private(text: str, *, user: int, first_name: str, username: str | None, message_id: int) -> dict:
    sender = {"id": user, "first_name": first_name}
    if username:
        sender["username"] = username
    return {"message": {"message_id": message_id, "text": text, "chat": {"id": user, "type": "private"}, "from": sender}}


def _group(text: str, *, chat: int, user: int, thread_id: int | None, message_id: int) -> dict:
    message = {
        "message_id": message_id, "text": text,
        "chat": {"id": chat, "type": "supergroup", "title": "группа", "is_forum": True},
        "from": {"id": user, "first_name": "Оператор", "is_bot": False},
    }
    if thread_id is not None:
        message["is_topic_message"] = True
        message["message_thread_id"] = thread_id
    return {"message": message}


def _callback(data: str, *, user: int, first_name: str) -> dict:
    return {
        "callback_query": {
            "id": "cb-1", "data": data, "from": {"id": user, "first_name": first_name},
            "message": {"message_id": 5, "chat": {"id": user, "type": "private"}},
        }
    }


@pytest.mark.integration
def test_support_button_opens_a_named_topic_and_relays_while_gemini_stays_anonymous(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLATFORM_SUPPORT_ADMIN_TELEGRAM_ID", str(KARINA))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    with temporary_database("whieda_support_site_bot") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SITE_MIGRATIONS)
            conn.execute(SEED)
            conn.execute("delete from support_forums")  # both forums get registered through the bot below
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.telegram.bindings import BotBindingContext
            from app.telegram.processor import process_core_telegram_update
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"structure_basic": True, "partner_leads": True})
            binding = BotBindingContext(
                binding_id=BINDING, tenant=tenant, bot_token_ref="env:PROOF", webhook_secret_ref="env:PROOF", bot_username="WHIEDA_Advisor_bot",
                status="active", processing_mode="core", bot_token="proof-token", webhook_secret="proof-secret",
            )
            ids = itertools.count(1000)

            async def fake_send(**kwargs):
                return {"ok": True, "message_id": next(ids)}

            send = AsyncMock(side_effect=fake_send)
            topics = AsyncMock(side_effect=[{"ok": True, "message_thread_id": 77}, {"ok": True, "message_thread_id": 78}])
            quiet = {
                "app.telegram.processor.link_lead_actor_by_username": AsyncMock(return_value=None),
                "app.telegram.processor.fill_lead_actor_user_id": AsyncMock(return_value=None),
                "app.telegram.processor.handle_onboarding": AsyncMock(return_value=None),
                "app.telegram.processor.handle_navigation_text": AsyncMock(return_value=None),
                "app.telegram.processor.try_handle_academy_text": AsyncMock(return_value=None),
                "app.telegram.processor.try_handle_crm_text": AsyncMock(return_value=None),
                "app.telegram.processor.try_handle_wwc_service_text": AsyncMock(return_value=None),
                "app.telegram.processor.handle_advisor_query": AsyncMock(return_value={"ok": True, "route": "advisor"}),
                "app.telegram.support.ensure_service_topics": AsyncMock(return_value=None),
                "app.telegram.support.set_message_reaction": AsyncMock(return_value={"ok": True}),
                "app.telegram.support.answer_callback_query": AsyncMock(return_value={"ok": True}),
            }
            patches = [patch(target, mock) for target, mock in quiet.items()]
            patches += [patch("app.telegram.support.send_telegram_text", send), patch("app.telegram.support.create_forum_topic", topics)]
            for item in patches:
                item.start()
            try:
                # The administrator registers her Gemini forum; the owner registers the «site» forum.
                services = await process_core_telegram_update(tenant, _group("/forum", chat=SERVICES_FORUM, user=KARINA, thread_id=None, message_id=1), "p1", binding=binding)
                site = await process_core_telegram_update(tenant, _group("/forum site", chat=SITE_FORUM, user=OWNER, thread_id=None, message_id=2), "p2", binding=binding)
                assert services["status"] == "registered" and services["kind"] == "services"
                assert site["status"] == "registered" and site["kind"] == "site"
                send.reset_mock()  # the two «подключена» confirmations are not part of the ticket flow

                # Olga presses «Поддержка»: a topic named after her, header with name and site.
                opened = await process_core_telegram_update(tenant, _private("поддержка", user=OLGA, first_name="Ольга", username="olga", message_id=11), "p3", binding=binding)
                assert opened["status"] == "ticket_opened" and opened["kind"] == "site"
                label = opened["ticket"]
                assert topics.await_args_list[0].kwargs["chat_id"] == str(SITE_FORUM)
                assert topics.await_args_list[0].kwargs["name"] == f"{label} · Ольга (@olga) · olga"
                header = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(SITE_FORUM)][0]
                assert header["message_thread_id"] == 77
                assert "Ольга (@olga)" in header["text"] and "https://olga-site.wwc.best/" in header["text"]
                invite = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(OLGA)][0]["text"]
                assert "WhatsApp" in invite and "ответ придёт в этот чат" in invite

                # Olga writes: into her topic, with her name; the owner answers in the topic: back to Olga.
                send.reset_mock()
                relayed = await process_core_telegram_update(tenant, _private("Поменяйте WhatsApp", user=OLGA, first_name="Ольга", username="olga", message_id=12), "p4", binding=binding)
                assert relayed["route"] == "support_relay" and relayed["direction"] == "user_to_admin"
                to_topic = send.await_args.kwargs
                assert to_topic["chat_id"] == str(SITE_FORUM) and to_topic["message_thread_id"] == 77
                assert to_topic["text"] == f"{label} · Ольга (@olga)\nПоменяйте WhatsApp"
                send.reset_mock()
                answered = await process_core_telegram_update(tenant, _group("Готово", chat=SITE_FORUM, user=OWNER, thread_id=77, message_id=300), "p5", binding=binding)
                assert answered["direction"] == "admin_to_user"
                assert send.await_args.kwargs["chat_id"] == str(OLGA)
                assert send.await_args.kwargs["text"] == f"Ответ команды WWC по обращению {label}:\nГотово"

                # Ivan asks Gemini support: a topic in the administrator's forum, no name anywhere.
                send.reset_mock()
                gemini = await process_core_telegram_update(tenant, _callback("svc:support:gemini", user=IVAN, first_name="Иван"), "p6", binding=binding)
                assert gemini["status"] == "ticket_opened" and gemini["kind"] == "services"
                assert topics.await_args_list[1].kwargs["chat_id"] == str(SERVICES_FORUM)
                assert topics.await_args_list[1].kwargs["name"] == f"{gemini['ticket']} · Вопрос по Gemini"
                to_karina = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(SERVICES_FORUM)]
                assert len(to_karina) == 1 and to_karina[0]["message_thread_id"] == 78
                assert to_karina[0]["text"].startswith("Клиент WWC · Заявка") and "Иван" not in to_karina[0]["text"]
                assert not [c for c in send.await_args_list if c.kwargs["chat_id"] in {str(SITE_FORUM), str(OWNER), str(KARINA)}]
            finally:
                for item in patches:
                    item.stop()

            # Stored state: both forums for one bot, the two tickets bound to their topics.
            from app.db import fetch_all, tenant_connection

            async with tenant_connection("whieda") as conn:
                forums = await fetch_all(conn, "select kind, chat_id from support_forums where binding_id = %s order by kind", (BINDING,))
                tickets = await fetch_all(conn, "select channel_code, admin_telegram_user_id, forum_chat_id, forum_thread_id, user_display from support_tickets order by ticket_no")
            assert [(f["kind"], int(f["chat_id"])) for f in forums] == [("services", SERVICES_FORUM), ("site", SITE_FORUM)]
            assert [(t["channel_code"], int(t["admin_telegram_user_id"]), int(t["forum_chat_id"]), int(t["forum_thread_id"])) for t in tickets] == [
                ("site", OWNER, SITE_FORUM, 77),
                ("gemini", KARINA, SERVICES_FORUM, 78),
            ]

        db.run_with_app(proof)
