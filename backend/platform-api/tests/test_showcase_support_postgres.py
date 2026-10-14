"""Обращения в боте витрины (14.10.2026) на настоящем PostgreSQL.

  * «Написать художнику» открывает обращение автору витрины в личку (режим Reply), текст и
    фото ученицы уходят ей, её Reply — ученице; бот WHIEDA это обращение не видит;
  * «/forum» администратора подключает группу с темами, «Написать администратору» — тема
    на обращение, ответ в теме — ученице, «закрыть» закрывает обращение и тему;
  * чужому закрывать нельзя; без адресата бот отсылает в студию.
"""

from __future__ import annotations

import logging

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

# Колонки тем «Бонусы»/«Отчёты» у support_forums — из миграции продаж сервисов, её нет в базовом наборе.
SUPPORT_MIGRATIONS = (*MIGRATIONS, "platform_service_sales_v10.sql")

V33 = "platform_academy_showcase_support_v33.sql"
VIKTOR, SASHA, KIRILL, STUDENT, STRANGER = 2, 7001, 7002, 9001, 9002
GROUP = -1001234567890

SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) values
  ('viktor', 'whieda', 'Виктор', '2'),
  ('almira', 'whieda', 'Александра Пармон', '7001');
update lead_actors set telegram_user_id = 2 where actor_id = 'viktor';
update lead_actors set telegram_user_id = 7001 where actor_id = 'almira';

alter table tenant_bot_bindings
  add column if not exists bot_token_ref text,
  add column if not exists bot_username text;
insert into tenant_bot_bindings (binding_id, tenant_id, webhook_secret_ref, bot_token_ref, bot_username, status)
values ('almira-academy-bot', 'whieda', 'env:ALMIRA_SECRET', 'env:ALMIRA_TOKEN', 'ALMIRA_I_bot', 'active');

insert into academy_showcases (tenant_id, showcase_id, hosts, owner_actor_id, brand_name, academy_title,
                               links, telegram_binding_id, more_mode, support)
values ('whieda', 'almira', array['academy.almira.by'], 'almira', 'ALMIRA', 'Онлайн-академия ALMIRA',
        '{"home_url": "https://almira.by/", "contact_url": "https://t.me/alm_school"}',
        'almira-academy-bot', 'none', '{"admin_telegram_user_id": 7002}');
