"""Запись на Живую встречу в настоящем PostgreSQL (V44, роль API без bypassrls).

1. V44 идемпотентна (дважды, а также до появления website_leads, как в testkit);
2. kind = meeting: заявка, привязка встречи, карточка CRM, сообщение партнёру через
   platform_outbox, событие lead_created для n8n НЕ ставится;
3. «первый партнёр навсегда»: тот же телефон — прежний владелец, person_lead_id = первая заявка;
4. load_lead_by_public_id и mark_bot_opened (метки карточки, заметка, повтор без дублей);
5. заявки других видов работают как раньше.

Telegram и n8n не вызываются: отправка — мок.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import SQL_DIR, temporary_database
from tests.test_crm_postgres import LEAD_TABLES, SEED, _binding

V44 = "platform_website_leads_meeting_v44.sql"

PERSON_COLUMNS = {
    "lead_kind": "text",
    "phone_e164": "text",
    "messenger_pref": "text",
    "utm": "jsonb",
    "source_tag": "text",
    "meeting_id": "bigint",
    "person_lead_id": "uuid",
    "telegram_user_id": "bigint",
    "max_user_id": "bigint",
    "bot_opened_at": "timestamp with time zone",
}


def test_v44_has_no_dollar_sign_and_is_listed_once():
    text = (SQL_DIR / V44).read_text(encoding="utf-8")
    assert "$" not in text  # боевые правки идут из контейнера и через n8n: знак доллара режется
    assert "set local lock_timeout" in text
    ps1 = (Path(__file__).resolve().parents[3] / "postgres" / "scripts" / "apply_staging_platform_all.ps1").read_text(
        encoding="utf-8"
    )
    assert ps1.count(V44) == 1


def _env(monkeypatch) -> None:
    for name in ("PLATFORM_DISABLED_FEATURES", "PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", "PLATFORM_BILLING_OWNER_TELEGRAM_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PLATFORM_ORGANIC_OWNER_ID", "organic")
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "*")
    monkeypatch.setenv("PLATFORM_CRM_LEAD_CARDS", "true")
    monkeypatch.setenv("PLATFORM_SCHEDULED_NOTIFY_BINDINGS", "whieda-advisor-bot")


def _body(key: str, phone: str, **extra) -> dict:
    return {
        "kind": "meeting",
        "name": extra.pop("name", "Анна Петрова"),
        "phone": phone,
        "messenger": extra.pop("messenger", "telegram"),
        "consent": {"accepted": True, "version": "wwc-consent-2026-10"},
        "idempotency_key": key,
        "page_url": extra.pop("page_url", "https://igor.wwc.best/vstrecha/"),
        **extra,
    }


@pytest.mark.integration
def test_meeting_leads_end_to_end(monkeypatch):
    _env(monkeypatch)
    soon = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=10)

    with temporary_database("whieda_meeting_leads") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            # В testkit website_leads ещё нет: V44 не падает без таблицы (alter table if exists + do-блок).
            db.apply_migrations(conn, twice=True)
            conn.execute(LEAD_TABLES)
            conn.execute("alter table website_leads add column deleted_at timestamptz")  # есть на бою
            for _ in range(2):  # идемпотентность на таблице, где колонок ещё нет и где они уже есть
                conn.execute((SQL_DIR / V44).read_text(encoding="utf-8"))
            columns = dict(
                conn.execute(
                    "select column_name, data_type from information_schema.columns "
                    "where table_name = 'website_leads' and column_name = any(%s)",
                    (list(PERSON_COLUMNS),),
                ).fetchall()
            )
            assert columns == PERSON_COLUMNS
            indexes = {
                row[0]
                for row in conn.execute(
                    "select indexname from pg_indexes where tablename = 'website_leads'"
                ).fetchall()
            }
            assert {"idx_website_leads_phone", "idx_website_leads_meeting"} <= indexes
            conn.execute(SEED)
            conn.execute(
                "insert into tenant_bot_bindings (binding_id, tenant_id, webhook_secret_ref, status) values "
                "('whieda-advisor-bot', 'whieda', 'env:X', 'active'), ('other-bot', 'other', 'env:Y', 'active')"
            )
            # встречи: 1 — живая и будущая; 2 — консультация; 3 — отменена; 4 — прошла по времени
            conn.execute(
                """
                insert into meetings (meeting_id, tenant_id, kind, title, starts_at, status) values
                  (1, 'whieda', 'live', 'Живая встреча', %s, 'planned'),
                  (2, 'whieda', 'consult', 'Консультация', %s, 'planned'),
                  (3, 'whieda', 'live', 'Живая встреча', %s, 'cancelled'),
                  (4, 'whieda', 'live', 'Живая встреча', %s, 'planned')
                """,
                (soon, soon, soon, datetime.now(timezone.utc) - timedelta(days=1)),
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "insert into website_leads (tenant_id, name, contact, product_name, attributed_owner_id, "
                    "assigned_owner_id, idempotency_key, consent_version, lead_kind) "
                    "values ('whieda', 'x', 'x', 'x', 'o', 'o', 'bad-kind', 'v', 'newkind')"
                )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "insert into website_leads (tenant_id, name, contact, product_name, attributed_owner_id, "
                    "assigned_owner_id, idempotency_key, consent_version, messenger_pref) "
                    "values ('whieda', 'x', 'x', 'x', 'o', 'o', 'bad-messenger', 'v', 'icq')"
                )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.crm import service as crm
            from app.db import fetch_all, tenant_connection
            from app.jobs.worker import process_due_notifications
            from app.leads.meeting_leads import load_lead_by_public_id, mark_bot_opened
            from app.leads.service import parse_lead_body, save_lead
            from app.meetings.service import as_msk

            async def rows(query: str, params: tuple = (), tenant: str = "whieda") -> list[dict]:
                async with tenant_connection(tenant) as conn:
                    return [dict(r) for r in await fetch_all(conn, query, params)]

            async def lead_row(public_id: str) -> dict:
                return (await rows("select * from website_leads where public_id = %s", (public_id,)))[0]

            async def save(key: str, phone: str, tenant: str = "whieda", **extra) -> dict:
                return await save_lead(parse_lead_body(_body(key, phone, **extra), tenant))

            await crm.get_or_create_account("whieda", 7001)  # igor-actor открыл ежедневник; petr-actor — нет

            # ---- 1. запись с выбранной встречей -------------------------------------------------
            first = await save(
                "m-a", "+7 (999) 111-22-33", meeting_id=1, marketing_consent=True,
                utm={"source": "instagram", "campaign": "oct", "junk": "x"}, source_tag="Reels_Oct",
                first_ref="igor", active_ref="igor", landing_url="https://wwc.best/vstrecha/",
            )
            assert first["created"] is True
            a = await lead_row(first["public_id"])
            assert (a["lead_kind"], a["phone_e164"], a["messenger_pref"], a["meeting_id"]) == (
                "meeting", "+79991112233", "telegram", 1,
            )
            assert a["utm"] == {"source": "instagram", "campaign": "oct"}
            assert a["source_tag"] == "reels_oct"
            assert a["person_lead_id"] is None
            assert a["assigned_owner_id"] == "igor-actor" and a["attributed_owner_id"] == "igor-actor"
            assert (a["product_name"], a["contact"], a["name"]) == ("Живая встреча", "+79991112233", "Анна Петрова")
            assert a["marketing_consent"] is True and a["consent_version"] == "wwc-consent-2026-10"
            assert a["first_ref_code"] == "igor" and a["active_ref_code"] == "igor"
            assert a["metadata"]["landing_url"] == "https://wwc.best/vstrecha/"
            assert "meeting_id_rejected" not in a["metadata"] and a["metadata"]["crm_card"]
            history = await rows("select owner_id, reason from website_lead_owner_history where lead_id = %s", (a["lead_id"],))
            assert history == [{"owner_id": "igor-actor", "reason": "Server-side referral routing"}]
            # n8n о такой заявке не узнаёт: события lead_created нет
            assert await rows("select 1 from platform_outbox where idempotency_key = %s", (f"lead_delivery:{a['lead_id']}",)) == []

            # карточка CRM
            card = (await rows("select * from crm_contacts where contact_id = %s::uuid", (a["metadata"]["crm_card"],)))[0]
            when_tag = f"записан {as_msk(soon):%d.%m}"
            assert (card["source"], card["status"], card["phone_e164"], card["lead_id"]) == (
                "встреча", "new", "+79991112233", a["lead_id"],
            )
            assert set(card["tags"]) == {when_tag, "бот не открыл"}
            notes = await rows("select body from crm_notes where contact_id = %s::uuid", (card["contact_id"],))
            assert len(notes) == 1
            note_lines = notes[0]["body"].split("\n")
            assert note_lines[:3] == ["Запись на Живую встречу.", "Телефон: +79991112233", "Мессенджер: Telegram"]
            assert note_lines[3].startswith("Встреча: ") and note_lines[3].endswith(" МСК")
            assert note_lines[4] == "Откуда: партнёр igor, метка reels_oct, источник instagram"
            kinds = [r["kind"] for r in await rows("select kind from crm_activities where contact_id = %s::uuid order by created_at", (card["contact_id"],))]
            assert kinds == ["created", "note"]

            # сообщение партнёру в очереди: одно, запланированное, с кнопкой на карточку
            queued = await rows("select * from platform_outbox where event_type = 'lead_meeting_created' order by outbox_id")
            assert len(queued) == 1 and queued[0]["status"] == "scheduled"
            payload = queued[0]["payload"]
            assert payload["chat_id"] == "7001" and payload["binding_id"] == "whieda-advisor-bot"
            assert payload["text"].split("\n")[0].startswith("Новая запись на Живую встречу ")
            assert payload["text"].split("\n")[1:] == [
                "Имя: Анна Петрова", "Телефон: +79991112233", "Мессенджер: Telegram",
                "Откуда: партнёр igor, метка reels_oct, источник instagram",
            ]
            button = payload["reply_markup"]["inline_keyboard"][0][0]
            assert button["text"] == "👤 Открыть карточку"
            assert f"contact={card['contact_id']}" in button["url"] and "/crm/" in button["url"]
            assert payload["site_login_user_id"] == 7001
            send = AsyncMock(return_value={"ok": True, "message_id": 10})
            login = AsyncMock(side_effect=lambda url, **_: url.split("#", 1)[0] + "#wwc-login=t")
            with patch("app.jobs.worker.send_telegram_text", send), patch("app.telegram.site_login.with_site_login", login):
                assert await process_due_notifications({"whieda": _binding()}) == 1
            assert send.await_args.kwargs["chat_id"] == "7001"
            assert send.await_args.kwargs["text"] == payload["text"]
            assert send.await_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["url"].endswith("#wwc-login=t")
            assert (await rows("select status from platform_outbox where event_type = 'lead_meeting_created'")) == [{"status": "done"}]

            # ---- 2. повтор того же запроса ----------------------------------------------------
            again = await save("m-a", "+7 (999) 111-22-33", meeting_id=1, first_ref="igor", active_ref="igor")
            assert again["created"] is False and again["public_id"] == first["public_id"]
            assert len(await rows("select 1 from website_leads where lead_kind = 'meeting'")) == 1
            assert len(await rows("select 1 from crm_contacts")) == 1
            assert len(await rows("select 1 from platform_outbox where event_type = 'lead_meeting_created'")) == 1

            # ---- 3. тот же телефон, ссылка другого партнёра, но бот по первой заявке не открывали:
            #         номер не закреплён (его мог вписать кто угодно), заявка идёт по своей ссылке ---------
            second = await save("m-b", "8 999 111 22 33", first_ref="petr", active_ref="petr", messenger="max",
                                page_url="https://petr.wwc.best/vstrecha/")
            b = await lead_row(second["public_id"])
            assert b["assigned_owner_id"] == "petr-actor" and b["attributed_owner_id"] == "petr-actor"
            assert b["person_lead_id"] == a["lead_id"] and b["meeting_id"] is None  # человек тот же, владелец — по ссылке
            assert "owner_locked_by_phone" not in b["metadata"]
            history = await rows("select owner_id, reason from website_lead_owner_history where lead_id = %s", (b["lead_id"],))
            assert history == [{"owner_id": "petr-actor", "reason": "Server-side referral routing"}]
            # у Игоря по-прежнему одна карточка человека и ни одной лишней записи; у Петра ежедневник не открыт
            assert len(await rows("select 1 from crm_contacts")) == 1
            card_after = (await rows("select tags from crm_contacts"))[0]
            assert "бот не открыл" in card_after["tags"] and when_tag in card_after["tags"]
            assert [r["kind"] for r in await rows("select kind from crm_activities where contact_id = %s::uuid order by created_at", (card["contact_id"],))] == [
                "created", "note",
            ]
            # партнёр новой заявки — другой, чем у первой: сообщение уходит ему (дедуп по телефону — на одного партнёра)
            second_text = (await rows("select payload from platform_outbox where idempotency_key = %s", (f"lead_meeting:{b['lead_id']}",)))[0]["payload"]
            assert second_text["chat_id"] == "7002"
            assert second_text["text"].split("\n")[0] == "Новая запись на Живую встречу"  # встреча не выбрана
            assert "Мессенджер: Max" in second_text["text"]
            assert second_text["text"].endswith("Откуда: партнёр petr")

            # ---- 4. партнёр без открытого ежедневника: карточки нет, сообщение есть -------------
            third = await save("m-c", "+7 999 222-33-44", first_ref="petr", active_ref="petr")
            c = await lead_row(third["public_id"])
            assert c["assigned_owner_id"] == "petr-actor" and c["person_lead_id"] is None
            assert "crm_card" not in c["metadata"]
            c_msg = (await rows("select payload from platform_outbox where idempotency_key = %s", (f"lead_meeting:{c['lead_id']}",)))[0]["payload"]
            assert c_msg["chat_id"] == "7002" and "reply_markup" not in c_msg

            # ---- 5. organic первой заявки человека не закрепляет -----------------------------
            organic_first = await save("m-d1", "+7 999 333-44-55", page_url="https://wwc.best/vstrecha/")
            assert (await lead_row(organic_first["public_id"]))["assigned_owner_id"] == "organic"
            by_partner = await save("m-d2", "+7 999 333-44-55", first_ref="petr", active_ref="petr")
            d2 = await lead_row(by_partner["public_id"])
            assert d2["assigned_owner_id"] == "petr-actor"
            assert d2["person_lead_id"] == (await lead_row(organic_first["public_id"]))["lead_id"]
            by_other = await save("m-d3", "+7 999 333-44-55", first_ref="igor", active_ref="igor")
            # d2 бота не открывала: номер за petr не закреплён, заявка идёт по своей ссылке (igor)
            assert (await lead_row(by_other["public_id"]))["assigned_owner_id"] == "igor-actor"

            # ---- 6. чужой арендатор телефон не закрепляет ---------------------------------------
            other = await save("m-o", "+7 (999) 111-22-33", tenant="other", page_url="https://wwc.best/vstrecha/")
            o = (await rows("select assigned_owner_id, person_lead_id from website_leads where public_id = %s", (other["public_id"],), "other"))[0]
            assert o == {"assigned_owner_id": "organic", "person_lead_id": None}

            # ---- 7. встреча, которую нельзя привязать ------------------------------------------
            for number, (meeting_id, reason) in enumerate(((2, "not_live"), (3, "not_planned"), (4, "in_past"), (999, "not_found"))):
                saved = await save(f"m-bad-{meeting_id}", f"+7 999 444-00-0{number}", meeting_id=meeting_id,
                                   first_ref="igor", active_ref="igor")
                bad = await lead_row(saved["public_id"])
                assert bad["meeting_id"] is None
                assert bad["metadata"]["meeting_id_requested"] == meeting_id
                assert bad["metadata"]["meeting_id_rejected"] == reason
            assert [t for t in (await rows("select tags from crm_contacts where phone_e164 = '+79994440000'"))[0]["tags"]] == ["бот не открыл"]

            # ---- 8. страница стенда: заявка сохраняется, партнёру не пишем -----------------------
            staged = await save("m-stg", "+7 999 555-00-00", page_url="https://staging.wwc.best/vstrecha/",
                                first_ref="igor", active_ref="igor")
            assert staged["created"] is True
            assert await rows("select 1 from platform_outbox where idempotency_key = %s", (f"lead_meeting:{(await lead_row(staged['public_id']))['lead_id']}",)) == []

            # ---- 9. поиск заявки по public_id ---------------------------------------------------
            async with tenant_connection("whieda") as conn:
                loaded = await load_lead_by_public_id(conn, "whieda", first["public_id"])
                assert loaded is not None
                assert (loaded.lead_id, loaded.public_id, loaded.name, loaded.phone_e164) == (
                    str(a["lead_id"]), first["public_id"], "Анна Петрова", "+79991112233",
                )
                assert (loaded.messenger_pref, loaded.meeting_id, loaded.assigned_owner_id) == ("telegram", 1, "igor-actor")
                assert (loaded.first_ref_code, loaded.active_ref_code, loaded.source_tag) == ("igor", "igor", "reels_oct")
                assert loaded.utm == {"source": "instagram", "campaign": "oct"}
                assert (loaded.telegram_user_id, loaded.max_user_id, loaded.bot_opened_at) == (None, None, None)
                assert await load_lead_by_public_id(conn, "whieda", "L-NOPE") is None
                assert await load_lead_by_public_id(conn, "whieda", "") is None
            async with tenant_connection("other") as conn:
                assert await load_lead_by_public_id(conn, "other", first["public_id"]) is None  # чужой арендатор

            # ---- 10. человек открыл бота -----------------------------------------------------
            async with tenant_connection("whieda") as conn:
                opened = await mark_bot_opened(conn, "whieda", first["public_id"], channel="telegram", channel_user_id=555001)
            assert opened is not None and opened.telegram_user_id == 555001 and opened.max_user_id is None
            assert opened.bot_opened_at is not None
            tags = (await rows("select tags from crm_contacts where contact_id = %s::uuid", (card["contact_id"],)))[0]["tags"]
            assert "бот не открыл" not in tags and "в боте" in tags and when_tag in tags
            bot_notes = [r["body"] for r in await rows("select body from crm_notes where contact_id = %s::uuid order by created_at", (card["contact_id"],))]
            assert bot_notes.count("Открыл(а) бота WWC в Telegram.") == 1
            kinds_after = [r["kind"] for r in await rows("select kind from crm_activities where contact_id = %s::uuid order by created_at", (card["contact_id"],))]
            assert kinds_after == ["created", "note", "note"]

            # повтор и второй канал: время не двигается, заметок не прибавляется
            async with tenant_connection("whieda") as conn:
                repeat = await mark_bot_opened(conn, "whieda", first["public_id"], channel="telegram", channel_user_id=999)
                via_max = await mark_bot_opened(conn, "whieda", first["public_id"], channel="max", channel_user_id=777002)
            assert repeat.bot_opened_at == opened.bot_opened_at and repeat.telegram_user_id == 555001  # первый открывший остаётся
            assert via_max.max_user_id == 777002 and via_max.telegram_user_id == 555001
            assert via_max.bot_opened_at == opened.bot_opened_at
            assert len(await rows("select 1 from crm_notes where contact_id = %s::uuid", (card["contact_id"],))) == len(bot_notes)

            # заявка без карточки отмечается тоже; неизвестная и чужая — None
            async with tenant_connection("whieda") as conn:
                no_card = await mark_bot_opened(conn, "whieda", third["public_id"], channel="max", channel_user_id=321)
                assert no_card is not None and no_card.max_user_id == 321 and no_card.bot_opened_at is not None
                assert await mark_bot_opened(conn, "whieda", "L-NOPE", channel="telegram", channel_user_id=1) is None
                with pytest.raises(ValueError):
                    await mark_bot_opened(conn, "whieda", first["public_id"], channel="whatsapp", channel_user_id=1)  # type: ignore[arg-type]
            async with tenant_connection("other") as conn:
                assert await mark_bot_opened(conn, "other", first["public_id"], channel="telegram", channel_user_id=1) is None

            def admin(statement: str, params: tuple = ()) -> None:
                with psycopg.connect(db.admin_dsn, autocommit=True) as admin_conn:
                    admin_conn.execute(statement, params)

            # ---- 10б. номер подтверждён в боте: «первый партнёр навсегда» -------------------------------
            # заявка a открыта в боте (шаг 10), её владелец igor — действующий партнёр: номер закреплён за ним,
            # хотя новая заявка пришла по ссылке petr
            anchored = await save("m-b2", "+7 999 111-22-33", first_ref="petr", active_ref="petr",
                                  page_url="https://petr.wwc.best/vstrecha/")
            b2 = await lead_row(anchored["public_id"])
            assert b2["assigned_owner_id"] == "igor-actor"  # первый (подтверждённый) партнёр навсегда
            assert b2["attributed_owner_id"] == "petr-actor"  # а чья ссылка привела — записано
            assert b2["person_lead_id"] == a["lead_id"] and b2["metadata"]["owner_locked_by_phone"] is True
            history = await rows("select owner_id, reason from website_lead_owner_history where lead_id = %s", (b2["lead_id"],))
            assert history == [{"owner_id": "igor-actor", "reason": "First partner kept: the same phone already has a lead"}]
            # одна карточка человека у Игоря: вторая запись — заметка и метка, без дубля
            assert len(await rows("select 1 from crm_contacts where phone_e164 = '+79991112233'")) == 1
            assert "lead" in [r["kind"] for r in await rows("select kind from crm_activities where contact_id = %s::uuid", (card["contact_id"],))]
            # Игорю об этом человеке уже писали (заявка a, меньше суток назад): второе сообщение не ставится
            assert await rows("select 1 from platform_outbox where idempotency_key = %s", (f"lead_meeting:{b2['lead_id']}",)) == []
            # а новый человек у того же партнёра сообщение получает (дедуп — по телефону)
            fresh = await save("m-new", "+7 999 123-00-00", first_ref="igor", active_ref="igor")
            assert len(await rows("select 1 from platform_outbox where idempotency_key = %s",
                                  (f"lead_meeting:{(await lead_row(fresh['public_id']))['lead_id']}",))) == 1
            # сутки прошли: заявка a старше 24 часов, заявка b2 сообщения не получала (не считается) —
            # следующая заявка с этим номером снова даёт Игорю сообщение
            admin("update website_leads set created_at = created_at - interval '25 hours' where lead_id = %s::uuid", (a["lead_id"],))
            next_day = await save("m-b3", "+7 999 111-22-33", first_ref="petr", active_ref="petr",
                                  page_url="https://petr.wwc.best/vstrecha/")
            b3 = await lead_row(next_day["public_id"])
            assert b3["assigned_owner_id"] == "igor-actor" and b3["person_lead_id"] == a["lead_id"]
            day_message = (await rows("select payload from platform_outbox where idempotency_key = %s", (f"lead_meeting:{b3['lead_id']}",)))[0]["payload"]
            assert day_message["chat_id"] == "7001" and day_message["text"].endswith("Откуда: партнёр petr")
            # и сразу после неё — снова тишина: окно считается от последнего сообщения
            right_after = await save("m-b4", "+7 999 111-22-33", first_ref="petr", active_ref="petr",
                                     page_url="https://petr.wwc.best/vstrecha/")
            assert await rows("select 1 from platform_outbox where idempotency_key = %s",
                              (f"lead_meeting:{(await lead_row(right_after['public_id']))['lead_id']}",)) == []

            # ---- 10в. якорь — только действующий партнёр (active или grace) ------------------------------
            g1 = await save("m-g1", "+7 999 888-00-01", first_ref="petr", active_ref="petr")
            async with tenant_connection("whieda") as conn:
                assert (await mark_bot_opened(conn, "whieda", g1["public_id"], channel="telegram", channel_user_id=555020)) is not None
            admin("update partner_subscriptions set paid_until = now() - interval '1 day' where ref_code = 'petr'")  # льготный срок
            g2 = await lead_row((await save("m-g2", "+7 999 888-00-01", first_ref="igor", active_ref="igor"))["public_id"])
            assert g2["assigned_owner_id"] == "petr-actor" and g2["metadata"]["owner_locked_by_phone"] is True  # grace держит номер
            admin("update partner_subscriptions set paid_until = now() - interval '10 days' where ref_code = 'petr'")  # ушёл
            g3 = await lead_row((await save("m-g3", "+7 999 888-00-01", first_ref="igor", active_ref="igor"))["public_id"])
            assert g3["assigned_owner_id"] == "igor-actor" and "owner_locked_by_phone" not in g3["metadata"]
            assert g3["person_lead_id"] == g2["person_lead_id"] == (await lead_row(g1["public_id"]))["lead_id"]
            g3_history = await rows("select reason from website_lead_owner_history where lead_id = %s", (g3["lead_id"],))
            assert g3_history == [{"reason": "Server-side referral routing"}]
            admin("update partner_subscriptions set paid_until = now() + interval '30 days' where ref_code = 'petr'")

            # ---- 11. заявка другого вида: как раньше --------------------------------------------
            old = await save_lead(
                parse_lead_body(
                    {"name": "Ольга", "contact": "+7 999 666-77-88", "product": "Стельки", "idempotency_key": "old-1",
                     "initial_ref": "igor", "active_ref": "igor", "page_url": "https://igor.wwc.best/catalog/"},
                    "whieda",
                )
            )
            o_row = await lead_row(old["public_id"])
            assert (o_row["lead_kind"], o_row["phone_e164"], o_row["person_lead_id"], o_row["meeting_id"]) == ("request", None, None, None)
            assert o_row["assigned_owner_id"] == "igor-actor"
            assert len(await rows("select 1 from platform_outbox where idempotency_key = %s", (f"lead_delivery:{o_row['lead_id']}",))) == 1
            old_card = (await rows("select source, tags from crm_contacts where lead_id = %s::uuid", (o_row["lead_id"],)))[0]
            assert old_card == {"source": "сайт", "tags": []}
            old_history = await rows("select reason from website_lead_owner_history where lead_id = %s", (o_row["lead_id"],))
            assert old_history == [{"reason": "Server-side referral routing"}]

        db.run_with_app(proof)
