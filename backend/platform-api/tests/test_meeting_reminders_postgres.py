"""Срез 5 бот-воронки в настоящем PostgreSQL (роль API без bypassrls): напоминания, «Присоединиться», после встречи.

Сквозной сценарий: заявки и записи на Живую встречу → напоминания «за сутки / 2 часа / час / 5 минут» (ключи, отметки
в ``reminders_sent``, Telegram и Max, повторный проход) → «Присоединиться» (первое и повторное нажатие, не в окне, без
ссылки, чужая запись) → через 30 минут после конца: ``attended`` / ``no_show``, визитки (партнёр, реклама — две подряд),
сообщение «не дошли» с записью и ближайшими встречами, метки и шаг карточек CRM, итог партнёрам, встреча ``done`` →
повторный проход ничего не шлёт → воркер отправляет строки (фото визитки, кнопки, Max).

Telegram и Max не вызываются: отправка — мок. «Сейчас» шага задаётся явно (``now=``), встреча — через 10 дней.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import SQL_DIR, temporary_database
from tests.test_crm_postgres import LEAD_TABLES, SEED, _binding
from tests.test_meeting_leads_postgres import V44, _body, _env
from tests.test_meeting_registrations_postgres import V46

ZOOM = "https://us02web.zoom.us/j/81234567890?pwd=SeCrEt"
RECORDING = "https://rutube.example/video/abc"
IGOR_PHOTO = "https://wwc.best/media/igor.jpg"


@pytest.mark.integration
def test_meeting_reminders_end_to_end(monkeypatch):
    _env(monkeypatch)
    monkeypatch.delenv("PLATFORM_MEETING_AD_CARD_REFS", raising=False)
    real_now = datetime.now(timezone.utc).replace(microsecond=0)
    start = real_now + timedelta(days=10)
    start2 = real_now + timedelta(days=12)

    with temporary_database("whieda_meeting_reminders") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, twice=True)
            conn.execute(LEAD_TABLES)
            conn.execute("alter table website_leads add column deleted_at timestamptz")  # есть на бою
            conn.execute((SQL_DIR / V44).read_text(encoding="utf-8"))
            conn.execute((SQL_DIR / V46).read_text(encoding="utf-8"))
            conn.execute(SEED)
            igor_profile = (
                '{"display_name": "Игорь Волков", "photo_url": "%s", "public_site_url": "https://igor.wwc.best/", '
                '"contacts": {"phone": "+7 999 111-22-33", "max_url": "https://max.ru/u/igor", "email": "igor@example.com"}}'
                % IGOR_PHOTO
            )
            conn.execute("update referral_profiles set public_profile = public_profile || %s::jsonb where ref_code = 'igor'", (igor_profile,))
            conn.execute("update lead_actors set telegram_username = '@igor_v' where actor_id = 'igor-actor'")
            conn.execute(
                "update lead_actors set telegram_chat_id = '7003', telegram_user_id = 7003, telegram_username = 'khripko', "
                "display_name = 'Виктор Хрипко' where actor_id = 'organic'"
            )
            conn.execute(
                "insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id, telegram_user_id) "
                "values ('elena-actor', 'whieda', 'Елена', '7004', 7004)"
            )
            conn.execute(
                "insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, public_profile, enabled) "
                "values ('onlineelena', 'whieda', 'elena-actor', 'named', %s::jsonb, true)",
                ('{"display_name": "Елена Онлайн", "subdomain": "elena", "contacts": {"max_url": "https://max.ru/u/elena"}}',),
            )
            conn.execute(
                "insert into tenant_bot_bindings (binding_id, tenant_id, webhook_secret_ref, status) "
                "values ('whieda-advisor-bot', 'whieda', 'env:X', 'active')"
            )
            conn.execute(
                "insert into meetings (meeting_id, tenant_id, kind, title, starts_at, duration_min, join_url, status) values "
                "(1, 'whieda', 'live', 'Живая встреча', %s, 60, %s, 'planned'), "
                "(2, 'whieda', 'live', 'Живая встреча', %s, 60, null, 'planned'), "
                "(3, 'whieda', 'live', 'Живая встреча', %s, 60, %s, 'cancelled')",
                (start, ZOOM, start2, start + timedelta(days=1), ZOOM),
            )
            db.grant_api_role(conn)

        def admin(query: str, params: tuple = ()) -> list[tuple]:
            with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
                cur = conn.execute(query, params)
                return cur.fetchall() if cur.description else []

        async def proof() -> None:
            from app.crm import service as crm
            from app.db import fetch_all, tenant_connection
            from app.funnel import registration
            from app.funnel.reminders import reminders_step
            from app.jobs import worker
            from app.leads.service import parse_lead_body, save_lead
            from app.meetings import registrations
            from app.meetings.service import as_msk, format_msk

            async def rows(query: str, params: tuple = (), tenant: str = "whieda") -> list[dict]:
                async with tenant_connection(tenant) as conn:
                    return [dict(r) for r in await fetch_all(conn, query, params)]

            async def lead_id(public_id: str) -> str:
                return str((await rows("select lead_id from website_leads where public_id = %s", (public_id,)))[0]["lead_id"])

            async def save(key: str, phone: str, name: str, **extra) -> dict:
                return await save_lead(parse_lead_body(_body(key, phone, name=name, **extra), "whieda"))

            async def register(meeting_id: int, channel: str, user: int, lead: str | None) -> dict:
                async with tenant_connection("whieda") as conn:
                    outcome = await registrations.register(
                        conn, "whieda", meeting_id=meeting_id, channel=channel, channel_user_id=user, lead_id=lead
                    )
                assert outcome.status == "registered"
                return {"id": outcome.registration.registration_id, "user": user}

            async def outbox(event_type: str | None = None) -> list[dict]:
                where = "where event_type = %s" if event_type else ""
                return await rows(
                    f"select idempotency_key, event_type, status, payload, due_at from platform_outbox {where} order by outbox_id",
                    (event_type,) if event_type else (),
                )

            async def reg_row(registration_id: int) -> dict:
                return (await rows(
                    "select status, joined_at, reminders_sent from meeting_registrations where registration_id = %s", (registration_id,)
                ))[0]

            def at(delta: timedelta) -> datetime:
                return start - delta

            bindings = {"whieda": _binding()}
            await crm.get_or_create_account("whieda", 7001)  # у Игоря открыт ежедневник: карточки заявок заводятся

            # ---- гости ---------------------------------------------------------------------------------
            igor = {"first_ref": "igor", "active_ref": "igor"}
            ad = {"page_url": "https://wwc.best/vstrecha/"}
            lead_a = await save("a", "+7 999 100-00-01", "Анна", meeting_id=1, **igor)
            lead_b = await save("b", "+7 999 100-00-02", "Борис", meeting_id=1, **ad)
            lead_c = await save("c", "+7 999 100-00-03", "Светлана", meeting_id=1, messenger="max", **igor)
            lead_e = await save("e", "+7 999 100-00-05", "Елизавета", meeting_id=1, **igor)
            lead_f = await save("f", "+7 999 100-00-06", "Фёдор", meeting_id=1, **ad)
            ids = {name: await lead_id(saved["public_id"]) for name, saved in
                   {"a": lead_a, "b": lead_b, "c": lead_c, "e": lead_e, "f": lead_f}.items()}
            owners = {r["lead_id"]: r["assigned_owner_id"] for r in await rows("select lead_id::text as lead_id, assigned_owner_id from website_leads")}
            assert owners[ids["a"]] == owners[ids["c"]] == "igor-actor" and owners[ids["b"]] == owners[ids["f"]] == "organic"
            card_of = {
                name: (await rows("select metadata ->> 'crm_card' as card from website_leads where lead_id = %s::uuid", (ids[name],)))[0]["card"]
                for name in ("a", "b", "c", "f")
            }
            assert card_of["a"] and card_of["c"] and card_of["b"] is None   # карточки есть только у партнёра с ежедневником

            reg_a = await register(1, "telegram", 5001, ids["a"])     # нажмёт «Присоединиться»
            reg_b = await register(1, "telegram", 5002, ids["b"])     # реклама, не нажмёт
            reg_c = await register(1, "max", 5003, ids["c"])          # Max, нажмёт
            reg_d = await register(1, "telegram", 5004, None)         # записался из бота, без заявки, не нажмёт
            reg_f = await register(1, "telegram", 5006, ids["f"])     # реклама, нажмёт: две визитки
            reg_e = await register(1, "telegram", 5005, ids["e"])     # отменит запись
            reg_m2 = await register(2, "telegram", 5001, ids["a"])    # встреча без ссылки
            async with tenant_connection("whieda") as conn:
                cancelled = await registrations.cancel(
                    conn, "whieda", registration_id=reg_e["id"], channel="telegram", channel_user_id=5005
                )
            assert cancelled.status == "cancelled"
            admin("update meetings set recording_url = %s where meeting_id = 1", (RECORDING,))
            active = [reg_a, reg_b, reg_c, reg_d, reg_f]

            # ---- 1. напоминания: окна, ключи, отметки, Telegram и Max -----------------------------------
            assert await reminders_step({}, now=at(timedelta(hours=24))) == {}   # процесс без бота whieda молчит
            assert await outbox("meeting_reminder") == []   # (заявки уже поставили партнёрам «Новая запись» — это другие строки)
            assert await reminders_step(bindings, now=at(timedelta(hours=27))) == {"meeting_reminders": 0, "meeting_after": 0}
            assert (await reminders_step(bindings, now=at(timedelta(hours=24, minutes=-1))))["meeting_reminders"] == 5
            day = await outbox("meeting_reminder")
            assert [r["idempotency_key"] for r in day] == [f"mrem:{r['id']}:day" for r in active]
            assert {r["status"] for r in day} == {"scheduled"}
            tg = day[0]["payload"]
            assert tg["chat_id"] == "5001" and tg["binding_id"] == "whieda-advisor-bot" and "reply_markup" not in tg
            assert "Завтра, " in tg["text"] and f"{as_msk(start):%H:%M}" in tg["text"] and "МСК" in tg["text"]
            max_row = next(r for r in day if r["idempotency_key"] == f"mrem:{reg_c['id']}:day")["payload"]
            assert max_row["channel"] == "max" and max_row["user_id"] == "5003" and "buttons" not in max_row
            # отметка в записи; отменённая запись и встреча в другой день напоминания не получили
            assert "day" in (await reg_row(reg_a["id"]))["reminders_sent"]
            assert (await reg_row(reg_e["id"]))["reminders_sent"] == {} and (await reg_row(reg_m2["id"]))["reminders_sent"] == {}
            # повторный проход, повтор того же окна и «ничья» ещё не пришло — новых строк нет
            assert (await reminders_step(bindings, now=at(timedelta(hours=24, minutes=-1))))["meeting_reminders"] == 0
            assert (await reminders_step(bindings, now=at(timedelta(hours=23, minutes=30))))["meeting_reminders"] == 0
            assert len(await outbox("meeting_reminder")) == 5
            # отметку потеряли, а ключ остался: второго сообщения нет
            admin("update meeting_registrations set reminders_sent = '{}'::jsonb where registration_id = %s", (reg_a["id"],))
            assert (await reminders_step(bindings, now=at(timedelta(hours=24, minutes=-2))))["meeting_reminders"] == 0
            assert len(await outbox("meeting_reminder")) == 5 and "day" in (await reg_row(reg_a["id"]))["reminders_sent"]

            assert (await reminders_step(bindings, now=at(timedelta(hours=3))))["meeting_reminders"] == 0
            assert (await reminders_step(bindings, now=at(timedelta(hours=2))))["meeting_reminders"] == 5
            assert (await reminders_step(bindings, now=at(timedelta(hours=1))))["meeting_reminders"] == 5
            hour_rows = [r for r in await outbox("meeting_reminder") if r["idempotency_key"].endswith(":hour")]
            assert len(hour_rows) == 5
            tg_hour = next(r for r in hour_rows if r["idempotency_key"] == f"mrem:{reg_a['id']}:hour")["payload"]
            assert tg_hour["reply_markup"] == {"inline_keyboard": [[{"text": "Присоединиться", "callback_data": f"mjoin:{reg_a['id']}"}]]}
            max_hour = next(r for r in hour_rows if r["idempotency_key"] == f"mrem:{reg_c['id']}:hour")["payload"]
            assert max_hour["buttons"] == [[{"type": "callback", "text": "Присоединиться", "payload": f"mjoin:{reg_c['id']}"}]]
            two = [r for r in await outbox("meeting_reminder") if r["idempotency_key"].endswith(":two_hours")]
            assert len(two) == 5 and all("reply_markup" not in r["payload"] and "buttons" not in r["payload"] for r in two)

            # ---- 2. «Присоединиться» ---------------------------------------------------------------------
            early = await registration.join("whieda", "telegram", 5002, reg_b["id"], now=at(timedelta(hours=3)))
            assert early.status == "too_early" and "МСК" in early.reply.text and early.reply.rows == []
            assert (await reg_row(reg_b["id"]))["status"] == "registered"
            foreign = await registration.join("whieda", "telegram", 5002, reg_a["id"], now=at(timedelta(minutes=30)))
            assert foreign.status == "stale" and (await reg_row(reg_a["id"]))["status"] == "registered"   # чужая запись
            wrong_channel = await registration.join("whieda", "max", 5001, reg_a["id"], now=at(timedelta(minutes=30)))
            assert wrong_channel.status == "stale"

            first = await registration.join("whieda", "telegram", 5001, reg_a["id"], now=at(timedelta(minutes=30)))
            assert first.status == "joined" and first.reply.rows == [[("▶️ Открыть встречу", ZOOM)]]
            row_a = await reg_row(reg_a["id"])
            assert row_a["status"] == "joined" and row_a["joined_at"] == at(timedelta(minutes=30))
            again = await registration.join("whieda", "telegram", 5001, reg_a["id"], now=at(timedelta(minutes=10)))
            assert again.status == "again" and again.reply.rows == first.reply.rows
            assert (await reg_row(reg_a["id"]))["joined_at"] == at(timedelta(minutes=30))   # время первого нажатия не меняется
            # joined больше напоминаний не получает: «за 5 минут» уходит остальным четырём
            assert (await reminders_step(bindings, now=at(timedelta(minutes=4))))["meeting_reminders"] == 4
            five = [r for r in await outbox("meeting_reminder") if r["idempotency_key"].endswith(":five")]
            assert {r["idempotency_key"] for r in five} == {f"mrem:{r['id']}:five" for r in (reg_b, reg_c, reg_d, reg_f)}

            via_max = await registration.join("whieda", "max", 5003, reg_c["id"], now=at(timedelta(minutes=3)))
            assert via_max.status == "joined" and via_max.reply.rows == [[("▶️ Открыть встречу", ZOOM)]]
            assert (await registration.join("whieda", "telegram", 5006, reg_f["id"], now=at(timedelta(minutes=2)))).status == "joined"
            # встреча без ссылки: нажатие в окне отмечает, ссылку обещают позже
            no_link = await registration.join("whieda", "telegram", 5001, reg_m2["id"], now=start2 - timedelta(minutes=5))
            assert no_link.status == "joined" and no_link.reply.rows == [] and "Ссылку пришлём" in no_link.reply.text
            # не в окне: после конца встречи
            late = await registration.join("whieda", "telegram", 5002, reg_b["id"], now=start + timedelta(minutes=61))
            assert late.status == "too_late" and (await reg_row(reg_b["id"]))["status"] == "registered"
            # отменённая встреча
            admin("update meetings set status = 'cancelled' where meeting_id = 2")
            gone = await registration.join("whieda", "telegram", 5001, reg_m2["id"], now=start2 - timedelta(minutes=5))
            assert gone.status == "cancelled"
            admin("update meetings set status = 'planned' where meeting_id = 2")

            # ---- 3. после встречи ------------------------------------------------------------------------
            after_now = start + timedelta(minutes=60 + 31)
            assert (await reminders_step(bindings, now=start + timedelta(minutes=89)))["meeting_after"] == 0   # 30 минут ещё не прошло
            assert await outbox("meeting_after") == [] and await outbox("meeting_summary") == []
            step = await reminders_step(bindings, now=after_now)
            assert step["meeting_after"] == 5 and step["meeting_reminders"] == 0

            statuses = {name: (await reg_row(r["id"]))["status"] for name, r in
                        {"a": reg_a, "b": reg_b, "c": reg_c, "d": reg_d, "f": reg_f, "e": reg_e}.items()}
            assert statuses == {"a": "attended", "b": "no_show", "c": "attended", "d": "no_show", "f": "attended", "e": "cancelled"}
            marks = ["after_sent" in (await reg_row(r["id"]))["reminders_sent"] for r in (reg_a, reg_b, reg_c, reg_d, reg_f)]
            assert marks == [True] * 5
            assert "after_sent" not in (await reg_row(reg_e["id"]))["reminders_sent"]
            assert (await rows("select status from meetings where meeting_id = 1"))[0]["status"] == "done"
            assert (await rows("select status from meetings where meeting_id = 2"))[0]["status"] == "planned"   # чужая встреча не тронута

            after = {r["idempotency_key"]: r for r in await outbox("meeting_after")}
            assert set(after) == {
                f"mafter:{reg_a['id']}:1", f"mafter:{reg_b['id']}:1", f"mafter:{reg_c['id']}:1", f"mafter:{reg_d['id']}:1",
                f"mafter:{reg_f['id']}:1", f"mafter:{reg_f['id']}:2",
            }
            # гость партнёра: его визитка, фото, контакты, кнопки (Telegram)
            card_a = after[f"mafter:{reg_a['id']}:1"]["payload"]
            assert card_a["chat_id"] == "5001" and card_a["card_photo"] == IGOR_PHOTO
            assert card_a["text"] == (
                "Спасибо, что были на Живой встрече!\nВаш партнёр WWC — Игорь Волков. Напишите ему: он поможет с первыми шагами.\n"
                "Телефон: +7 999 111-22-33\nПочта: igor@example.com"
            )
            assert [(b["text"], b["url"]) for row in card_a["reply_markup"]["inline_keyboard"] for b in row] == [
                ("Написать в Telegram", "https://t.me/igor_v"), ("Написать в Max", "https://max.ru/u/igor"), ("Сайт", "https://igor.wwc.best/"),
            ]
            # тот же партнёр, гость в Max: картинка по адресу и кнопки-ссылки
            card_c = after[f"mafter:{reg_c['id']}:1"]["payload"]
            assert card_c["channel"] == "max" and card_c["user_id"] == "5003" and card_c["image_url"] == IGOR_PHOTO
            assert [b["type"] for row in card_c["buttons"] for b in row] == ["link", "link", "link"]
            # гость с рекламы: две визитки подряд — владелец WWC, затем онлайн-Елена
            first_card = after[f"mafter:{reg_f['id']}:1"]
            second_card = after[f"mafter:{reg_f['id']}:2"]
            assert first_card["payload"]["text"] == "Спасибо, что были на Живой встрече!\nВиктор Хрипко — на связи по любым вопросам."
            assert [b["url"] for row in first_card["payload"]["reply_markup"]["inline_keyboard"] for b in row] == ["https://t.me/khripko"]
            assert second_card["payload"]["text"] == "Елена Онлайн — тоже на связи по любым вопросам."
            assert "card_photo" not in first_card["payload"] and "card_photo" not in second_card["payload"]
            assert [b["url"] for row in second_card["payload"]["reply_markup"]["inline_keyboard"] for b in row] == [
                "https://max.ru/u/elena", "https://elena.wwc.best"
            ]
            assert second_card["due_at"] > first_card["due_at"]
            # не пришли: запись и ближайшие встречи (встреча 2 — будущая, живая; отменённая 3 не предлагается)
            for reg in (reg_b, reg_d):
                miss = after[f"mafter:{reg['id']}:1"]["payload"]
                assert miss["text"].startswith("Вы записывались на Живую встречу") and "не дошли" in miss["text"]
                rows_flat = [b for row in miss["reply_markup"]["inline_keyboard"] for b in row]
                assert rows_flat[0] == {"text": "▶️ Смотреть запись", "url": RECORDING}
                assert [b["callback_data"] for b in rows_flat[1:]] == ["mreg:2"]
            assert RECORDING not in str(after[f"mafter:{reg_a['id']}:1"]["payload"])  # пришедшим запись не шлём

            # карточки CRM: метка, заметка, шаг «Напомнить о себе» на сегодня
            for name, tag_text in (("a", f"был {as_msk(start):%d.%m}"), ("c", f"был {as_msk(start):%d.%m}")):
                card = (await rows(
                    "select c.tags, c.status, c.next_step, c.next_at = (now() at time zone a.timezone)::date as today "
                    "from crm_contacts c join platform_accounts a on a.tenant_id = c.tenant_id and a.account_id = c.account_id "
                    "where c.contact_id = %s::uuid", (card_of[name],),
                ))[0]
                assert tag_text in card["tags"], name
                assert (card["status"], card["next_step"], card["today"]) == ("new", "ping", True), name
                notes = [r["body"] for r in await rows("select body from crm_notes where contact_id = %s::uuid order by created_at", (card_of[name],))]
                assert any(n.startswith("Был(а) на Живой встрече: ") for n in notes), name

            # итог партнёрам: по сообщению владельцу (у igor-actor — CRM открыта, кнопка; у «organic» — как есть)
            summaries = {r["idempotency_key"]: r["payload"] for r in await outbox("meeting_summary")}
            assert set(summaries) == {"msum:1:igor-actor", "msum:1:organic"}
            head = f"Живая встреча {format_msk(start, now=after_now)}:"
            igor_sum = summaries["msum:1:igor-actor"]
            assert igor_sum["chat_id"] == "7001" and igor_sum["binding_id"] == "whieda-advisor-bot"
            assert igor_sum["text"] == f"{head}\nБыли: Анна, Светлана."
            assert igor_sum["reply_markup"]["inline_keyboard"][0][0]["text"] == "📋 Открыть CRM"
            assert igor_sum["reply_markup"]["inline_keyboard"][0][0]["url"].endswith("/crm/") and igor_sum["site_login_user_id"] == 7001
            organic_sum = summaries["msum:1:organic"]
            assert organic_sum["chat_id"] == "7003"
            assert organic_sum["text"] == f"{head}\nБыли: Фёдор.\nЗаписались, но не пришли: Борис, из бота без заявки — 1."

            # повторный проход и позже окна — ничего нового
            before = len(await outbox())
            assert await reminders_step(bindings, now=after_now) == {"meeting_reminders": 0, "meeting_after": 0}
            assert await reminders_step(bindings, now=start + timedelta(hours=4)) == {"meeting_reminders": 0, "meeting_after": 0}
            assert len(await outbox()) == before

            # ---- 4. воркер отправляет строки: фото визитки одним сообщением, кнопки, Max --------------------
            admin("update platform_outbox set due_at = now() - interval '1 minute' where status = 'scheduled'")
            media = AsyncMock(return_value={"ok": True, "message_id": 1})
            text = AsyncMock(return_value={"ok": True})
            max_send = AsyncMock(return_value={"ok": True})
            with patch.object(worker, "send_telegram_media", media), patch.object(worker, "send_telegram_text", text), \
                    patch("app.max.outbox.send_max_message", max_send):
                sent = await worker.process_due_notifications(bindings, batch_size=100, budget_sec=60.0)
            assert sent == before
            assert {r["status"] for r in await outbox()} == {"done"}
            media.assert_awaited_once()   # фото с подписью — только у визитки партнёра Игоря (у остальных фото нет)
            assert media.await_args.args[:3] == ("5001", "photo", IGOR_PHOTO)
            assert media.await_args.kwargs["caption"] == card_a["text"]
            sent_texts = {call.kwargs["chat_id"] for call in text.await_args_list}
            assert {"5001", "5002", "5004", "5006", "7001", "7003"} <= sent_texts
            max_users = {call.kwargs["user_id"] for call in max_send.await_args_list}
            assert max_users == {"5003"}
            card_calls = [c for c in max_send.await_args_list if c.kwargs["attachments"]]
            assert len(card_calls) == 1 and card_calls[0].kwargs["attachments"] == [{"type": "image", "payload": {"url": IGOR_PHOTO}}]

        db.run_with_app(proof)
