"""CRM v1 against real PostgreSQL (platform_crm_v14.sql, non-bypassrls API role).

1. contact → «Сегодня» → status change applies the rule → note → export → delete;
   another account sees nothing (404), another tenant sees nothing (RLS);
2. a site lead becomes a «Новый контакт» card of its owner; a broken hook does
   not lose the lead;
3. the morning message: one outbox row per account and day, the old outbox
   worker leaves it alone, process_due_notifications sends it (Telegram mocked)
   and marks it done.

No real Telegram or n8n call is made: send_telegram_text and trigger_lead_delivery
are mocks.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import psycopg
import pytest
from psycopg import sql

from tests.postgres_testkit import temporary_database

LEAD_TABLES = """
create table service_locations (
  service_location_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  country_code text not null,
  city text not null,
  operator_actor_id text,
  enabled boolean not null default false,
  verified_at timestamptz,
  created_at timestamptz not null default now()
);

create table website_leads (
  lead_id uuid primary key default gen_random_uuid(),
  public_id text not null default ('L-' || upper(substr(replace(gen_random_uuid()::text, '-', ''), 1, 8))),
  tenant_id text not null,
  name text not null,
  contact text not null,
  comment text,
  product_name text not null,
  product_sku text,
  product_variant text,
  page_url text,
  initial_ref_code text,
  first_ref_code text,
  active_ref_code text,
  attributed_owner_id text not null,
  assigned_owner_id text not null,
  ref_profile_version integer,
  service_location_id uuid,
  country_code text,
  city text,
  idempotency_key text not null,
  consent_version text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (tenant_id, idempotency_key)
);

create table website_lead_owner_history (
  id bigserial primary key, lead_id uuid not null, tenant_id text not null, owner_id text not null,
  action text not null, changed_by text not null, reason text
);

create table website_lead_status_history (
  id bigserial primary key, lead_id uuid not null, tenant_id text not null, old_status text,
  new_status text not null, changed_by_actor_id text, reason text
);
"""

SEED = """
insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) values
  ('igor-actor', 'whieda', 'Игорь', '7001'),
  ('petr-actor', 'whieda', 'Пётр', '7002'),
  ('organic', 'whieda', 'Organic', null);
update lead_actors set telegram_user_id = 7001 where actor_id = 'igor-actor';
update lead_actors set telegram_user_id = 7002 where actor_id = 'petr-actor';
insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, public_profile, enabled) values
  ('igor', 'whieda', 'igor-actor', 'named', '{"subdomain": "igor"}', true),
  ('petr', 'whieda', 'petr-actor', 'named', '{"subdomain": "petr"}', true);
insert into partner_subscriptions (tenant_id, ref_code, paid_until) values
  ('whieda', 'igor', now() + interval '30 days'),
  ('whieda', 'petr', now() + interval '30 days');
insert into tenants (tenant_id, display_name, status) values ('other', 'Other', 'active')
  on conflict (tenant_id) do nothing;
