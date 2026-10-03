"""WWC CRM v2 against real PostgreSQL (platform_crm_v20.sql, non-bypassrls API role).

1. V20 on a database that already has v1 cards and notes: run twice, the history
   is backfilled once («created» per card, «note» per note), old rows get defaults;
2. the card flow: «Сделано» through the steps, «Перенести», tap logging, notes,
   the history with cursors, tags and priority, server search (ILIKE with % and _
   typed as letters), sorts and cursors, the pipeline, soft delete / «Вернуть» /
   purge, templates (six defaults once), the install-banner flag, RLS;
3. «Через час встреча»: the window, no duplicates, a moved meeting, the account
   timezone, access, a deleted card; the worker sends it with a signed-in button.

No real Telegram call and no real sign-in link: send_telegram_text and
with_site_login are mocks.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, SQL_DIR, temporary_database
from tests.test_crm_postgres import LEAD_TABLES, SEED, _binding

ACC = "00000000-0000-4000-8000-000000000001"
OLD_DATA = f"""
insert into platform_accounts (tenant_id, account_id, telegram_user_id) values ('whieda', '{ACC}', 7001);
insert into crm_contacts (tenant_id, contact_id, account_id, name, created_at) values
  ('whieda', '00000000-0000-4000-8000-0000000000a1', '{ACC}', 'Анна', now() - interval '3 days'),
  ('whieda', '00000000-0000-4000-8000-0000000000a2', '{ACC}', 'Борис', now() - interval '2 days');
insert into crm_contacts (tenant_id, contact_id, account_id, name, lead_id) values
  ('whieda', '00000000-0000-4000-8000-0000000000a3', '{ACC}', 'Заявка с сайта', gen_random_uuid());
insert into crm_notes (tenant_id, contact_id, body, created_at) values
  ('whieda', '00000000-0000-4000-8000-0000000000a1', 'первая', now() - interval '2 days'),
  ('whieda', '00000000-0000-4000-8000-0000000000a1', 'вторая', now() - interval '1 day');
