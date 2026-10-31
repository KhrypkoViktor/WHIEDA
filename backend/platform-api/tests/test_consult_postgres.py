"""Личный разбор в настоящем PostgreSQL (V47, роль API без bypassrls).

1. V47 идемпотентна (дважды), знака доллара нет, зарегистрирована во всех списках после V46;
2. таблица: колонки, ограничения (канал, ведущий, статус), индексы, RLS по арендатору;
3. сквозной сценарий через кнопки Telegram и Max: гость с заявкой и карточкой CRM → два вопроса → Елена → заявка,
   сообщения Елене и копия Виктору в platform_outbox, метка/заметка/шаг карточки; повтор за 7 дней; другой ведущий;
   давняя, выполненная и отменённая заявки не мешают; человек в Max без заявки; двойное нажатие одновременно;
   стенд без бота плановых сообщений; ведущая без чата; воркер забирает эти сообщения.

Telegram и Max не вызываются: отправка — мок.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, SQL_DIR, temporary_database
from tests.test_crm_postgres import LEAD_TABLES, SEED, _binding
from tests.test_meeting_registrations_postgres import _body, _env

V44 = "platform_website_leads_meeting_v44.sql"
V46 = "platform_meeting_registrations_v46.sql"
V47 = "platform_consult_requests_v47.sql"
ROOT = Path(__file__).resolve().parents[3]

COLUMNS = {
    "request_id": "bigint",
    "tenant_id": "text",
    "lead_id": "uuid",
    "channel": "text",
    "channel_user_id": "bigint",
    "display_name": "text",
    "host": "text",
    "answers": "jsonb",
    "status": "text",
    "created_at": "timestamp with time zone",
    "updated_at": "timestamp with time zone",
    "notified_at": "timestamp with time zone",
}


def test_v47_has_no_dollar_sign_a_lock_timeout_and_is_registered_everywhere_once():
    text = (SQL_DIR / V47).read_text(encoding="utf-8")
    assert "$" not in text  # боевые правки идут из контейнера и через n8n: знак доллара режется
    assert "set local lock_timeout" in text
    for relative in (
        "postgres/scripts/apply_staging_platform_all.ps1",
        "postgres/scripts/staging_proof_lib.py",
        "backend/platform-api/tests/postgres_testkit.py",
    ):
        assert (ROOT / relative).read_text(encoding="utf-8").count(V47) == 1, relative
    schema = (ROOT / "backend/platform-api/app/schema_requirements.py").read_text(encoding="utf-8")
    assert '"consult_requests"' in schema


def test_v47_comes_right_after_v46_in_every_list():
    for relative in (
        "postgres/scripts/apply_staging_platform_all.ps1",
        "postgres/scripts/staging_proof_lib.py",
        "backend/platform-api/tests/postgres_testkit.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert text.index(V46) < text.index(V47), relative


@pytest.mark.integration
def test_consult_requests_end_to_end(monkeypatch):
    _env(monkeypatch)
    from app.settings import get_settings

    get_settings.cache_clear()

    with temporary_database("whieda_consult") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, MIGRATIONS, twice=True)  # V47 дважды подряд
            columns = dict(
                conn.execute(
                    "select column_name, data_type from information_schema.columns where table_name = 'consult_requests'"
                ).fetchall()
            )
            assert columns == COLUMNS
            nullable = dict(
                conn.execute(
                    "select column_name, is_nullable from information_schema.columns where table_name = 'consult_requests'"
                ).fetchall()
            )
            assert {k for k, v in nullable.items() if v == "NO"} == {
                "request_id", "tenant_id", "channel", "channel_user_id", "host", "answers", "status", "created_at", "updated_at",
            }
            defaults = {
                row[0]: row[1]
                for row in conn.execute(
                    "select column_name, column_default from information_schema.columns "
                    "where table_name = 'consult_requests' and column_name in ('status', 'answers')"
                ).fetchall()
            }
            assert "'new'" in defaults["status"] and "'{}'" in defaults["answers"]
            indexes = {
                row[0]: row[1]
                for row in conn.execute("select indexname, indexdef from pg_indexes where tablename = 'consult_requests'").fetchall()
            }
            assert {"idx_consult_requests_status", "idx_consult_requests_person"} <= set(indexes)
            assert "(tenant_id, status, created_at)" in indexes["idx_consult_requests_status"]
            assert "(tenant_id, channel, channel_user_id, host, created_at)" in indexes["idx_consult_requests_person"]
            assert conn.execute("select relrowsecurity from pg_class where relname = 'consult_requests'").fetchone() == (True,)
            assert conn.execute("select count(*) from pg_policies where tablename = 'consult_requests'").fetchone() == (1,)

            conn.execute(LEAD_TABLES)
            conn.execute("alter table website_leads add column deleted_at timestamptz")  # есть на бою
            conn.execute((SQL_DIR / V44).read_text(encoding="utf-8"))  # заявка на встречу: телефон, мессенджер, bot_opened_at
            conn.execute(SEED)
            conn.execute(
                "insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) values "
                "('viktor', 'whieda', 'Виктор', '1001'), ('onlineelena', 'whieda', 'Елена', '2002')"
            )
            conn.execute("update lead_actors set telegram_user_id = 1001 where actor_id = 'viktor'")
            conn.execute("update lead_actors set telegram_user_id = 2002 where actor_id = 'onlineelena'")
            conn.execute(
                "insert into tenant_bot_bindings (binding_id, tenant_id, webhook_secret_ref, status) values "
                "('whieda-advisor-bot', 'whieda', 'env:X', 'active')"
            )
            # ограничения таблицы: администратор обходит RLS, но не CHECK и не NOT NULL
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("insert into consult_requests (tenant_id, channel, channel_user_id, host) values ('whieda', 'whatsapp', 1, 'viktor')")
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("insert into consult_requests (tenant_id, channel, channel_user_id, host) values ('whieda', 'telegram', 1, 'anna')")
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "insert into consult_requests (tenant_id, channel, channel_user_id, host, status) "
                    "values ('whieda', 'telegram', 1, 'viktor', 'bogus')"
                )
            with pytest.raises(psycopg.errors.NotNullViolation):
                conn.execute("insert into consult_requests (tenant_id, channel, host) values ('whieda', 'max', 'elena')")
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.crm import service as crm
            from app.db import fetch_all, tenant_connection
            from app.funnel import consult, system_word
            from app.jobs import worker
            from app.leads.meeting_leads import mark_bot_opened
            from app.leads.service import parse_lead_body, save_lead
            from app.max import processor as max_processor
            from app.max.update_parser import MaxCallbackEvent
            from app.telegram.bindings import binding_context_scope
            from app.telegram.update_parser import TelegramCallbackQuery
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})

            async def rows(query: str, params: tuple = (), tenant_id: str = "whieda") -> list[dict]:
                async with tenant_connection(tenant_id) as conn:
                    return [dict(r) for r in await fetch_all(conn, query, params)]

            async def requests_of(user: int, channel: str = "telegram") -> list[dict]:
                return await rows(
                    "select request_id, lead_id::text as lead_id, channel, channel_user_id, display_name, host, answers, status, "
                    "notified_at is not null as notified from consult_requests where channel = %s and channel_user_id = %s order by request_id",
                    (channel, user),
                )

            async def outbox() -> list[dict]:
                return await rows(
                    "select idempotency_key, event_type, status, due_at <= now() as due, payload from platform_outbox "
                    "where idempotency_key like 'consult:%%' order by outbox_id"
                )

            sent = AsyncMock(return_value={"ok": True})
            max_sent = AsyncMock(return_value={"ok": True})

            def tg_callback(user: int, data: str) -> TelegramCallbackQuery:
                from_user = {"id": user, "first_name": "Анна", "last_name": "Петрова", "username": "anna_petrova"}
                return TelegramCallbackQuery(
                    chat_id=user, user_id=user, callback_query_id="cq", data=data, chat_type="private",
                    raw={"callback_query": {"from": from_user, "message": {"message_id": 3}}},
                )

            def max_callback(user: int, payload: str) -> MaxCallbackEvent:
                return MaxCallbackEvent(
                    callback_id="cb", payload=payload, user_id=user, chat_id=888, message_id="mid", chat_type="dialog",
                    username=None, display_name="Инна Орлова",
                    raw={"callback": {"user": {"user_id": user, "first_name": "Инна", "last_name": "Орлова"}}},
                )

            async def press(user: int, data: str):
                return await system_word.try_handle_funnel_callback(tenant, tg_callback(user, data), trace_id="t")

            async def press_max(user: int, payload: str):
                return await max_processor.process_max_event(tenant, max_callback(user, payload), "t")

            async def set_request(request_id: int, **fields: str) -> None:
                async with tenant_connection("whieda") as conn:
                    for column, value in fields.items():
                        await conn.execute(f"update consult_requests set {column} = {value} where request_id = %s", (request_id,))

            monkeypatch.setattr(max_processor, "answer_max_callback", AsyncMock(return_value={"ok": True}))
            with binding_context_scope(_binding()), patch.object(system_word, "send_telegram_text", sent), \
                    patch.object(system_word, "answer_callback_query", AsyncMock()), patch.object(system_word, "send_max_text", max_sent):

                # ---- 1. гость с заявкой на встречу и карточкой CRM: вся цепочка кнопок ----------------------
                await crm.get_or_create_account("whieda", 7001)  # у Игоря открыт ежедневник: карточка заявки заводится
                lead = await save_lead(parse_lead_body(_body("c-a", "+7 (999) 111-22-33", first_ref="igor", active_ref="igor"), "whieda"))
                lead_row = (await rows("select lead_id::text as lead_id, metadata from website_leads where public_id = %s", (lead["public_id"],)))[0]
                card = lead_row["metadata"]["crm_card"]
                async with tenant_connection("whieda") as conn:
                    await mark_bot_opened(conn, "whieda", lead["public_id"], channel="telegram", channel_user_id=555001)
                before = (await rows("select tags, status, next_step from crm_contacts where contact_id = %s::uuid", (card,)))[0]
                assert before["status"] == "new" and before["next_step"] == "invite" and set(before["tags"]) == {"в боте"}

                assert (await press(555001, "funnel:consult"))["status"] == "consult"
                assert sent.await_args.kwargs["text"] == "Кто вы сейчас?"
                assert (await press(555001, "cq1:net"))["status"] == "consult_goal"
                assert (await press(555001, "cq2:net:money"))["status"] == "consult_host"
                assert [b["callback_data"] for row in sent.await_args.kwargs["reply_markup"]["inline_keyboard"] for b in row] == [
                    "chost:net:money:viktor", "chost:net:money:elena"
                ]
                assert await requests_of(555001) == []  # до выбора ведущего в таблице ничего нет
                assert await outbox() == []

                done = await press(555001, "chost:net:money:elena")
                assert done == {"ok": True, "route": "funnel_callback", "status": "consult_created", "trace_id": "t"}
                assert sent.await_args.kwargs["text"] == "Заявка принята. Елена напишет вам в Telegram и предложит время. Напоминание за час пришлём."
                (first,) = await requests_of(555001)
                assert first["lead_id"] == lead_row["lead_id"] and first["channel"] == "telegram" and first["host"] == "elena"
                assert first["display_name"] == "Анна Петрова" and first["answers"] == {"who": "net", "goal": "money"}
                assert first["status"] == "new" and first["notified"] is True
                request_id = first["request_id"]

                # Елене и копия Виктору: плановые сообщения (их забирает воркер по binding_id и due_at)
                queued = await outbox()
                assert [(m["idempotency_key"], m["event_type"], m["status"], m["due"]) for m in queued] == [
                    (f"consult:{request_id}", "consult_request_created", "scheduled", True),
                    (f"consult:{request_id}:owner", "consult_request_created", "scheduled", True),
                ]
                to_elena, to_owner = (m["payload"] for m in queued)
                assert to_elena["chat_id"] == "2002" and to_owner["chat_id"] == "1001"
                assert to_elena["binding_id"] == to_owner["binding_id"] == "whieda-advisor-bot"
                assert to_elena["text"].split("\n") == [
                    "Личный разбор: Анна Петрова", "Мессенджер: Telegram @anna_petrova", "Телефон: +79991112233",
                    "Кто: Сетевик", "Цель: Доход", "От партнёра: igor",
                ]
                assert to_owner["text"].split("\n")[:2] == ["Личный разбор: Анна Петрова", "Ведущий: Елена"]
                assert to_elena["reply_markup"] == to_owner["reply_markup"] == {
                    "inline_keyboard": [[{"text": "Написать", "url": "https://t.me/anna_petrova"}]]
                }

                # карточка партнёра: метка, заметка с ответами и ведущим, шаг «Напомнить о себе»
                after = (await rows("select tags, status, next_step, next_at from crm_contacts where contact_id = %s::uuid", (card,)))[0]
                assert set(after["tags"]) == {"в боте", "разбор"} and after["next_step"] == "ping" and after["next_at"] is not None
                note = (await rows("select body from crm_notes where contact_id = %s::uuid order by created_at desc limit 1", (card,)))[0]["body"]
                assert note == "Заявка на личный разбор. Ведущий: Елена. Кто: Сетевик. Цель: Доход."

                # ---- 2. повтор за 7 дней: тому же ведущему — нет, другому — да -----------------------------
                again = await press(555001, "chost:net:money:elena")
                assert again["status"] == "consult_exists"
                assert sent.await_args.kwargs["text"] == "Заявка уже есть, ведущий напишет."
                assert len(await requests_of(555001)) == 1 and len(await outbox()) == 2
                other_answers = await press(555001, "chost:sell:team:elena")  # ответы другие, ведущий тот же — всё равно «уже есть»
                assert other_answers["status"] == "consult_exists" and len(await requests_of(555001)) == 1

                to_viktor = await press(555001, "chost:biz:team:viktor")
                assert to_viktor["status"] == "consult_created"
                assert sent.await_args.kwargs["text"].startswith("Заявка принята. Виктор напишет вам в Telegram")
                regs = await requests_of(555001)
                assert [(r["host"], r["status"]) for r in regs] == [("elena", "new"), ("viktor", "new")]
                keys = [m["idempotency_key"] for m in await outbox()]
                assert keys[2:] == [f"consult:{regs[1]['request_id']}"]  # Виктору — одно сообщение, без копии самому себе

                # ---- 3. что не мешает новой заявке: давняя, выполненная, отменённая; что мешает: «ведущий написал» ----
                await set_request(request_id, status="'contacted'")
                assert (await press(555001, "chost:net:money:elena"))["status"] == "consult_exists"  # ведущий написал, разбор впереди
                await set_request(request_id, created_at="now() - interval '8 days'")
                assert (await press(555001, "chost:net:money:elena"))["status"] == "consult_created"  # неделя прошла
                second_id = (await requests_of(555001))[-1]["request_id"]
                await set_request(second_id, status="'done'")
                assert (await press(555001, "chost:net:money:elena"))["status"] == "consult_created"  # разбор проведён — можно снова
                third_id = (await requests_of(555001))[-1]["request_id"]
                await set_request(third_id, status="'cancelled'")
                assert (await press(555001, "chost:net:money:elena"))["status"] == "consult_created"  # отменённая не считается
                assert [r["host"] for r in await requests_of(555001)].count("elena") == 4

                # ---- 4. человек в Max без заявки: те же кнопки, канал max, без кнопки «Написать» -------------
                for payload in ("funnel:consult", "cq1:look", "cq2:look:system"):
                    assert (await press_max(555001, payload))["status"] == "handled"
                assert max_sent.await_args.kwargs["text"] == "С кем удобнее?"
                assert (await press_max(555001, "chost:look:system:elena"))["status"] == "handled"
                assert max_sent.await_args.kwargs["text"] == "Заявка принята. Елена напишет вам в Max и предложит время. Напоминание за час пришлём."
                (in_max,) = await requests_of(555001, "max")  # тот же числовой id, но другой мессенджер — другой человек
                assert in_max["lead_id"] is None and in_max["display_name"] == "Инна Орлова" and in_max["notified"] is True
                assert in_max["answers"] == {"who": "look", "goal": "system"}
                max_messages = [m["payload"] for m in await outbox() if m["payload"]["consult_request_id"] == in_max["request_id"]]
                assert len(max_messages) == 2 and all("reply_markup" not in p for p in max_messages)
                assert all(p["text"].split("\n")[-1] == "Ответьте человеку в Max." for p in max_messages)
                assert "Мессенджер: Max" in max_messages[0]["text"].split("\n") and "От партнёра" not in max_messages[0]["text"]
                assert (await press_max(555001, "chost:look:system:elena"))["status"] == "handled"
                assert max_sent.await_args.kwargs["text"] == "Заявка уже есть, ведущий напишет."
                assert len(await requests_of(555001, "max")) == 1

                # ---- 5. двойное нажатие одновременно: одна заявка, одно «уже есть» -------------------------
                oleg = consult.Requester("telegram", 556001, "Олег", None)
                results = await asyncio.gather(
                    consult.submit_request("whieda", oleg, "net", "team", "viktor"),
                    consult.submit_request("whieda", oleg, "net", "team", "viktor"),
                )
                assert sorted(r.status for r in results) == ["created", "exists"]
                (oleg_request,) = await requests_of(556001)
                oleg_messages = [m["payload"] for m in await outbox() if m["payload"]["consult_request_id"] == oleg_request["request_id"]]
                assert len(oleg_messages) == 1  # без ника ссылка идёт на id человека
                assert oleg_messages[0]["reply_markup"]["inline_keyboard"][0][0]["url"] == "tg://user?id=556001"
                assert oleg_messages[0]["text"].split("\n")[1] == "Мессенджер: Telegram, без ника"

                # ---- 6. ведущая без чата с ботом: заявка есть, notified_at пуст, Виктору — с пометкой ----------
                async with tenant_connection("whieda") as conn:
                    await conn.execute("update lead_actors set telegram_chat_id = null, telegram_user_id = null where actor_id = 'onlineelena'")
                no_chat = await consult.submit_request("whieda", consult.Requester("telegram", 556002, "Ира", "ira_k_01"), "net", "money", "elena")
                assert no_chat.status == "created" and no_chat.notified is False
                (stored,) = await requests_of(556002)
                assert stored["notified"] is False and stored["status"] == "new"
                owner_only = [m["payload"] for m in await outbox() if m["idempotency_key"] == f"consult:{stored['request_id']}:owner"]
                assert len(owner_only) == 1
                assert owner_only[0]["text"].split("\n")[-1] == "Ведущему сообщение не ушло: у него нет чата с ботом. Напишите ему сами."
                assert [m for m in await outbox() if m["idempotency_key"] == f"consult:{stored['request_id']}"] == []

                # ---- 7. стенд: бота плановых сообщений нет — заявка в таблице, в очереди ничего ----------------
                before_count = len(await outbox())
                monkeypatch.delenv("PLATFORM_SCHEDULED_NOTIFY_BINDINGS")
                get_settings.cache_clear()
                try:
                    staging = await consult.submit_request("whieda", consult.Requester("telegram", 556003, "Паша", None), "net", "money", "viktor")
                finally:
                    monkeypatch.setenv("PLATFORM_SCHEDULED_NOTIFY_BINDINGS", "whieda-advisor-bot")
                    get_settings.cache_clear()
                assert staging.status == "created" and staging.notified is False
                assert (await requests_of(556003))[0]["notified"] is False and len(await outbox()) == before_count

                # ---- 8. арендатор видит только свои заявки ------------------------------------------------
                assert len(await rows("select 1 from consult_requests", tenant_id="other")) == 0
                assert len(await rows("select 1 from consult_requests")) > 0
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    async with tenant_connection("other") as conn:
                        await conn.execute(
                            "insert into consult_requests (tenant_id, channel, channel_user_id, host) values ('whieda', 'max', 5, 'elena')"
                        )
                # у другого арендатора тот же человек и тот же ведущий — своя заявка, чужая «уже есть» её не закрывает
                other_tenant = await consult.submit_request("other", consult.Requester("telegram", 555001, "Анна", None), "net", "money", "elena")
                assert other_tenant.status == "created"
                assert len(await rows("select 1 from consult_requests where channel_user_id = 555001", tenant_id="other")) == 1

                # ---- 9. воркер плановых сообщений забирает эти строки тем же запросом, что и остальные --------------
                due_total = len(await rows("select 1 from platform_outbox where status = 'scheduled' and due_at <= now()"))
                due_consult = len(await rows("select 1 from platform_outbox where status = 'scheduled' and idempotency_key like 'consult:%%'"))
                assert due_consult > 0 and due_total >= due_consult  # в очереди есть и сообщение партнёру о заявке на встречу
                fake_send = AsyncMock()
                with patch.object(worker, "_send_due_notification", fake_send):
                    taken = await worker.process_due_notifications({"whieda-advisor-bot": _binding()})
                assert taken == due_total and fake_send.await_count == due_total
                consult_calls = [c.args[1] for c in fake_send.await_args_list if "consult_request_id" in c.args[1]]
                assert len(consult_calls) == due_consult and {p["chat_id"] for p in consult_calls} <= {"1001", "2002"}
                assert await rows("select 1 from platform_outbox where status <> 'done' and idempotency_key like 'consult:%%'") == []

        db.run_with_app(proof)