"""


def _env(monkeypatch) -> None:
    monkeypatch.setenv("PLATFORM_ACADEMY_OPEN", "true")
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", str(VIKTOR))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(VIKTOR))
    monkeypatch.setenv("PLATFORM_TELEGRAM_BOT_USERNAME", "WHIEDA_Advisor_bot")
    monkeypatch.setenv("PLATFORM_ACADEMY_SITE_BASE", "https://wwc.best")


def _user(user_id: int, name: str, username: str | None) -> dict:
    return {"id": user_id, "is_bot": False, "first_name": name, **({"username": username} if username else {})}


def private(user_id: int, text: str, *, message_id: int, name: str = "Ученик", username: str | None = None,
            reply_to: int | None = None, photo: bool = False) -> dict:
    message = {
        "message_id": message_id, "from": _user(user_id, name, username),
        "chat": {"id": user_id, "type": "private"},
    }
    if photo:
        message["photo"] = [{"file_id": f"photo-{message_id}"}]
        if text:
            message["caption"] = text
    else:
        message["text"] = text
    if reply_to is not None:
        message["reply_to_message"] = {"message_id": reply_to}
    return {"update_id": message_id, "message": message}


def group(user_id: int, text: str, *, message_id: int, thread_id: int | None = None, name: str = "Кирилл") -> dict:
    message = {
        "message_id": message_id, "from": _user(user_id, name, "kirilkvk"), "text": text,
        "chat": {"id": GROUP, "type": "supergroup", "is_forum": True, "title": "ALMIRA · Обращения"},
    }
    if thread_id is not None:
        message["is_topic_message"] = True
        message["message_thread_id"] = thread_id
    return {"update_id": message_id, "message": message}


def callback(user_id: int, data: str, *, chat_id: int, chat_type: str = "private", thread_id: int | None = None,
             name: str = "x", username: str | None = None) -> dict:
    message = {"message_id": 1, "chat": {"id": chat_id, "type": chat_type}}
    if thread_id is not None:
        message["is_topic_message"] = True
        message["message_thread_id"] = thread_id
    return {"update_id": 1, "callback_query": {"id": "cb", "data": data, "from": _user(user_id, name, username), "message": message}}


@pytest.mark.integration
def test_showcase_support_tunnel_author_private_and_admin_forum(monkeypatch):
    _env(monkeypatch)
    # На бою INFO включён: служебные поля в extra (created, message…) роняют logger.info — ловим здесь.
    logging.getLogger("app.telegram.showcase_support").setLevel(logging.INFO)
    with temporary_database("whieda_showcase_support") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SUPPORT_MIGRATIONS)
            conn.execute(SEED)
            db.grant_api_role(conn)

        sent: list[dict] = []
        copied: list[dict] = []
        topics: list[dict] = []
        closed_topics: list[dict] = []
        counter = {"id": 100}

        async def fake_send(*, chat_id, text, bot_token, reply_markup=None, message_thread_id=None, **_):
            counter["id"] += 1
            sent.append({"chat_id": int(chat_id), "text": text, "thread_id": message_thread_id, "markup": reply_markup, "message_id": counter["id"]})
            return {"ok": True, "message_id": counter["id"]}

        async def fake_copy(*, chat_id, from_chat_id, message_id, bot_token, message_thread_id=None, **_):
            copied.append({"chat_id": int(chat_id), "from": int(from_chat_id), "message_id": int(message_id), "thread_id": message_thread_id})
            return {"ok": True, "message_id": 1}

        async def fake_topic(*, chat_id, name, bot_token):
            topics.append({"chat_id": int(chat_id), "name": name})
            return {"ok": True, "message_thread_id": 77}

        async def fake_edit_topic(*, chat_id, message_thread_id, name, bot_token):
            topics.append({"chat_id": int(chat_id), "name": name, "edited": message_thread_id})
            return {"ok": True}

        async def fake_close_topic(*, chat_id, message_thread_id, bot_token):
            closed_topics.append({"chat_id": int(chat_id), "thread_id": message_thread_id})
            return {"ok": True}

        async def ok(**_):
            return {"ok": True}

        from app.telegram import showcase_support as ss

        monkeypatch.setattr(ss, "send_telegram_text", fake_send)
        monkeypatch.setattr(ss, "copy_telegram_message", fake_copy)
        monkeypatch.setattr(ss, "create_forum_topic", fake_topic)
        monkeypatch.setattr(ss, "edit_forum_topic", fake_edit_topic)
        monkeypatch.setattr(ss, "close_forum_topic", fake_close_topic)
        monkeypatch.setattr(ss, "answer_callback_query", ok)
        monkeypatch.setattr(ss, "set_message_reaction", ok)

        def to(chat_id: int) -> list[str]:
            return [row["text"] for row in sent if row["chat_id"] == chat_id]

        async def proof() -> None:
            from app.academy import showcases as sc
            from app.support.service import get_open_ticket_for_user
            from app.telegram.bindings import BotBindingContext, binding_context_scope
            from app.telegram.showcase_bot import process_showcase_bot_update
            from app.tenancy import TenantContext

            sc._reset_cache()
            showcase = await sc.showcase_for_host("whieda", "academy.almira.by")
            assert showcase.support == {"admin_telegram_user_id": KIRILL}
            assert await ss.support_targets("whieda", showcase) == {"author": SASHA, "admin": KIRILL}
            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})
            binding = BotBindingContext(
                binding_id="almira-academy-bot", tenant=tenant, bot_token_ref="env:X", webhook_secret_ref="env:Y",
                bot_username="ALMIRA_I_bot", status="active", processing_mode="core", bot_token="t", webhook_secret="s",
                showcase_id="almira",
            )

            async def bot(update: dict) -> dict:
                with binding_context_scope(binding):
                    return await process_showcase_bot_update(tenant, update, "trace", showcase)

            # 1. Художнику: обращение открыто, шапка — Саше в личку с именем и кнопкой «Закрыть».
            result = await bot(private(STUDENT, "Написать художнику", message_id=1, name="Мария", username="masha"))
            assert (result["route"], result["status"], result["kind"]) == ("showcase_support", "ticket_opened", "author")
            header = [row for row in sent if row["chat_id"] == SASHA][-1]
            assert header["text"].startswith("#A-1 · Мария (@masha) · художнику")
            assert header["markup"]["inline_keyboard"][0][0]["callback_data"].startswith("svc:close:")
            assert to(STUDENT)[-1].startswith("Обращение #A-1 открыто")
            # Бот WHIEDA это обращение не видит.
            assert await get_open_ticket_for_user("whieda", user_telegram_user_id=STUDENT) is None

            # 2. Текст и фото ученицы — Саше; команда Академии по-прежнему не уходит в обращение.
            relayed = await bot(private(STUDENT, "А можно гуашью?", message_id=2, name="Мария", username="masha"))
            assert relayed["route"] == "showcase_support_relay" and relayed["direction"] == "user_to_admin"
            assert to(SASHA)[-1] == "#A-1 · Мария (@masha)\nА можно гуашью?"
            await bot(private(STUDENT, "", message_id=3, name="Мария", username="masha", photo=True))
            assert copied[-1] == {"chat_id": SASHA, "from": STUDENT, "message_id": 3, "thread_id": None}
            assert "(вложение выше)" in to(SASHA)[-1]

            # 3. Саша отвечает через Reply на шапку — ученице; плоский ответ при одном открытом — тоже ей.
            answered = await bot(private(SASHA, "Можно, но тоном", message_id=4, name="Александра", reply_to=header["message_id"]))
            assert answered["direction"] == "admin_to_user"
            assert to(STUDENT)[-1] == "Ответ художника по обращению #A-1:\nМожно, но тоном"
            await bot(private(SASHA, "И бумагу плотнее", message_id=5, name="Александра"))
            assert to(STUDENT)[-1] == "Ответ художника по обращению #A-1:\nИ бумагу плотнее"
            assert to(SASHA)[-1] == "→ отправлено: #A-1"

            # 4. Чужой закрыть не может; Саша закрывает кнопкой — ученице сообщение.
            denied = await bot(callback(STRANGER, header["markup"]["inline_keyboard"][0][0]["callback_data"], chat_id=STRANGER))
            assert denied["status"] == "forbidden"
            done = await bot(callback(SASHA, header["markup"]["inline_keyboard"][0][0]["callback_data"], chat_id=SASHA))
            assert done["status"] == "closed"
            assert to(STUDENT)[-1].startswith("Обращение #A-1 закрыто")

            # 5. Открыты оба обращения: сообщение — в то, что открыто последним (не «сначала художнику»).
            await bot(private(STUDENT, "Написать художнику", message_id=6, name="Мария", username="masha"))
            opened = await bot(callback(STUDENT, "shs:open:admin", chat_id=STUDENT, name="Мария", username="masha"))
            assert (opened["status"], opened["kind"]) == ("ticket_opened", "admin")
            await bot(private(STUDENT, "Вопрос про оплату", message_id=7, name="Мария", username="masha"))
            assert to(KIRILL)[-1].endswith("Вопрос про оплату")
            assert not to(SASHA)[-1].endswith("Вопрос про оплату")
            await bot(private(STUDENT, "Написать художнику", message_id=8, name="Мария", username="masha"))
            await bot(private(STUDENT, "А это художнику", message_id=9, name="Мария", username="masha"))
            assert to(SASHA)[-1].endswith("А это художнику")

            # 6. «/forum» подключает группу (чужой — нет) и переносит открытое обращение администратору в тему.
            assert (await bot(group(STRANGER, "/forum", message_id=10)))["status"] == "forbidden"
            registered = await bot(group(KIRILL, "/forum", message_id=11))
            assert (registered["status"], registered["kind"]) == ("registered", "services")
            assert topics[-1] == {"chat_id": GROUP, "name": "#A-3 · Мария (@masha) · администратору"}
            topic_header = [row for row in sent if row["chat_id"] == GROUP and row["thread_id"] == 77][0]
            assert topic_header["text"].startswith("#A-3")
            assert "перенесены в темы: 1" in [row for row in sent if row["chat_id"] == GROUP][-1]["text"]
            await bot(private(STUDENT, "Написать администратору", message_id=12, name="Мария", username="masha"))
            await bot(private(STUDENT, "Как оплатить?", message_id=16, name="Мария", username="masha"))
            assert [row for row in sent if row["chat_id"] == GROUP][-1]["text"].endswith("Как оплатить?")

            # 7. Ответ Кирилла в теме — ученице; «закрыть» в теме закрывает обращение и тему.
            reply = await bot(group(KIRILL, "Через ЕРИП, пришлю ссылку", message_id=13, thread_id=77))
            assert reply["direction"] == "admin_to_user"
            assert to(STUDENT)[-1] == "Ответ администратора по обращению #A-3:\nЧерез ЕРИП, пришлю ссылку"
            closed = await bot(group(KIRILL, "закрыть", message_id=14, thread_id=77))
            assert closed["status"] == "closed"
            assert closed_topics == [{"chat_id": GROUP, "thread_id": 77}]
            assert topics[-1]["name"].startswith("✅ #A-3")
            assert to(STUDENT)[-1].startswith("Обращение #A-3 закрыто")
            # Закрытая тема: ответ туда больше не уходит ученице.
            late = await bot(group(KIRILL, "ещё", message_id=15, thread_id=77))
            assert late["status"] == "closed"

            # 8. Без адресата (витрина без администратора) — отсылка в студию.
            bare = sc.Showcase(**{**showcase.__dict__, "support": {}, "owner_actor_id": None})
            with binding_context_scope(binding):
                none = await process_showcase_bot_update(tenant, callback(STUDENT, "shs:open:author", chat_id=STUDENT), "trace", bare)
            assert none["status"] == "no_target"
            assert "https://t.me/alm_school" in to(STUDENT)[-1]

        db.run_with_app(proof)