"""


def _binding():
    from app.telegram.bindings import BotBindingContext
    from app.tenancy import TenantContext

    return BotBindingContext(
        binding_id="whieda-advisor-bot",
        tenant=TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"crm": True}),
        bot_token_ref="env:TEST_TOKEN",
        webhook_secret_ref="env:TEST_SECRET",
        bot_username="test_bot",
        status="active",
        processing_mode="core",
        bot_token="test-token",
        webhook_secret="test-secret",
    )


@pytest.mark.integration
def test_crm_contact_lifecycle_lead_card_and_morning_message(monkeypatch):
    for name in ("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "PLATFORM_DISABLED_FEATURES", "PLATFORM_ADMIN_SUPER_TELEGRAM_IDS",
                 "PLATFORM_BILLING_OWNER_TELEGRAM_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PLATFORM_ORGANIC_OWNER_ID", "organic")

    with temporary_database("whieda_crm") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, twice=True)  # V14 is idempotent
            conn.execute(LEAD_TABLES)
            conn.execute(SEED)
            db.grant_api_role(conn)
            entitlement = conn.execute(
                "select enabled from tenant_entitlements where tenant_id = 'whieda' and feature_key = 'crm'"
            ).fetchone()
            assert entitlement == (True,)
            outbox_owner_check = conn.execute(
                "select count(*) from information_schema.columns where table_name = 'platform_outbox' and column_name = 'due_at'"
            ).fetchone()
            assert outbox_owner_check == (1,)

        def admin(statement) -> None:
            with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
                conn.execute(statement)

        async def proof() -> None:
            from app.crm import service as crm
            from app.crm.digest import enqueue_crm_digests
            from app.db import fetch_all, fetch_one, tenant_connection
            from app.jobs.outbox import enqueue_outbox_event
            from app.jobs.worker import process_due_notifications, process_pending_outbox
            from app.leads.service import parse_lead_body, save_lead
            from app.telegram.delivery import TelegramDeliveryError

            async def rows(query: str, params: tuple = (), tenant: str = "whieda") -> list[dict]:
                async with tenant_connection(tenant) as conn:
                    return [dict(r) for r in await fetch_all(conn, query, params)]

            # ---- 1. contact lifecycle -------------------------------------------------
            igor = await crm.get_or_create_account("whieda", 7001)
            assert (await crm.get_or_create_account("whieda", 7001))["account_id"] == igor["account_id"]
            assert igor["timezone"] == "Europe/Moscow"
            today = igor["today"]

            anna = await crm.create_contact("whieda", igor, name=" Анна  Петрова ", phone="8 928 672-92-88", source="соседка")
            assert anna["name"] == "Анна Петрова"
            assert anna["phone_e164"] == "+79286729288" and anna["phone_raw"] == "8 928 672-92-88"
            assert (anna["status"], anna["next_step"], anna["next_at"]) == ("new", "invite", today.isoformat())
            boris = await crm.create_contact("whieda", igor, name="Борис", phone="12-34")
            assert boris["phone_e164"] is None and boris["phone"] == "12-34"

            with pytest.raises(crm.CrmError) as dup:
                await crm.create_contact("whieda", igor, name="Аня", phone="+7 (928) 672-92-88")
            assert (dup.value.status, dup.value.code, dup.value.extra) == (409, "duplicate", {"contact_id": anna["id"]})

            view = await crm.today_view("whieda", igor)
            assert [g["step"] for g in view["groups"]] == ["invite"]
            assert {c["name"] for c in view["groups"][0]["contacts"]} == {"Анна Петрова", "Борис"}
            assert view["overdue"] == 0

            presented = await crm.update_contact("whieda", igor, anna["id"], {"status": "presented"})
            assert (presented["next_step"], presented["next_at"]) == ("decide", (today + timedelta(days=2)).isoformat())
            view = await crm.today_view("whieda", igor)
            assert {c["name"] for g in view["groups"] for c in g["contacts"]} == {"Борис"}

            with pytest.raises(crm.CrmError) as missing_meeting:
                await crm.update_contact("whieda", igor, anna["id"], {"status": "invited"})
            assert missing_meeting.value.code == "meeting_at_required"
            # 21:30 UTC = 00:30 next day in Moscow: the rule takes the partner's date.
            meeting = datetime(2026, 10, 1, 21, 30, tzinfo=timezone.utc)
            invited = await crm.update_contact("whieda", igor, anna["id"], {"status": "invited", "meeting_at": meeting})
            assert (invited["next_step"], invited["next_at"]) == ("result", "2026-10-02")
            manual = await crm.update_contact(
                "whieda", igor, anna["id"], {"status": "client", "next_step": "ping", "next_at": today}
            )
            assert (manual["status"], manual["next_step"], manual["next_at"]) == ("client", "ping", today.isoformat())

            note = await crm.add_note("whieda", igor, anna["id"], "Была на презентации, думает")
            detail = await crm.get_contact("whieda", igor, anna["id"])
            assert [n["body"] for n in detail["notes"]] == ["Была на презентации, думает"]

            assert [c["name"] for c in await crm.list_contacts("whieda", igor, q="928")] == ["Анна Петрова"]
            assert [c["name"] for c in await crm.list_contacts("whieda", igor, q="анна")] == ["Анна Петрова"]
            assert [c["name"] for c in await crm.list_contacts("whieda", igor, q="соседка")] == ["Анна Петрова"]
            assert [c["name"] for c in await crm.list_contacts("whieda", igor, status="new")] == ["Борис"]

            csv_text = await crm.export_csv("whieda", igor)
            assert csv_text.startswith("﻿Имя;Телефон;Откуда знакомы;Статус;Следующий шаг;Дата;Заметки\r\n")
            assert "Анна Петрова;'+79286729288;соседка;Клиент;Напомнить о себе;" in csv_text
            assert "Была на презентации, думает" in csv_text

            petr = await crm.get_or_create_account("whieda", 7002)
            for call in (
                crm.get_contact("whieda", petr, anna["id"]),
                crm.update_contact("whieda", petr, anna["id"], {"name": "x"}),
                crm.add_note("whieda", petr, anna["id"], "x"),
                crm.delete_contact("whieda", petr, anna["id"]),
                crm.get_contact("whieda", igor, "not-a-uuid"),
            ):
                with pytest.raises(crm.CrmError) as foreign:
                    await call
                assert foreign.value.status == 404
            assert await crm.list_contacts("whieda", petr) == []
            assert await rows("select contact_id from crm_contacts", tenant="other") == []  # RLS

            moved = await crm.set_timezone("whieda", igor, "Asia/Yekaterinburg")
            assert moved["timezone"] == "Asia/Yekaterinburg"
            for bad in ("Mars/Olympus", "Europe/Moscow'; drop table crm_notes; --"):
                with pytest.raises(crm.CrmError) as tz_error:
                    await crm.set_timezone("whieda", igor, bad)
                assert tz_error.value.code == "invalid_timezone"
            igor = await crm.set_timezone("whieda", igor, "Europe/Moscow")

            await crm.delete_note("whieda", igor, anna["id"], note["id"])
            assert (await crm.get_contact("whieda", igor, anna["id"]))["notes"] == []
            await crm.add_note("whieda", igor, anna["id"], "ещё одна")
            await crm.delete_contact("whieda", igor, anna["id"])
            with pytest.raises(crm.CrmError):
                await crm.get_contact("whieda", igor, anna["id"])
            assert await rows("select note_id from crm_notes where contact_id = %s::uuid", (anna["id"],)) == []

            # ---- 2. site lead → card --------------------------------------------------
            def lead(key: str, contact: str, ref: str = "igor", name: str = "Ольга") -> object:
                return parse_lead_body(
                    {"name": name, "contact": contact, "product": "Стельки", "comment": "вечером",
                     "idempotency_key": key, "initial_ref": ref, "active_ref": ref},
                    tenant_id="whieda",
                )

            saved = await save_lead(lead("crm-lead-1", "+7 999 111-22-33"))
            assert saved["assigned_owner_id"] == "igor-actor"
            cards = await rows(
                "select contact_id::text as id, name, phone_e164, source, status, next_step, next_at, lead_id::text as lead_id "
                "from crm_contacts where lead_id is not null"
            )
            assert len(cards) == 1
            card = cards[0]
            assert (card["name"], card["phone_e164"], card["source"], card["status"], card["next_step"]) == (
                "Ольга", "+79991112233", "сайт", "new", "invite"
            )
            assert card["next_at"] == igor["today"] and card["lead_id"] == str(saved["lead_id"])
            notes = await rows("select body from crm_notes where contact_id = %s::uuid", (card["id"],))
            assert notes == [{"body": "Заявка с сайта.\nИнтерес: Стельки\nКомментарий: вечером"}]

            await save_lead(lead("crm-lead-1", "+7 999 111-22-33"))  # same request again
            await save_lead(lead("crm-lead-2", "8 999 111 22 33"))  # same person, new request
            assert len(await rows("select 1 from crm_contacts where phone_e164 = '+79991112233'")) == 1
            assert len(await rows("select 1 from crm_notes where contact_id = %s::uuid", (card["id"],))) == 2

            await save_lead(lead("crm-lead-3", "@olga_tg", name="Ольга 2"))
            handle_card = await rows("select contact_id::text as id, phone_e164 from crm_contacts where name = 'Ольга 2'")
            assert handle_card and handle_card[0]["phone_e164"] is None
            handle_note = await rows("select body from crm_notes where contact_id = %s::uuid", (handle_card[0]["id"],))
            assert "Контакт: @olga_tg" in handle_note[0]["body"]

            # Пётр уже открывал ежедневник (аккаунт выше) — его заявка становится его карточкой.
            await save_lead(lead("crm-lead-petr", "+7 999 333-44-55", ref="petr"))
            petr_cards = await rows(
                "select c.phone_e164 from crm_contacts c join platform_accounts a on a.account_id = c.account_id "
                "where a.telegram_user_id = 7002"
            )
            assert petr_cards == [{"phone_e164": "+79993334455"}]
            # Без аккаунта (ни разу не открывал) — карточки нет, заявка сохраняется как раньше.
            admin("delete from platform_accounts where telegram_user_id = 7002")
            await save_lead(lead("crm-lead-petr-2", "+7 999 333-44-66", ref="petr"))
            assert await rows("select 1 from crm_contacts where phone_e164 = '+79993334466'") == []

            admin(sql.SQL("revoke insert on crm_contacts from {}").format(sql.Identifier(db.role)))
            broken = await save_lead(lead("crm-lead-broken", "+7 999 555-66-77"))
            admin(sql.SQL("grant insert on crm_contacts to {}").format(sql.Identifier(db.role)))
            assert broken["created"] is True
            assert len(await rows("select 1 from website_leads where idempotency_key = 'crm-lead-broken'")) == 1
            assert await rows("select 1 from crm_contacts where phone_e164 = '+79995556677'") == []

            # ---- 3. morning message ---------------------------------------------------
            planned = await enqueue_crm_digests({"whieda": "whieda-advisor-bot"}, in_window=lambda _t: True)
            assert planned == 1
            assert await enqueue_crm_digests({"whieda": "whieda-advisor-bot"}, in_window=lambda _t: True) == 0
            assert await enqueue_crm_digests({"whieda": "whieda-advisor-bot"}, in_window=lambda _t: False) == 0
            digests = await rows(
                "select outbox_id, idempotency_key, payload, status, due_at from platform_outbox "
                "where event_type = 'crm_daily_digest'"
            )
            assert len(digests) == 1
            digest = digests[0]
            assert digest["idempotency_key"] == f"crm_digest:{igor['account_id']}:{igor['today'].isoformat()}"
            assert digest["status"] == "pending" and digest["due_at"] is not None
            assert digest["payload"]["chat_id"] == "7001"
            assert digest["payload"]["url"] == "https://igor.wwc.best/crm/#today"
            assert digest["payload"]["text"].startswith("Сегодня в ежедневнике: пригласить на встречу — ")

            # The old outbox worker handles its own events and leaves due rows alone.
            async with tenant_connection("whieda") as conn:
                await enqueue_outbox_event(conn, tenant_id="whieda", event_type="onboarding_reminder",
                                           idempotency_key="crm-proof-old-event", payload={"x": 1})
            with patch("app.jobs.worker.trigger_lead_delivery", AsyncMock()) as delivery:
                await process_pending_outbox(batch_size=50)
            assert delivery.await_count >= 1  # lead_created events of the leads above
            status = await rows("select idempotency_key, status from platform_outbox where idempotency_key in (%s, %s)",
                                ("crm-proof-old-event", digest["idempotency_key"]))
            assert {r["idempotency_key"]: r["status"] for r in status} == {
                "crm-proof-old-event": "done",
                digest["idempotency_key"]: "pending",
            }

            # Rows planned for another bot (staging) are not claimed by this process.
            now = datetime.now(timezone.utc)
            async with tenant_connection("whieda") as conn:
                await enqueue_outbox_event(conn, tenant_id="whieda", event_type="crm_daily_digest",
                                           idempotency_key="proof:staging-bot",
                                           payload={"chat_id": "1", "text": "x", "binding_id": "wwc-cabinet-staging-bot"},
                                           due_at=now)
                await enqueue_outbox_event(conn, tenant_id="whieda", event_type="crm_daily_digest",
                                           idempotency_key="proof:retry",
                                           payload={"chat_id": "2", "text": "retry", "binding_id": "whieda-advisor-bot"},
                                           due_at=now + timedelta(hours=1))
            send = AsyncMock(return_value={"ok": True, "message_id": 10})
            with patch("app.jobs.worker.send_telegram_text", send):
                assert await process_due_notifications({"whieda": _binding()}) == 1
                assert await process_due_notifications({"whieda": _binding()}) == 0
            assert send.await_count == 1
            assert send.await_args.kwargs["chat_id"] == "7001"
            assert send.await_args.kwargs["bot_token"] == "test-token"
            assert send.await_args.kwargs["text"] == digest["payload"]["text"]
            done = await rows("select status, attempts from platform_outbox where outbox_id = %s", (digest["outbox_id"],))
            assert done == [{"status": "done", "attempts": 1}]
            staging = await rows("select status from platform_outbox where idempotency_key = 'proof:staging-bot'")
            assert staging == [{"status": "pending"}]

            # A transient failure is retried later; «blocked» (403) is final.
            admin("update platform_outbox set due_at = now() - interval '1 minute' where idempotency_key = 'proof:retry'")
            with patch("app.jobs.worker.send_telegram_text",
                       AsyncMock(side_effect=TelegramDeliveryError("telegram_send_failed:502"))):
                assert await process_due_notifications({"whieda": _binding()}) == 0
            retry = await rows("select status, attempts, due_at > now() as later from platform_outbox "
                               "where idempotency_key = 'proof:retry'")
            assert retry == [{"status": "pending", "attempts": 1, "later": True}]
            admin("update platform_outbox set due_at = now() - interval '1 minute' where idempotency_key = 'proof:retry'")
            with patch("app.jobs.worker.send_telegram_text",
                       AsyncMock(side_effect=TelegramDeliveryError("telegram_send_failed:403"))):
                assert await process_due_notifications({"whieda": _binding()}) == 0
            final = await rows("select status, attempts from platform_outbox where idempotency_key = 'proof:retry'")
            assert final == [{"status": "failed", "attempts": 2}]
            last_error = await rows("select last_error from platform_outbox where idempotency_key = 'proof:retry'")
            assert "test-token" not in (last_error[0]["last_error"] or "")

        db.run_with_app(proof)
