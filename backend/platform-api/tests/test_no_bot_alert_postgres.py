"""Алерт «бот не открыл» в настоящем PostgreSQL (срез 9 бот-воронки, роль API без bypassrls).

1. Выборка: окно (от суток до трёх минут), открытый бот, заявки других видов и арендаторов, удалённые;
2. сообщение владельцу в ``platform_outbox`` (ключ ``no_bot_alert:<lead_id>``), кнопки, отправка воркером;
3. отметки в ``website_leads.metadata`` (jsonb): алерт, причина пропуска, дата шага карточки;
4. карточка CRM: заметка и шаг «Напомнить о себе» на сегодня; идемпотентность; гонка с открытием бота;
5. ``mark_bot_opened`` возвращает шаг по умолчанию, если его поставил алерт и партнёр шаг не менял.

Telegram не вызывается: отправка — мок.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import SQL_DIR, temporary_database
from tests.test_crm_postgres import LEAD_TABLES, SEED, _binding
from tests.test_meeting_leads_postgres import V44, _body, _env

NOTE = "Бот не открыт за 3 минуты. Следующий шаг: написать в WhatsApp или позвонить."


@pytest.mark.integration
def test_no_bot_alert_end_to_end(monkeypatch):
    _env(monkeypatch)
    soon = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=10)

    with temporary_database("whieda_no_bot_alert") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, twice=True)
            conn.execute(LEAD_TABLES)
            conn.execute("alter table website_leads add column deleted_at timestamptz")  # есть на бою
            conn.execute((SQL_DIR / V44).read_text(encoding="utf-8"))
            conn.execute(SEED)
            conn.execute(
                "insert into tenant_bot_bindings (binding_id, tenant_id, webhook_secret_ref, status) values "
                "('whieda-advisor-bot', 'whieda', 'env:X', 'active'), ('other-bot', 'other', 'env:Y', 'active')"
            )
            conn.execute(
                "insert into meetings (meeting_id, tenant_id, kind, title, starts_at, status) "
                "values (1, 'whieda', 'live', 'Живая встреча', %s, 'planned')",
                (soon,),
            )
            db.grant_api_role(conn)

        def admin(query: str, params: tuple = ()) -> list[tuple]:
            with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
                cur = conn.execute(query, params)
                return cur.fetchall() if cur.description else []

        async def proof() -> None:
            from app.crm import service as crm
            from app.db import fetch_all, tenant_connection
            from app.funnel.no_bot_alert import alert_one, find_candidates, no_bot_alert_step
            from app.jobs.worker import process_due_notifications
            from app.leads.meeting_leads import mark_bot_opened
            from app.leads.service import parse_lead_body, save_lead
            from app.meetings.service import as_msk, format_msk

            async def rows(query: str, params: tuple = (), tenant: str = "whieda") -> list[dict]:
                async with tenant_connection(tenant) as conn:
                    return [dict(r) for r in await fetch_all(conn, query, params)]

            async def lead_row(public_id: str) -> dict:
                return (await rows("select * from website_leads where public_id = %s", (public_id,)))[0]

            async def save(key: str, phone: str, age_minutes: float | None, tenant: str = "whieda", **extra) -> dict:
                saved = await save_lead(parse_lead_body(_body(key, phone, **extra), tenant))
                if age_minutes is not None:
                    admin(
                        "update website_leads set created_at = now() - make_interval(secs => %s) where public_id = %s",
                        (int(age_minutes * 60), saved["public_id"]),
                    )
                return saved

            async def alerts() -> list[dict]:
                return await rows(
                    "select idempotency_key, status, payload from platform_outbox "
                    "where event_type = 'lead_meeting_no_bot_alert' order by idempotency_key"
                )

            bindings = {"whieda": _binding()}
            await crm.get_or_create_account("whieda", 7001)  # igor открыл ежедневник; petr — нет
            igor = {"first_ref": "igor", "active_ref": "igor"}
            petr = {"first_ref": "petr", "active_ref": "petr"}
            web = {"page_url": "https://wwc.best/vstrecha/"}

            # ---- заявки: возраст в минутах ----------------------------------------------------
            a = await save("a", "+7 999 100-00-01", 10, meeting_id=1, **igor)  # попадает: карточка есть
            young = await save("young", "+7 999 100-00-02", 0.5, **igor)  # 3 минуты ещё не прошло
            old = await save("old", "+7 999 100-00-03", 25 * 60, **igor)  # старше суток
            d = await save("d", "+7 999 100-00-04", 10, messenger="max", **petr)  # попадает: карточки нет
            opened = await save("opened", "+7 999 100-00-05", 10, **igor)  # бот уже открыт
            staged = await save("staged", "+7 999 100-00-06", 10, page_url="https://staging.wwc.best/vstrecha/", **igor)
            g = await save("g", "+7 999 100-00-07", 10, **web)  # organic, у organic нет чата
            other_tenant = await save("o", "+7 999 100-00-09", 10, tenant="other", **web)
            request_kind = await save_lead(
                parse_lead_body(
                    {"name": "Ольга", "contact": "+7 999 666-77-88", "product": "Стельки", "idempotency_key": "old-1",
                     "initial_ref": "igor", "active_ref": "igor", "page_url": "https://igor.wwc.best/catalog/"},
                    "whieda",
                )
            )
            admin("update website_leads set created_at = now() - interval '10 minutes' where public_id = %s", (request_kind["public_id"],))
            deleted = await save("deleted", "+7 999 100-00-10", 10, **igor)
            admin("update website_leads set deleted_at = now() where public_id = %s", (deleted["public_id"],))
            async with tenant_connection("whieda") as conn:
                assert await mark_bot_opened(conn, "whieda", opened["public_id"], channel="telegram", channel_user_id=555) is not None

            ids = {name: str((await lead_row(saved["public_id"]))["lead_id"]) for name, saved in {
                "a": a, "young": young, "old": old, "d": d, "opened": opened, "staged": staged, "g": g,
                "other": other_tenant, "request": request_kind, "deleted": deleted,
            }.items()}

            # ---- 1. выборка: окно, открытый бот, вид заявки, арендатор, удалённые -------------------
            async with tenant_connection("whieda") as conn:
                candidates = await find_candidates(conn, "whieda")
            by_id = {row["lead_id"]: row for row in candidates}
            assert set(by_id) == {ids["a"], ids["d"], ids["staged"], ids["g"]}
            a_row = by_id[ids["a"]]
            assert a_row["owner_id"] == "igor-actor" and a_row["phone_e164"] == "+79991000001"
            assert a_row["starts_at"] == soon and a_row["card_id"] is not None
            assert by_id[ids["d"]]["starts_at"] is None and by_id[ids["d"]]["card_id"] is None
            assert by_id[ids["g"]]["owner_id"] == "organic"

            # ---- 2. шаг: алерты A и D; страница стенда и organic без чата пропущены с причиной ------
            assert await no_bot_alert_step(bindings) == 2
            assert await no_bot_alert_step({}) == 0  # процесс без бота whieda ничего не делает
            sent = await alerts()
            assert [r["idempotency_key"] for r in sent] == sorted([f"no_bot_alert:{ids['a']}", f"no_bot_alert:{ids['d']}"])
            assert {r["status"] for r in sent} == {"scheduled"}
            payloads = {r["idempotency_key"].split(":")[1]: r["payload"] for r in sent}

            card_a = (await lead_row(a["public_id"]))["metadata"]["crm_card"]
            pa = payloads[ids["a"]]
            when = format_msk(soon)
            assert pa["chat_id"] == "7001" and pa["binding_id"] == "whieda-advisor-bot" and pa["lead_id"] == ids["a"]
            assert pa["text"] == (
                f"⚠️ Анна Петрова записался(ась) на Живую встречу {when}, но бота не открыл(а). "
                "Телефон: +79991000001. Мессенджер: Telegram. Напишите в WhatsApp или позвоните."
            )
            wa_btn, card_btn = (row[0] for row in pa["reply_markup"]["inline_keyboard"])
            assert wa_btn == {"text": "WhatsApp", "url": "https://wa.me/79991000001"}
            assert card_btn["text"] == "👤 Открыть карточку" and f"contact={card_a}" in card_btn["url"]
            assert pa["site_login_user_id"] == 7001

            pd = payloads[ids["d"]]
            assert pd["chat_id"] == "7002"
            assert pd["text"].startswith("⚠️ Анна Петрова записался(ась) на Живую встречу, но бота не открыл(а). ")
            assert "Мессенджер: Max." in pd["text"] and "+79991000004" in pd["text"]
            assert pd["reply_markup"] == {"inline_keyboard": [[{"text": "WhatsApp", "url": "https://wa.me/79991000004"}]]}
            assert "site_login_user_id" not in pd

            # отметки в metadata
            def marks(meta: dict) -> dict:
                return {k: v for k, v in meta.items() if k.startswith("no_bot_alert")}

            today = admin(
                "select (now() at time zone a.timezone)::date::text from platform_accounts a where a.telegram_user_id = 7001"
            )[0][0]
            ma = marks((await lead_row(a["public_id"]))["metadata"])
            assert set(ma) == {"no_bot_alert_at", "no_bot_alert_step"} and ma["no_bot_alert_step"] == today
            assert ma["no_bot_alert_at"]  # время отметки (ISO) лежит в metadata->>'no_bot_alert_at'
            assert set(marks((await lead_row(d["public_id"]))["metadata"])) == {"no_bot_alert_at"}  # карточки нет — шага нет
            assert marks((await lead_row(staged["public_id"]))["metadata"]) == {"no_bot_alert_skip": "test_page"}
            assert marks((await lead_row(g["public_id"]))["metadata"]) == {"no_bot_alert_skip": "no_owner_chat"}
            for untouched in (young, old, opened, other_tenant, request_kind, deleted):
                assert marks((await lead_row(untouched["public_id"]))["metadata"]) == {}

            # карточка A: шаг «Напомнить о себе» на сегодня, заметка, метка «бот не открыл» осталась
            card = (await rows("select status, next_step, next_at::text as next_at, tags from crm_contacts where contact_id = %s::uuid", (card_a,)))[0]
            assert (card["status"], card["next_step"], card["next_at"]) == ("new", "ping", today)
            assert "бот не открыл" in card["tags"]
            notes = [r["body"] for r in await rows("select body from crm_notes where contact_id = %s::uuid order by created_at", (card_a,))]
            assert notes.count(NOTE) == 1
            kinds = [r["kind"] for r in await rows("select kind from crm_activities where contact_id = %s::uuid order by created_at", (card_a,))]
            assert kinds == ["created", "note", "note"]  # без новых видов истории
            # карточки заявок, которым алерт не слали (в т.ч. стенд), не тронуты
            young_card = (await lead_row(young["public_id"]))["metadata"]["crm_card"]
            assert (await rows("select next_step from crm_contacts where contact_id = %s::uuid", (young_card,)))[0]["next_step"] == "invite"

            # ---- 3. второй проход и повтор по ключу: ничего нового -----------------------------
            assert await no_bot_alert_step(bindings) == 0
            assert len(await alerts()) == 2
            admin("update website_leads set metadata = metadata - 'no_bot_alert_at' where public_id = %s", (a["public_id"],))
            assert await no_bot_alert_step(bindings) == 0  # ключ no_bot_alert:<id> уже в очереди: сообщения нет
            assert len(await alerts()) == 2
            notes_again = [r["body"] for r in await rows("select body from crm_notes where contact_id = %s::uuid", (card_a,))]
            assert notes_again.count(NOTE) == 1  # и заметка не дублируется
            assert (await lead_row(a["public_id"]))["metadata"]["no_bot_alert_at"]  # отметка поставлена снова

            # ---- 4. воркер отправляет: кнопка карточки становится входом, WhatsApp — без изменений -------
            send = AsyncMock(return_value={"ok": True, "message_id": 10})
            with patch("app.jobs.worker.send_telegram_text", send), patch(
                "app.telegram.site_login.create_bot_login", AsyncMock(return_value="tok")
            ):
                await process_due_notifications(bindings)
            alert_calls = [c.kwargs for c in send.await_args_list if c.kwargs["text"].startswith("⚠️")]
            assert {c["chat_id"] for c in alert_calls} == {"7001", "7002"}
            to_igor = next(c for c in alert_calls if c["chat_id"] == "7001")
            urls = [row[0]["url"] for row in to_igor["reply_markup"]["inline_keyboard"]]
            assert urls[0] == "https://wa.me/79991000001"
            assert urls[1].endswith("#wwc-login=tok") and f"contact={card_a}" in urls[1]
            assert {r["status"] for r in await alerts()} == {"done"}

            # ---- 5. владелец organic: чат появился — сообщение владельцу тем же текстом ----------------
            admin("update lead_actors set telegram_chat_id = '5001', telegram_user_id = 5001 where actor_id = 'organic'")
            h = await save("h", "+7 999 100-00-11", 10, **web)
            assert await no_bot_alert_step(bindings) == 1
            h_id = str((await lead_row(h["public_id"]))["lead_id"])
            ph = next(r["payload"] for r in await alerts() if r["idempotency_key"] == f"no_bot_alert:{h_id}")
            assert ph["chat_id"] == "5001" and ph["text"].startswith("⚠️ Анна Петрова записался(ась) на Живую встречу, но бота")
            assert [row[0]["text"] for row in ph["reply_markup"]["inline_keyboard"]] == ["WhatsApp"]

            # ---- 6. человек открыл бота между выбором и отметкой: сообщения нет --------------------------
            race = await save("race", "+7 999 100-00-12", 10, **igor)
            async with tenant_connection("whieda") as conn:
                pending = {r["lead_id"]: r for r in await find_candidates(conn, "whieda")}
                race_id = str((await lead_row(race["public_id"]))["lead_id"])
                assert race_id in pending
                await mark_bot_opened(conn, "whieda", race["public_id"], channel="telegram", channel_user_id=556)
                assert await alert_one(conn, tenant_id="whieda", binding_id="whieda-advisor-bot", row=pending[race_id]) == "raced"
            assert not any(r["idempotency_key"] == f"no_bot_alert:{race_id}" for r in await alerts())
            assert marks((await lead_row(race["public_id"]))["metadata"]) == {}

            # ---- 7. открыл бота после алерта: шаг по умолчанию вернулся, метка снята -------------------------
            async with tenant_connection("whieda") as conn:
                opened_a = await mark_bot_opened(conn, "whieda", a["public_id"], channel="telegram", channel_user_id=777)
            assert opened_a is not None and opened_a.bot_opened_at is not None
            card = (await rows("select next_step, next_at::text as next_at, tags from crm_contacts where contact_id = %s::uuid", (card_a,)))[0]
            assert (card["next_step"], card["next_at"]) == ("invite", today)
            assert "бот не открыл" not in card["tags"] and "в боте" in card["tags"]
            assert "no_bot_alert_step" not in (await lead_row(a["public_id"]))["metadata"]
            assert "no_bot_alert_at" in (await lead_row(a["public_id"]))["metadata"]  # что алерт был, помним
            sent_after = await alerts()
            assert len(sent_after) == 3  # после открытия новых сообщений нет

            # ---- 8. партнёр сам изменил шаг после алерта: открытие бота его не трогает ------------------------
            late = await save("late", "+7 999 100-00-13", 10, **igor)
            assert await no_bot_alert_step(bindings) == 1
            late_card = (await lead_row(late["public_id"]))["metadata"]["crm_card"]
            assert (await rows("select next_step from crm_contacts where contact_id = %s::uuid", (late_card,)))[0]["next_step"] == "ping"
            admin("update crm_contacts set next_step = 'decide', status = 'presented' where contact_id = %s::uuid", (late_card,))
            async with tenant_connection("whieda") as conn:
                await mark_bot_opened(conn, "whieda", late["public_id"], channel="max", channel_user_id=888)
            kept = (await rows("select status, next_step from crm_contacts where contact_id = %s::uuid", (late_card,)))[0]
            assert (kept["status"], kept["next_step"]) == ("presented", "decide")
            assert "no_bot_alert_step" not in (await lead_row(late["public_id"]))["metadata"]

            # ---- 9. шаг, отложенный партнёром на будущее, алерт не перебивает -----------------------------------------
            postponed = await save("postponed", "+7 999 100-00-14", 10, **igor)
            card_p = (await lead_row(postponed["public_id"]))["metadata"]["crm_card"]
            admin("update crm_contacts set next_at = (now() + interval '5 days')::date where contact_id = %s::uuid", (card_p,))
            assert await no_bot_alert_step(bindings) == 1  # сообщение уходит
            step_p = (await rows("select next_step, next_at::text as next_at from crm_contacts where contact_id = %s::uuid", (card_p,)))[0]
            assert step_p["next_step"] == "invite" and step_p["next_at"] != today  # шаг не заменён
            assert "no_bot_alert_step" not in (await lead_row(postponed["public_id"]))["metadata"]
            assert [r["body"] for r in await rows("select body from crm_notes where contact_id = %s::uuid", (card_p,))].count(NOTE) == 1

        db.run_with_app(proof)