"""


def _clean_env(monkeypatch) -> None:
    for name in ("PLATFORM_DISABLED_FEATURES", "PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", "PLATFORM_BILLING_OWNER_TELEGRAM_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "*")
    monkeypatch.setenv("PLATFORM_ORGANIC_OWNER_ID", "organic")
    monkeypatch.setenv("PLATFORM_CRM_LEAD_CARDS", "false")


@pytest.mark.integration
def test_v20_twice_on_v1_data_backfills_the_history_once(monkeypatch):
    _clean_env(monkeypatch)
    with temporary_database("whieda_crm_v20") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, names=[name for name in MIGRATIONS if name != "platform_crm_v20.sql"])
            conn.execute(SEED)
            conn.execute(OLD_DATA)
            v20 = (SQL_DIR / "platform_crm_v20.sql").read_text(encoding="utf-8")
            conn.execute(v20)
            first = conn.execute(
                "select kind, contact_id::text, payload, created_at from crm_activities order by kind, contact_id, created_at"
            ).fetchall()
            conn.execute(v20)  # the second run adds nothing and breaks nothing
            second = conn.execute(
                "select kind, contact_id::text, payload, created_at from crm_activities order by kind, contact_id, created_at"
            ).fetchall()
            assert first == second
            kinds = [row[0] for row in first]
            assert kinds.count("created") == 3 and kinds.count("note") == 2
            created = {row[1]: row for row in first if row[0] == "created"}
            assert created["00000000-0000-4000-8000-0000000000a3"][2] == {"source": "site", "backfill": True}
            assert created["00000000-0000-4000-8000-0000000000a1"][2] == {"backfill": True}
            # The history keeps the real dates: a card's «created» is its created_at.
            assert conn.execute(
                "select bool_and(a.created_at = c.created_at) from crm_activities a "
                "join crm_contacts c on c.contact_id = a.contact_id where a.kind = 'created'"
            ).fetchone() == (True,)
            defaults = conn.execute(
                "select distinct tags, priority, last_touch_at, meeting_reminded_at, deleted_at from crm_contacts"
            ).fetchall()
            assert defaults == [([], 0, None, None, None)]
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("update crm_contacts set priority = 2")
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("insert into crm_activities (tenant_id, account_id, contact_id, kind) "
                             f"values ('whieda', '{ACC}', '00000000-0000-4000-8000-0000000000a1', 'tags')")
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.crm import service as crm
            from app.crm.queries import list_activities

            igor = await crm.get_or_create_account("whieda", 7001)
            feed = await list_activities("whieda", igor, "00000000-0000-4000-8000-0000000000a1")
            assert [(item["kind"], (item.get("note") or {}).get("body")) for item in feed["items"]] == [
                ("note", "вторая"), ("note", "первая"), ("created", None)]

        db.run_with_app(proof)


@pytest.mark.integration
def test_crm_v2_cards_lists_templates_and_meeting_reminders(monkeypatch):
    _clean_env(monkeypatch)
    with temporary_database("whieda_crm_v2") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, twice=True)  # V20 is idempotent
            conn.execute(LEAD_TABLES)
            conn.execute(SEED)
            # 7003: opened the CRM once, but has no partner profile (no PRO) — no reminders.
            conn.execute("insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) "
                         "values ('nopro-actor', 'whieda', 'Без PRO', '7003')")
            conn.execute("update lead_actors set telegram_user_id = 7003 where actor_id = 'nopro-actor'")
            db.grant_api_role(conn)

        def admin(statement: str) -> None:
            with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
                conn.execute(statement)

        async def proof() -> None:
            from app.crm import service as crm
            from app.crm import templates as tpl
            from app.crm.meetings import enqueue_meeting_reminders
            from app.crm.queries import list_activities, list_contacts, list_tags, pipeline, pipeline_column
            from app.db import fetch_all, tenant_connection
            from app.jobs.worker import process_due_notifications

            async def rows(query: str, params: tuple = (), tenant: str = "whieda") -> list[dict]:
                async with tenant_connection(tenant) as conn:
                    return [dict(r) for r in await fetch_all(conn, query, params)]

            async def kinds(contact_id: str) -> list[str]:
                return [item["kind"] for item in (await list_activities("whieda", igor, contact_id))["items"]]

            igor = await crm.get_or_create_account("whieda", 7001)
            petr = await crm.get_or_create_account("whieda", 7002)
            today = igor["today"]

            # ---- «Сделано» through the steps, «Перенести», history -------------------
            anna = await crm.create_contact("whieda", igor, name="Анна", phone="+7 928 672-92-88",
                                            tags=["#VIP", "vip", "Минск"], priority=True)
            assert (anna["tags"], anna["priority"], anna["last_touch_at"]) == (["VIP", "Минск"], 1, None)
            with pytest.raises(crm.CrmError) as no_meeting:
                await crm.done_contact("whieda", igor, anna["id"])
            assert (no_meeting.value.status, no_meeting.value.code) == (400, "meeting_at_required")

            meeting = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=2)
            invited = await crm.done_contact("whieda", igor, anna["id"], meeting_at=meeting)
            assert (invited["status"], invited["next_step"]) == ("invited", "result")
            assert invited["meeting_at"] is not None and invited["last_touch_at"] is not None
            assert await kinds(anna["id"]) == ["meeting", "status", "step", "created"]
            presented = await crm.done_contact("whieda", igor, anna["id"])
            assert (presented["status"], presented["next_step"], presented["next_at"]) == (
                "presented", "decide", (today + timedelta(days=2)).isoformat())
            deciding = await crm.done_contact("whieda", igor, anna["id"])
            assert (deciding["status"], deciding["next_step"], deciding["next_at"]) == ("deciding", "ping", None)
            snoozed = await crm.snooze_contact("whieda", igor, anna["id"], days=3)
            assert (snoozed["next_step"], snoozed["next_at"]) == ("ping", (today + timedelta(days=3)).isoformat())
            moved = await crm.snooze_contact("whieda", igor, anna["id"], on=today)
            assert moved["next_at"] == today.isoformat()
            for bad in ({"days": 0}, {"on": today - timedelta(days=1)}, {}):
                with pytest.raises(crm.CrmError) as snooze_error:
                    await crm.snooze_contact("whieda", igor, anna["id"], **bad)
                assert snooze_error.value.status == 400
            history = (await list_activities("whieda", igor, anna["id"]))["items"]
            assert history[0]["payload"] == {"action": "snooze", "step": "ping",
                                             "from": (today + timedelta(days=3)).isoformat(), "at": today.isoformat()}
            status_changes = [item["payload"] for item in history if item["kind"] == "status"]
            assert status_changes == [{"from": "presented", "to": "deciding"}, {"from": "invited", "to": "presented"},
                                      {"from": "new", "to": "invited"}]

            # PATCH: status → «status», an explicit date → «step», a new meeting → «meeting» + touch.
            patched = await crm.update_contact("whieda", igor, anna["id"], {"status": "client"})
            assert (patched["next_step"], patched["next_at"]) == ("ping", (today + timedelta(days=30)).isoformat())
            await crm.update_contact("whieda", igor, anna["id"], {"next_at": today + timedelta(days=5)})
            await crm.update_contact("whieda", igor, anna["id"], {"tags": ["клиент"], "priority": 0})
            assert (await kinds(anna["id"]))[:2] == ["step", "status"]  # tags/priority leave no row

            # Taps: a call, a message by template; last_touch_at moves.
            templates = await tpl.list_templates("whieda", igor)
            assert [t["title"] for t in templates][:2] == ["Приглашение", "Напоминание о встрече"]
            assert [t["position"] for t in templates] == [1, 2, 3, 4, 5, 6]
            assert len(await tpl.list_templates("whieda", igor)) == 6  # seeded once
            call = await crm.log_contact("whieda", igor, anna["id"], kind="call")
            assert call["activity"]["payload"] == {"channel": "phone"}
            message = await crm.log_contact("whieda", igor, anna["id"], kind="message", channel="whatsapp",
                                            template_id=templates[0]["id"])
            assert message["activity"]["payload"] == {"channel": "whatsapp", "template_id": templates[0]["id"],
                                                      "template_title": "Приглашение"}
            assert message["contact"]["last_touch_at"] >= call["contact"]["last_touch_at"]
            petr_templates = await tpl.list_templates("whieda", petr)
            with pytest.raises(crm.CrmError) as foreign_template:
                await crm.log_contact("whieda", igor, anna["id"], kind="message", channel="viber",
                                      template_id=petr_templates[0]["id"])
            assert (foreign_template.value.status, foreign_template.value.code) == (404, "template_not_found")
            with pytest.raises(crm.CrmError) as bad_channel:
                await crm.log_contact("whieda", igor, anna["id"], kind="message", channel="pigeon")
            assert bad_channel.value.code == "invalid_channel"

            # Notes are in the history with their text; a deleted note leaves no row.
            note = await crm.add_note("whieda", igor, anna["id"], "Позвонить после отпуска")
            feed = (await list_activities("whieda", igor, anna["id"], limit=2))
            assert feed["items"][0]["kind"] == "note" and feed["items"][0]["note"]["body"] == "Позвонить после отпуска"
            seen = [item["id"] for item in feed["items"]]
            cursor = feed["next_cursor"]
            while cursor:
                more = await list_activities("whieda", igor, anna["id"], cursor=cursor, limit=2)
                seen += [item["id"] for item in more["items"]]
                cursor = more["next_cursor"]
            everything = (await list_activities("whieda", igor, anna["id"], limit=100))["items"]
            assert seen == [item["id"] for item in everything] and len(set(seen)) == len(seen)
            await crm.delete_note("whieda", igor, anna["id"], note["id"])
            assert "note" not in await kinds(anna["id"])
            with pytest.raises(crm.CrmError) as bad_cursor:
                await list_activities("whieda", igor, anna["id"], cursor="garbage")
            assert (bad_cursor.value.status, bad_cursor.value.code) == (400, "invalid_cursor")

            # ---- search, filters, sorts, cursors ---------------------------------------
            for name, phone, source in (("Борис_1", "+7 900 000-00-01", "спортзал"),
                                        ("Вера 100%", "+7 900 000-00-02", "соседка"),
                                        ("вероника", None, "работа"), ("Глеб", "8 (900) 000-00-03", "")):
                await crm.create_contact("whieda", igor, name=name, phone=phone, source=source)

            async def found(**kwargs) -> list[str]:
                return [c["name"] for c in (await list_contacts("whieda", igor, sort="name", **kwargs))["items"]]

            assert await found(q="ВЕР") == ["Вера 100%", "вероника"]
            assert await found(q="%") == ["Вера 100%"]          # a typed % is a letter
            assert await found(q="_") == ["Борис_1"]           # and so is _
            assert await found(q="спорт") == ["Борис_1"]       # source
            assert await found(q="900 000-00-03") == ["Глеб"]  # its digits match +79000000003
            assert await found(q="(9") == ["Глеб"]             # phone_raw as typed (under 3 digits)
            assert await found(q="900") == ["Борис_1", "Вера 100%", "Глеб"]  # 3+ digits search numbers
            assert await found(q="9000000003") == ["Глеб"]
            assert await found(tag="клиент") == ["Анна"]
            assert await found(status="client") == ["Анна"]
            with pytest.raises(crm.CrmError) as bad_sort:
                await list_contacts("whieda", igor, sort="age")
            assert bad_sort.value.code == "invalid_sort"

            pages, cursor = [], None
            while True:
                result = await list_contacts("whieda", igor, sort="name", limit=2, cursor=cursor)
                assert result["total"] == 5
                pages.append([c["name"] for c in result["items"]])
                cursor = result["next_cursor"]
                if not cursor:
                    break
            assert pages == [["Анна", "Борис_1"], ["Вера 100%", "вероника"], ["Глеб"]]
            from app.crm.paging import encode_cursor

            with pytest.raises(crm.CrmError) as erased_card:  # the «by name» cursor is only a card id
                await list_contacts("whieda", igor, sort="name", cursor=encode_cursor(
                    "name", ["6f1c3f7e-3f0a-4a52-9b1e-2d6a1c5e9f00"]))
            assert (erased_card.value.status, erased_card.value.code) == (400, "invalid_cursor")
            by_next = await list_contacts("whieda", igor, sort="next", limit=10)
            dates = [c["next_at"] for c in by_next["items"]]
            assert dates == sorted(d for d in dates if d) + [None] * dates.count(None)
            updated = await list_contacts("whieda", igor, limit=2)
            second = await list_contacts("whieda", igor, limit=2, cursor=updated["next_cursor"])
            assert not {c["id"] for c in updated["items"]} & {c["id"] for c in second["items"]}
            with pytest.raises(crm.CrmError):
                await list_contacts("whieda", igor, sort="name", cursor=updated["next_cursor"])  # another list

            assert await list_tags("whieda", igor) == [{"tag": "клиент", "count": 1}]

            # ---- pipeline ---------------------------------------------------------------------
            await crm.bulk_create_contacts("whieda", igor, [{"name": f"Импорт {i:02d}"} for i in range(20)])
            star = (await list_contacts("whieda", igor, q="Импорт 00"))["items"][0]
            await crm.update_contact("whieda", igor, star["id"], {"priority": 1})
            board = await pipeline("whieda", igor)
            assert [column["status"] for column in board["columns"]] == [
                "new", "invited", "presented", "deciding", "client", "partner", "paused"]
            new_column = board["columns"][0]
            assert new_column["count"] == 24 and len(new_column["items"]) == 20 and new_column["next_cursor"]
            assert new_column["items"][0]["id"] == star["id"]  # starred first
            assert board["columns"][4]["count"] == 1 and board["total"] == 25
            rest = await pipeline_column("whieda", igor, "new", cursor=new_column["next_cursor"])
            assert len(rest["items"]) == 4 and rest["next_cursor"] is None and rest["total"] == 24
            assert not {c["id"] for c in rest["items"]} & {c["id"] for c in new_column["items"]}

            # ---- soft delete, «Вернуть», purge -----------------------------------------------
            boris = (await list_contacts("whieda", igor, q="Борис"))["items"][0]
            await crm.delete_contact("whieda", igor, boris["id"])
            assert await found(q="Борис") == []
            assert "Борис" not in await crm.export_csv("whieda", igor)
            with pytest.raises(crm.CrmError):
                await crm.delete_contact("whieda", igor, boris["id"])
            twin = await crm.create_contact("whieda", igor, name="Борис второй", phone="+7 900 000-00-01")
            with pytest.raises(crm.CrmError) as taken:
                await crm.restore_contact("whieda", igor, boris["id"])
            assert (taken.value.status, taken.value.code, taken.value.extra) == (409, "duplicate", {"contact_id": twin["id"]})
            await crm.delete_contact("whieda", igor, twin["id"])
            restored = await crm.restore_contact("whieda", igor, boris["id"])
            assert restored["name"] == "Борис_1"
            assert (await crm.restore_contact("whieda", igor, boris["id"]))["id"] == boris["id"]  # a double tap
            with pytest.raises(crm.CrmError) as foreign_restore:
                await crm.restore_contact("whieda", petr, boris["id"])
            assert foreign_restore.value.status == 404
            admin(f"update crm_contacts set deleted_at = now() - interval '25 hours' where contact_id = '{twin['id']}'")
            with pytest.raises(crm.CrmError) as too_late:
                await crm.restore_contact("whieda", igor, twin["id"])
            assert too_late.value.status == 404
            assert await crm.purge_deleted_contacts("whieda") == 1
            assert await rows("select 1 from crm_activities where contact_id = %s::uuid", (twin["id"],)) == []

            # ---- templates ----------------------------------------------------------------------
            await tpl.delete_template("whieda", igor, templates[5]["id"])
            assert len(await tpl.list_templates("whieda", igor)) == 5  # a deleted default does not return
            own = await tpl.create_template("whieda", igor, title=" Мой  шаблон ", body="{имя}, привет!")
            assert (own["title"], own["position"]) == ("Мой шаблон", 6)
            renamed = await tpl.update_template("whieda", igor, own["id"], {"title": "Новое имя", "position": 0})
            assert (renamed["title"], renamed["position"]) == ("Новое имя", 0)
            assert (await tpl.list_templates("whieda", igor))[0]["id"] == own["id"]
            for call_ in (tpl.update_template("whieda", petr, own["id"], {"title": "x"}),
                          tpl.delete_template("whieda", petr, own["id"]),
                          tpl.delete_template("whieda", igor, "not-a-uuid")):
                with pytest.raises(crm.CrmError) as foreign:
                    await call_
                assert (foreign.value.status, foreign.value.code) == (404, "template_not_found")
            with pytest.raises(crm.CrmError) as empty_body:
                await tpl.create_template("whieda", igor, title="x", body="  ")
            assert empty_body.value.code == "body_required"

            # ---- me: install banner ---------------------------------------------------------------
            assert igor["install_hint_dismissed"] is False
            await crm.update_account("whieda", igor, install_hint_dismissed=True)
            assert (await crm.get_or_create_account("whieda", 7001))["install_hint_dismissed"] is True
            back = await crm.update_account("whieda", igor, install_hint_dismissed=False)
            assert back["install_hint_dismissed"] is False and back["timezone"] == "Europe/Moscow"

            # ---- «Сегодня»: a meeting today is listed under meetings ----------------------------
            gleb = (await list_contacts("whieda", igor, q="Глеб"))["items"][0]
            await crm.update_contact("whieda", igor, gleb["id"], {"status": "presented",
                                                                  "meeting_at": datetime.now(timezone.utc)})
            view = await crm.today_view("whieda", igor)
            sections = {section["key"]: [c["name"] for c in section["contacts"]] for section in view["sections"]}
            assert sections["meetings"] == ["Глеб"]
            assert "Глеб" not in sum((names for key, names in sections.items() if key != "meetings"), [])
            assert {group["step"] for group in view["groups"]} >= {"invite"}  # v1 groups are still there

            # ---- «Через час встреча» ------------------------------------------------------------------
            now = datetime.now(timezone.utc).replace(microsecond=0)
            soon = await crm.create_contact("whieda", igor, name="Дина <b>", phone="+7 911 000-00-01")
            await crm.update_contact("whieda", igor, soon["id"], {"meeting_at": now + timedelta(minutes=60)})
            for minutes, name in ((120, "Через два часа"), (30, "Через полчаса"), (-10, "Уже прошла")):
                other = await crm.create_contact("whieda", igor, name=name)
                await crm.update_contact("whieda", igor, other["id"], {"meeting_at": now + timedelta(minutes=minutes)})
            gone = await crm.create_contact("whieda", igor, name="Удалённая")
            await crm.update_contact("whieda", igor, gone["id"], {"meeting_at": now + timedelta(minutes=58)})
            await crm.delete_contact("whieda", igor, gone["id"])
            # Пётр lives in Yekaterinburg (UTC+5, no DST): the time is his local one.
            petr = await crm.set_timezone("whieda", petr, "Asia/Yekaterinburg")
            petr_card = await crm.create_contact("whieda", petr, name="Клиент Петра")
            await crm.update_contact("whieda", petr, petr_card["id"], {"meeting_at": now + timedelta(minutes=55)})
            nopro = await crm.get_or_create_account("whieda", 7003)
            nopro_card = await crm.create_contact("whieda", nopro, name="Клиент без PRO")
            await crm.update_contact("whieda", nopro, nopro_card["id"], {"meeting_at": now + timedelta(minutes=60)})

            assert await enqueue_meeting_reminders({"whieda": "whieda-advisor-bot"}) == 2
            assert await enqueue_meeting_reminders({"whieda": "whieda-advisor-bot"}) == 0  # no duplicates
            queued = await rows("select idempotency_key, payload, status from platform_outbox "
                                "where event_type = 'crm_meeting_reminder' order by payload ->> 'chat_id'")
            assert [row["payload"]["chat_id"] for row in queued] == ["7001", "7002"]
            dina = queued[0]
            dina_meeting = now + timedelta(minutes=60)
            moscow = (dina_meeting + timedelta(hours=3)).strftime("%H:%M")
            assert dina["payload"]["text"] == (
                f"⏰ Через час встреча: Дина ‹b›, +79110000001\nНачало в {moscow} по вашему времени.")
            assert dina["idempotency_key"] == f"crm_meeting:{soon['id']}:{int(dina_meeting.timestamp())}"
            assert dina["status"] == "scheduled" and dina["payload"]["site_login_user_id"] == 7001
            assert dina["payload"]["reply_markup"]["inline_keyboard"][0][0] == {
                "text": "👤 Открыть карточку",
                "url": f"https://igor.wwc.best/crm/?contact={soon['id']}#contact/{soon['id']}"}
            yekaterinburg = (now + timedelta(minutes=55) + timedelta(hours=5)).strftime("%H:%M")
            assert queued[1]["payload"]["text"].endswith(f"Начало в {yekaterinburg} по вашему времени.")
            reminded = await rows("select name from crm_contacts where meeting_reminded_at is not null order by name")
            assert reminded == [{"name": "Дина <b>"}, {"name": "Клиент Петра"}]

            # A moved meeting gets its own reminder; an unchanged one never a second.
            await crm.update_contact("whieda", igor, soon["id"], {"meeting_at": now + timedelta(minutes=62)})
            assert await rows("select meeting_reminded_at from crm_contacts where contact_id = %s::uuid",
                              (soon["id"],)) == [{"meeting_reminded_at": None}]
            assert await enqueue_meeting_reminders({"whieda": "whieda-advisor-bot"}) == 1
            keys = await rows("select idempotency_key from platform_outbox where event_type = 'crm_meeting_reminder' "
                              "and payload ->> 'contact_id' = %s order by created_at", (soon["id"],))
            assert len({row["idempotency_key"] for row in keys}) == 2

            send = AsyncMock(return_value={"ok": True, "message_id": 7})
            login = AsyncMock(side_effect=lambda url, **_: url.split("#", 1)[0] + "#wwc-login=t")
            with patch("app.jobs.worker.send_telegram_text", send), patch("app.telegram.site_login.with_site_login", login):
                assert await process_due_notifications({"whieda": _binding()}) == 3
            sent = {call_.kwargs["chat_id"]: call_.kwargs for call_ in send.await_args_list}
            assert set(sent) == {"7001", "7002"}
            button = sent["7001"]["reply_markup"]["inline_keyboard"][0][0]
            assert button["url"] == f"https://igor.wwc.best/crm/?contact={soon['id']}#wwc-login=t"
            assert {call_.kwargs["telegram_user_id"] for call_ in login.await_args_list} == {7001, 7002}
            done = await rows("select status from platform_outbox where event_type = 'crm_meeting_reminder'")
            assert {row["status"] for row in done} == {"done"}

            # ---- RLS: another tenant sees none of it ----------------------------------------------
            for table in ("crm_activities", "crm_templates", "crm_contacts"):
                assert await rows(f"select 1 from {table}", tenant="other") == []

        db.run_with_app(proof)
