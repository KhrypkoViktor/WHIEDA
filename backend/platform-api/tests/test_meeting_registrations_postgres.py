"""Запись на Живую встречу в настоящем PostgreSQL (V46, роль API без bypassrls).

1. V46 идемпотентна (дважды в testkit, где website_leads ещё нет, и на таблице с колонками V44), знака доллара нет;
2. ограничения таблицы: канал, статус, одна активная запись на (арендатор, встреча, мессенджер, человек);
   отменённую и перенесённую можно создать заново; RLS по арендатору;
3. запись, перенос, отмена на живых строках: ``moved_from``, статусы, «уже записаны», чужая запись, поздно;
4. режим гостя: гость, партнёр (Telegram, канал Max, привязанная учётка), чужой арендатор;
5. сквозной сценарий: заявка → /start lead_<id> в Telegram и Max → партнёр закреплён первым касанием, запись,
   метки и заметки карточки CRM → меню гостя → перенос → отмена; пересланная ссылка; незнакомый человек и заявка
   другого вида — обычный старт.

Telegram и Max не вызываются: отправка — мок.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, SQL_DIR, temporary_database
from tests.test_crm_postgres import LEAD_TABLES, SEED, _binding

V44 = "platform_website_leads_meeting_v44.sql"
V46 = "platform_meeting_registrations_v46.sql"
ROOT = Path(__file__).resolve().parents[3]

COLUMNS = {
    "registration_id": "bigint",
    "tenant_id": "text",
    "meeting_id": "bigint",
    "lead_id": "uuid",
    "channel": "text",
    "channel_user_id": "bigint",
    "status": "text",
    "registered_at": "timestamp with time zone",
    "joined_at": "timestamp with time zone",
    "moved_from": "bigint",
    "reminders_sent": "jsonb",
    "created_at": "timestamp with time zone",
    "updated_at": "timestamp with time zone",
}


def test_v46_has_no_dollar_sign_a_lock_timeout_and_is_registered_everywhere_once():
    text = (SQL_DIR / V46).read_text(encoding="utf-8")
    assert "$" not in text  # боевые правки идут из контейнера и через n8n: знак доллара режется
    assert "set local lock_timeout" in text
    for relative in (
        "postgres/scripts/apply_staging_platform_all.ps1",
        "postgres/scripts/staging_proof_lib.py",
        "backend/platform-api/tests/postgres_testkit.py",
    ):
        assert (ROOT / relative).read_text(encoding="utf-8").count(V46) == 1, relative
    schema = (ROOT / "backend/platform-api/app/schema_requirements.py").read_text(encoding="utf-8")
    assert '"meeting_registrations"' in schema


def test_v46_comes_after_v44_and_v45_in_every_list():
    for relative in ("postgres/scripts/apply_staging_platform_all.ps1", "postgres/scripts/staging_proof_lib.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert text.index("platform_website_leads_meeting_v44.sql") < text.index("platform_bot_media_v45.sql") < text.index(V46), relative


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
def test_meeting_registrations_end_to_end(monkeypatch):
    _env(monkeypatch)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    in_days = lambda days: now + timedelta(days=days)  # noqa: E731

    with temporary_database("whieda_meeting_regs") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            # В testkit website_leads ещё нет: V46 не падает без таблицы (do-блок с проверкой), и дважды подряд.
            # platform_service_sales_v10 — колонки форума поддержки, которые читает «Задать вопрос» (open_site_support).
            db.apply_migrations(conn, (*MIGRATIONS, "platform_service_sales_v10.sql"), twice=True)
            columns = dict(
                conn.execute(
                    "select column_name, data_type from information_schema.columns where table_name = 'meeting_registrations'"
                ).fetchall()
            )
            assert columns == COLUMNS
            nullable = dict(
                conn.execute(
                    "select column_name, is_nullable from information_schema.columns where table_name = 'meeting_registrations'"
                ).fetchall()
            )
            assert {k for k, v in nullable.items() if v == "NO"} == {
                "registration_id", "tenant_id", "meeting_id", "channel", "channel_user_id", "status", "registered_at",
                "reminders_sent", "created_at", "updated_at",
            }
            defaults = {
                row[0]: row[1]
                for row in conn.execute(
                    "select column_name, column_default from information_schema.columns "
                    "where table_name = 'meeting_registrations' and column_name in ('status', 'reminders_sent')"
                ).fetchall()
            }
            assert "'registered'" in defaults["status"] and "'{}'" in defaults["reminders_sent"]
            indexes = {
                row[0]: row[1]
                for row in conn.execute("select indexname, indexdef from pg_indexes where tablename = 'meeting_registrations'").fetchall()
            }
            assert {"uq_meeting_registrations_active", "idx_meeting_registrations_meeting_status", "idx_meeting_registrations_person"} <= set(indexes)
            assert "UNIQUE" in indexes["uq_meeting_registrations_active"]
            assert "(tenant_id, meeting_id, channel, channel_user_id)" in indexes["uq_meeting_registrations_active"]
            assert "moved" in indexes["uq_meeting_registrations_active"] and "cancelled" in indexes["uq_meeting_registrations_active"]
            assert "(tenant_id, meeting_id, status)" in indexes["idx_meeting_registrations_meeting_status"]
            assert conn.execute("select relrowsecurity from pg_class where relname = 'meeting_registrations'").fetchone() == (True,)
            assert conn.execute("select count(*) from pg_policies where tablename = 'meeting_registrations'").fetchone() == (1,)

            conn.execute(LEAD_TABLES)
            conn.execute("alter table website_leads add column deleted_at timestamptz")  # есть на бою
            for _ in range(2):  # идемпотентность и на таблице с колонками V44: индексы по человеку появляются
                conn.execute((SQL_DIR / V44).read_text(encoding="utf-8"))
                conn.execute((SQL_DIR / V46).read_text(encoding="utf-8"))
            website_indexes = {
                row[0] for row in conn.execute("select indexname from pg_indexes where tablename = 'website_leads'").fetchall()
            }
            assert {"idx_website_leads_tg_user", "idx_website_leads_max_user"} <= website_indexes

            conn.execute(SEED)
            conn.execute(
                "update referral_profiles set public_profile = public_profile || %s::jsonb where ref_code = 'igor'",
                ('{"display_name": "Игорь Волков"}',),
            )
            conn.execute(
                "insert into tenant_bot_bindings (binding_id, tenant_id, webhook_secret_ref, status) values "
                "('whieda-advisor-bot', 'whieda', 'env:X', 'active')"
            )
            # встречи: 1–4 живые будущие; 5 — консультация; 6 — отменена; 7 — прошла по времени
            conn.execute(
                """
                insert into meetings (meeting_id, tenant_id, kind, title, starts_at, status) values
                  (1, 'whieda', 'live', 'Живая встреча', %s, 'planned'),
                  (2, 'whieda', 'live', 'Живая встреча', %s, 'planned'),
                  (3, 'whieda', 'live', 'Живая встреча', %s, 'planned'),
                  (4, 'whieda', 'live', 'Живая встреча', %s, 'planned'),
                  (5, 'whieda', 'consult', 'Консультация', %s, 'planned'),
                  (6, 'whieda', 'live', 'Живая встреча', %s, 'cancelled'),
                  (7, 'whieda', 'live', 'Живая встреча', %s, 'planned'),
                  (8, 'other', 'live', 'Чужая встреча', %s, 'planned')
                """,
                (in_days(10), in_days(12), in_days(14), in_days(16), in_days(11), in_days(13), now - timedelta(days=1), in_days(9)),
            )
            # Ограничения таблицы: администратор обходит RLS, но не CHECK и не уникальный индекс.
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "insert into meeting_registrations (tenant_id, meeting_id, channel, channel_user_id) "
                    "values ('whieda', 1, 'whatsapp', 1)"
                )
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(
                    "insert into meeting_registrations (tenant_id, meeting_id, channel, channel_user_id, status) "
                    "values ('whieda', 1, 'telegram', 1, 'bogus')"
                )
            with pytest.raises(psycopg.errors.NotNullViolation):
                conn.execute("insert into meeting_registrations (tenant_id, meeting_id, channel) values ('whieda', 1, 'max')")
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.crm import service as crm
            from app.db import fetch_all, tenant_connection
            from app.funnel import guest_bot, registration
            from app.funnel.guest import resolve_guest
            from app.leads.meeting_leads import load_lead_by_channel_user, load_lead_by_public_id
            from app.leads.service import parse_lead_body, save_lead
            from app.meetings import registrations
            from app.meetings.service import as_msk
            from app.telegram.bindings import binding_context_scope
            from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
            from app.tenancy import TenantContext

            tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})

            async def rows(query: str, params: tuple = (), tenant_id: str = "whieda") -> list[dict]:
                async with tenant_connection(tenant_id) as conn:
                    return [dict(r) for r in await fetch_all(conn, query, params)]

            async def registrations_of(user: int, channel: str = "telegram") -> list[dict]:
                return await rows(
                    "select registration_id, meeting_id, lead_id::text as lead_id, status, moved_from from meeting_registrations "
                    "where channel = %s and channel_user_id = %s order by registration_id",
                    (channel, user),
                )

            def tag(days: int) -> str:
                return f"записан {as_msk(in_days(days)):%d.%m}"

            # ---- 1. запись, повтор, закрытые встречи ---------------------------------------------------
            async with tenant_connection("whieda") as conn:
                first = await registrations.register(conn, "whieda", meeting_id=1, channel="telegram", channel_user_id=900)
                again = await registrations.register(conn, "whieda", meeting_id=1, channel="telegram", channel_user_id=900)
                max_same_id = await registrations.register(conn, "whieda", meeting_id=1, channel="max", channel_user_id=900)
                other_person = await registrations.register(conn, "whieda", meeting_id=1, channel="telegram", channel_user_id=901)
                closed = {
                    meeting_id: (await registrations.register(conn, "whieda", meeting_id=meeting_id, channel="telegram", channel_user_id=900)).status
                    for meeting_id in (5, 6, 7, 8, 999)
                }
            assert first.status == "registered" and first.registration.status == "registered"
            assert first.registration.meeting_id == 1 and first.registration.lead_id is None and first.registration.moved_from is None
            assert again.status == "already" and again.registration.registration_id == first.registration.registration_id
            assert max_same_id.status == "registered" and max_same_id.registration.registration_id != first.registration.registration_id
            assert other_person.status == "registered"
            # консультация, отменённая, прошедшая, чужого арендатора, несуществующая — отказ, строк не прибавилось
            assert closed == {5: "closed", 6: "closed", 7: "closed", 8: "not_found", 999: "not_found"}
            assert len(await registrations_of(900)) == 1 and len(await registrations_of(900, "max")) == 1

            # база сама не пускает вторую активную запись: уникальный индекс
            with pytest.raises(psycopg.errors.UniqueViolation):
                async with tenant_connection("whieda") as conn:
                    await conn.execute(
                        "insert into meeting_registrations (tenant_id, meeting_id, channel, channel_user_id) "
                        "values ('whieda', 1, 'telegram', 900)"
                    )

            # ---- 2. отмена, повторная запись, чужая запись --------------------------------------------
            async with tenant_connection("whieda") as conn:
                stranger = await registrations.cancel(
                    conn, "whieda", registration_id=first.registration.registration_id, channel="telegram", channel_user_id=901
                )
                wrong_channel = await registrations.cancel(
                    conn, "whieda", registration_id=first.registration.registration_id, channel="max", channel_user_id=900
                )
                cancelled = await registrations.cancel(
                    conn, "whieda", registration_id=first.registration.registration_id, channel="telegram", channel_user_id=900
                )
                cancelled_twice = await registrations.cancel(
                    conn, "whieda", registration_id=first.registration.registration_id, channel="telegram", channel_user_id=900
                )
                renewed = await registrations.register(conn, "whieda", meeting_id=1, channel="telegram", channel_user_id=900)
            assert stranger.status == "not_found" and wrong_channel.status == "not_found"
            assert cancelled.status == "cancelled" and cancelled.registration.status == "cancelled"
            assert cancelled_twice.status == "not_active"
            assert renewed.status == "registered" and renewed.registration.registration_id != first.registration.registration_id
            history = await registrations_of(900)
            assert [(r["status"], r["moved_from"]) for r in history] == [("cancelled", None), ("registered", None)]

            # ---- 3. перенос ---------------------------------------------------------------------------
            async with tenant_connection("whieda") as conn:
                moved = await registrations.move(
                    conn, "whieda", registration_id=renewed.registration.registration_id, to_meeting_id=2,
                    channel="telegram", channel_user_id=900,
                )
            assert moved.status == "moved"
            assert moved.old_registration.status == "moved" and moved.registration.status == "registered"
            assert moved.registration.meeting_id == 2 and moved.registration.moved_from == renewed.registration.registration_id
            assert moved.meeting["meeting_id"] == 2 and moved.old_meeting["meeting_id"] == 1
            history = await registrations_of(900)
            assert [(r["meeting_id"], r["status"]) for r in history] == [(1, "cancelled"), (1, "moved"), (2, "registered")]
            assert history[2]["moved_from"] == history[1]["registration_id"]
            async with tenant_connection("whieda") as conn:
                # на ту же, на закрытую, повторный перенос по старой записи, чужой человек
                same = await registrations.move(
                    conn, "whieda", registration_id=moved.registration.registration_id, to_meeting_id=2,
                    channel="telegram", channel_user_id=900,
                )
                to_consult = await registrations.move(
                    conn, "whieda", registration_id=moved.registration.registration_id, to_meeting_id=5,
                    channel="telegram", channel_user_id=900,
                )
                to_cancelled = await registrations.move(
                    conn, "whieda", registration_id=moved.registration.registration_id, to_meeting_id=6,
                    channel="telegram", channel_user_id=900,
                )
                old_button = await registrations.move(
                    conn, "whieda", registration_id=renewed.registration.registration_id, to_meeting_id=3,
                    channel="telegram", channel_user_id=900,
                )
                foreign = await registrations.move(
                    conn, "whieda", registration_id=moved.registration.registration_id, to_meeting_id=3,
                    channel="telegram", channel_user_id=901,
                )
            assert (same.status, to_consult.status, to_cancelled.status) == ("same", "closed", "closed")
            assert old_button.status == "not_active" and foreign.status == "not_found"
            assert [(r["meeting_id"], r["status"]) for r in await registrations_of(900)] == [(1, "cancelled"), (1, "moved"), (2, "registered")]

            # перенос на встречу, куда человек уже записан: прежняя запись остаётся
            async with tenant_connection("whieda") as conn:
                await registrations.register(conn, "whieda", meeting_id=3, channel="telegram", channel_user_id=900)
                clash = await registrations.move(
                    conn, "whieda", registration_id=moved.registration.registration_id, to_meeting_id=3,
                    channel="telegram", channel_user_id=900,
                )
            assert clash.status == "already"
            assert [(r["meeting_id"], r["status"]) for r in await registrations_of(900)][-2:] == [(2, "registered"), (3, "registered")]

            # ---- 4. поздно: встреча началась ----------------------------------------------------------
            started = in_days(12) + timedelta(minutes=5)
            async with tenant_connection("whieda") as conn:
                late_move = await registrations.move(
                    conn, "whieda", registration_id=moved.registration.registration_id, to_meeting_id=4,
                    channel="telegram", channel_user_id=900, now=started,
                )
                late_cancel = await registrations.cancel(
                    conn, "whieda", registration_id=moved.registration.registration_id, channel="telegram", channel_user_id=900, now=started,
                )
                late_register = await registrations.register(
                    conn, "whieda", meeting_id=2, channel="telegram", channel_user_id=902, now=started
                )
            assert late_move.status == "too_late" and late_cancel.status == "too_late"
            assert late_register.status == "closed"
            # владелец отменил встречу — человек всё равно может уйти или перенести запись
            async with tenant_connection("whieda") as conn:
                await conn.execute("update meetings set status = 'cancelled' where meeting_id = 3")
                third = (await registrations_of(900))[-1]
                from_cancelled = await registrations.cancel(
                    conn, "whieda", registration_id=third["registration_id"], channel="telegram", channel_user_id=900
                )
                await conn.execute("update meetings set status = 'planned' where meeting_id = 3")
            assert third["meeting_id"] == 3 and from_cancelled.status == "cancelled"

            # ---- 5. выборки ---------------------------------------------------------------------------
            async with tenant_connection("whieda") as conn:
                upcoming = await registrations.open_meetings(conn, "whieda", limit=3)
                excluded = await registrations.open_meetings(conn, "whieda", limit=10, exclude_meeting_id=2)
                active = await registrations.active_registrations(conn, "whieda", channel="telegram", channel_user_id=900)
                nobody = await registrations.active_registrations(conn, "whieda", channel="telegram", channel_user_id=123)
                has = await registrations.person_has_registration(conn, "whieda", channel="telegram", channel_user_id=900)
                has_not = await registrations.person_has_registration(conn, "whieda", channel="telegram", channel_user_id=123)
                mine = await registrations.get_registration(
                    conn, "whieda", registration_id=moved.registration.registration_id, channel="telegram", channel_user_id=900
                )
                not_mine = await registrations.get_registration(
                    conn, "whieda", registration_id=moved.registration.registration_id, channel="telegram", channel_user_id=901
                )
            assert [m["meeting_id"] for m in upcoming] == [1, 2, 3]            # живые, будущие, по времени; лимит 3
            assert [m["meeting_id"] for m in excluded] == [1, 3, 4]            # без текущей; консультации, отменённой, прошедшей и чужой нет
            assert all("join_url" not in m for m in upcoming)                  # ссылка входа здесь не читается
            assert [(r.meeting_id, r.status) for r, _ in active] == [(2, "registered")]
            assert active[0][1]["meeting_id"] == 2 and active[0][1]["starts_at"] is not None
            assert nobody == [] and has is True and has_not is False
            assert mine is not None and not_mine is None

            # ---- 6. RLS: арендатор видит только своё ---------------------------------------------------
            assert len(await rows("select 1 from meeting_registrations", tenant_id="other")) == 0
            assert len(await rows("select 1 from meeting_registrations")) > 0
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with tenant_connection("other") as conn:
                    await conn.execute(
                        "insert into meeting_registrations (tenant_id, meeting_id, channel, channel_user_id) values ('whieda', 2, 'max', 5)"
                    )
            async with tenant_connection("other") as conn:   # у «other» своя встреча 8
                other_reg = await registrations.register(conn, "other", meeting_id=8, channel="telegram", channel_user_id=900)
                missing = await registrations.register(conn, "other", meeting_id=2, channel="telegram", channel_user_id=900)
            assert other_reg.status == "registered" and missing.status == "not_found"   # чужая встреча арендатору «other» не видна

            # ---- 7. режим гостя ----------------------------------------------------------------------
            await crm.get_or_create_account("whieda", 7001)   # у Игоря открыт ежедневник: карточки заявок заводятся
            lead_a = await save_lead(parse_lead_body(_body("g-a", "+7 (999) 111-22-33", meeting_id=1, first_ref="igor", active_ref="igor"), "whieda"))
            lead_b = await save_lead(parse_lead_body(_body("g-b", "+7 999 222-33-44", messenger="max", page_url="https://wwc.best/vstrecha/"), "whieda"))
            lead_c = await save_lead(parse_lead_body(_body("g-c", "+7 999 333-44-55", meeting_id=1, first_ref="igor", active_ref="igor"), "whieda"))
            old_kind = await save_lead(
                parse_lead_body(
                    {"name": "Ольга", "contact": "+7 999 666-77-88", "product": "Стельки", "idempotency_key": "old-1",
                     "initial_ref": "igor", "active_ref": "igor", "page_url": "https://igor.wwc.best/catalog/"},
                    "whieda",
                )
            )
            lead_rows = {
                key: (await rows("select * from website_leads where public_id = %s", (saved["public_id"],)))[0]
                for key, saved in (("a", lead_a), ("b", lead_b), ("c", lead_c))
            }
            card_a = lead_rows["a"]["metadata"]["crm_card"]
            assert lead_rows["a"]["assigned_owner_id"] == "igor-actor" and lead_rows["b"]["assigned_owner_id"] == "organic"
            assert "crm_card" in lead_rows["c"]["metadata"]

            async with tenant_connection("whieda") as conn:
                nobody_yet = await resolve_guest(conn, "whieda", "telegram", 555001)
                no_lead_for_user = await load_lead_by_channel_user(conn, "whieda", "telegram", 555001)
            assert nobody_yet is None and no_lead_for_user is None   # заявка есть, но человек бота ещё не открывал

            # ---- 8. старт lead_<id> в Telegram -------------------------------------------------------
            sent = AsyncMock(return_value={"ok": True, "message_id": 1})
            edits = AsyncMock(return_value={"ok": True})

            def message(user: int, text: str = "/start") -> TelegramMessage:
                return TelegramMessage(chat_id=user, user_id=user, message_id=1, text=text, chat_type="private", file_id=None,
                                       raw={"message": {"from": {"id": user, "first_name": "Анна"}}})

            def callback(user: int, data: str, message_id: int = 77) -> TelegramCallbackQuery:
                return TelegramCallbackQuery(chat_id=user, user_id=user, callback_query_id="cq", data=data, chat_type="private",
                                             raw={"callback_query": {"message": {"message_id": message_id}}})

            def last_text() -> str:
                return sent.await_args.kwargs["text"]

            def last_buttons() -> list[tuple[str, str]]:
                markup = sent.await_args.kwargs["reply_markup"]
                return [(b["text"], b["callback_data"]) for row in markup["inline_keyboard"] for b in row] if markup else []

            with binding_context_scope(_binding()), patch.object(guest_bot, "send_telegram_text", sent), \
                    patch.object(guest_bot, "answer_callback_query", AsyncMock()), patch.object(guest_bot, "_call_telegram", edits):
                # незнакомый id и заявка другого вида — обычный старт (None), ничего не отправлено
                assert await guest_bot.handle_lead_start_telegram(tenant, message(555001), "L-NOPE0000", trace_id="t") is None
                assert await guest_bot.handle_lead_start_telegram(tenant, message(555001), old_kind["public_id"], trace_id="t") is None
                sent.assert_not_awaited()
                assert await rows("select 1 from partner_referral_attributions where invitee_actor_id = 'telegram:whieda:555001'") == []

                started = await guest_bot.handle_lead_start_telegram(tenant, message(555001), lead_a["public_id"], trace_id="t")
                assert started == {"ok": True, "route": "lead_start", "status": "registered", "attribution": "attributed", "trace_id": "t"}
                assert last_text().startswith("Вы пришли от Игорь Волков.\n\nВы записаны на Живую встречу: ")
                assert last_text().endswith(" МСК.\nНапоминание о встрече придёт сюда.")
                regs = await registrations_of(555001)
                assert len(regs) == 1 and regs[0]["status"] == "registered" and regs[0]["meeting_id"] == 1
                assert regs[0]["lead_id"] == str(lead_rows["a"]["lead_id"])
                assert last_buttons()[:2] == [("Перенести", f"mmove:{regs[0]['registration_id']}"), ("Отменить запись", f"mcancel:{regs[0]['registration_id']}")]
                assert [d for _, d in last_buttons()[2:]] == ["funnel:tour", "funnel:consult", "funnel:ask"]
                # заявка привязана к человеку; партнёр закреплён первым касанием тем же путём, что у ref_<код>
                lead_after = (await rows("select telegram_user_id, max_user_id, bot_opened_at from website_leads where lead_id = %s", (lead_rows["a"]["lead_id"],)))[0]
                assert lead_after["telegram_user_id"] == 555001 and lead_after["max_user_id"] is None and lead_after["bot_opened_at"] is not None
                attribution = await rows("select inviter_actor_id, source from partner_referral_attributions where invitee_actor_id = 'telegram:whieda:555001'")
                assert attribution == [{"inviter_actor_id": "igor-actor", "source": "telegram_deeplink"}]
                # карточка: «бот не открыл» → «в боте», метка встречи осталась, заметки записаны
                card = (await rows("select tags from crm_contacts where contact_id = %s::uuid", (card_a,)))[0]
                assert set(card["tags"]) == {tag(10), "в боте"}
                notes = [r["body"] for r in await rows("select body from crm_notes where contact_id = %s::uuid order by created_at", (card_a,))]
                assert notes[-2:] == ["Открыл(а) бота WWC в Telegram.", notes[-1]] and notes[-1].startswith("Записался(ась) на Живую встречу: ")

                # повтор того же старта: ни второй записи, ни второй заметки, партнёр не меняется
                again_started = await guest_bot.handle_lead_start_telegram(tenant, message(555001), lead_a["public_id"], trace_id="t")
                assert again_started["status"] == "already" and again_started["attribution"] == "already_registered"
                assert len(await registrations_of(555001)) == 1
                assert [r["body"] for r in await rows("select body from crm_notes where contact_id = %s::uuid order by created_at", (card_a,))] == notes

                # гость: голый /start, «меню» и приветствие — меню гостя; партнёр и чужие — как прежде
                for text in ("/start", "меню", "привет"):
                    shown = await guest_bot.try_handle_guest_menu_text(tenant, message(555001, text), trace_id="t")
                    assert shown["route"] == "guest_menu"
                    assert last_text().startswith("Вы пришли от Игорь Волков.\n\nВы записаны на Живую встречу")
                async with tenant_connection("whieda") as conn:
                    guest_context = await resolve_guest(conn, "whieda", "telegram", 555001)
                assert guest_context is not None and guest_context.lead.public_id == lead_a["public_id"]
                sent.reset_mock()
                for stranger in (7001, 7002, 123456):   # Игорь и Пётр — партнёры; третий ни разу не писал
                    assert await guest_bot.try_handle_guest_menu_text(tenant, message(stranger), trace_id="t") is None
                assert await guest_bot.try_handle_guest_menu_text(tenant, message(555001, "товары"), trace_id="t") is None
                sent.assert_not_awaited()

                # партнёр не гость даже с чужой заявкой на своё имя
                async with tenant_connection("whieda") as conn:
                    await conn.execute("update website_leads set telegram_user_id = 7001 where lead_id = %s", (lead_rows["c"]["lead_id"],))
                    assert await resolve_guest(conn, "whieda", "telegram", 7001) is None
                    await conn.execute("update website_leads set telegram_user_id = null where lead_id = %s", (lead_rows["c"]["lead_id"],))

                # ---- 9. перенос и отмена из бота --------------------------------------------------------
                reg_id = regs[0]["registration_id"]
                listed = await guest_bot.try_handle_guest_callback(tenant, callback(555001, f"mmove:{reg_id}"), trace_id="t")
                assert listed["status"] == "listed"
                assert last_text() == "На какую встречу перенести запись?"
                assert [d for _, d in last_buttons()] == [f"mmoveto:{reg_id}:2", f"mmoveto:{reg_id}:3", f"mmoveto:{reg_id}:4"]
                edits.assert_not_awaited()

                done = await guest_bot.try_handle_guest_callback(tenant, callback(555001, f"mmoveto:{reg_id}:2", message_id=88), trace_id="t")
                assert done["status"] == "moved"
                regs = await registrations_of(555001)
                assert [(r["meeting_id"], r["status"]) for r in regs] == [(1, "moved"), (2, "registered")]
                assert regs[1]["moved_from"] == regs[0]["registration_id"] and regs[1]["lead_id"] == regs[0]["lead_id"]
                new_id = regs[1]["registration_id"]
                assert last_text().startswith("Запись перенесена: ")
                assert last_buttons() == [("Перенести", f"mmove:{new_id}"), ("Отменить запись", f"mcancel:{new_id}")]
                edits.assert_awaited_once()                      # кнопки списка снимаются
                assert edits.await_args.args[1]["message_id"] == 88
                card = (await rows("select tags from crm_contacts where contact_id = %s::uuid", (card_a,)))[0]
                assert set(card["tags"]) == {tag(12), "в боте"}   # метка старой встречи снята, новой — поставлена
                move_notes = [r["body"] for r in await rows("select body from crm_notes where contact_id = %s::uuid order by created_at", (card_a,))]
                assert move_notes[-1].startswith("Перенёс(ла) запись на Живую встречу: ") and " → " in move_notes[-1]

                # старая кнопка «Отменить» под прежней записью: вежливый отказ, ничего не менялось
                stale = await guest_bot.try_handle_guest_callback(tenant, callback(555001, f"mcancel:{reg_id}"), trace_id="t")
                assert stale["status"] == "stale" and last_text().startswith("Эта запись уже изменена.")
                assert (await registrations_of(555001))[1]["status"] == "registered"
                # чужая запись: тот же отказ, у чужого человека ничего не меняется
                foreign = await guest_bot.try_handle_guest_callback(tenant, callback(555777, f"mcancel:{new_id}"), trace_id="t")
                assert foreign["status"] == "stale"
                assert (await registrations_of(555001))[1]["status"] == "registered"

                # список под видео-туром: три встречи, на которую записан — «Вы записаны»
                meet = await guest_bot.try_handle_guest_callback(tenant, callback(555001, "funnel:meet"), trace_id="t")
                assert meet["status"] == "listed"
                assert [d for _, d in last_buttons()] == ["mreg:1", "mreg:2", "mreg:3"]
                assert [t.split(":")[0] for t, _ in last_buttons()] == ["Записаться", "Вы записаны", "Записаться"]

                # запись на прошедшую встречу и на несуществующую: вежливый отказ, другие встречи предложены
                refused = await guest_bot.try_handle_guest_callback(tenant, callback(555001, "mreg:7"), trace_id="t")
                assert refused["status"] == "closed" and last_text().startswith("Эта встреча уже прошла или отменена.\nВыберите другую:")
                assert [d for _, d in last_buttons()] == ["mreg:1", "mreg:2", "mreg:3"]

                # отмена: строка остаётся, метка снимается
                cancel = await guest_bot.try_handle_guest_callback(tenant, callback(555001, f"mcancel:{new_id}"), trace_id="t")
                assert cancel["status"] == "cancelled" and last_text().startswith("Запись на ") and "отменена" in last_text()
                assert [(r["meeting_id"], r["status"]) for r in await registrations_of(555001)] == [(1, "moved"), (2, "cancelled")]
                card = (await rows("select tags from crm_contacts where contact_id = %s::uuid", (card_a,)))[0]
                assert set(card["tags"]) == {"в боте"}
                assert (await rows("select body from crm_notes where contact_id = %s::uuid order by created_at desc limit 1", (card_a,)))[0]["body"].startswith("Отменил(а) запись")
                # меню гостя после отмены: ближайшие встречи кнопками, а не записи
                await guest_bot.try_handle_guest_menu_text(tenant, message(555001), trace_id="t")
                assert [d for _, d in last_buttons()][:2] == ["mreg:1", "mreg:2"]
                # и снова записаться на ту же встречу можно: метка возвращается
                back = await guest_bot.try_handle_guest_callback(tenant, callback(555001, "mreg:2"), trace_id="t")
                assert back["status"] == "registered"
                card = (await rows("select tags from crm_contacts where contact_id = %s::uuid", (card_a,)))[0]
                assert set(card["tags"]) == {"в боте", tag(12)}

                # ---- 10. пересланная ссылка: другой человек — запись есть, заявки и карточки в ней нет ------
                await guest_bot.handle_lead_start_telegram(tenant, message(555002), lead_a["public_id"], trace_id="t")
                forwarded = await registrations_of(555002)
                assert len(forwarded) == 1 and forwarded[0]["lead_id"] is None and forwarded[0]["meeting_id"] == 1
                lead_after = (await rows("select telegram_user_id from website_leads where lead_id = %s", (lead_rows["a"]["lead_id"],)))[0]
                assert lead_after["telegram_user_id"] == 555001   # первый открывший остаётся
                assert len(await rows("select 1 from crm_notes where contact_id = %s::uuid", (card_a,))) == len(move_notes) + 2
                # ему тоже закреплён партнёр заявки (он ещё не был знаком боту)
                assert (await rows("select inviter_actor_id from partner_referral_attributions where invitee_actor_id = 'telegram:whieda:555002'"))[0]["inviter_actor_id"] == "igor-actor"
                async with tenant_connection("whieda") as conn:
                    friend = await resolve_guest(conn, "whieda", "telegram", 555002)
                assert friend is not None and friend.lead is None   # гость по записи, без заявки

                # ---- 11. человек, уже известный боту с другим партнёром, остаётся за ним ----------------------
                from app.referral_bonus.service import accept_referral_start, get_or_create_invite_code

                petr_code = await get_or_create_invite_code("whieda", "petr-actor")
                first_touch = await accept_referral_start(
                    "whieda", telegram_user_id=555003, telegram_chat_id=555003, invite_code=petr_code, raw_update={}
                )
                assert first_touch.status == "attributed"
                await guest_bot.handle_lead_start_telegram(tenant, message(555003), lead_c["public_id"], trace_id="t")
                result_c = await guest_bot.handle_lead_start_telegram(tenant, message(555003), lead_c["public_id"], trace_id="t")
                assert result_c["attribution"] == "already_registered"
                assert (await rows("select inviter_actor_id from partner_referral_attributions where invitee_actor_id = 'telegram:whieda:555003'"))[0]["inviter_actor_id"] == "petr-actor"

                # ---- 12. встреча из заявки отменена до старта ----------------------------------------------
                late = await save_lead(parse_lead_body(_body("g-d", "+7 999 444-55-66", meeting_id=4, first_ref="igor", active_ref="igor"), "whieda"))
                async with tenant_connection("whieda") as conn:
                    await conn.execute("update meetings set status = 'cancelled' where meeting_id = 4")
                gone = await guest_bot.handle_lead_start_telegram(tenant, message(555004), late["public_id"], trace_id="t")
                assert gone["status"] == "closed"
                assert "Встреча, на которую вы записывались, уже прошла или отменена." in last_text()
                assert [d for _, d in last_buttons()] == ["mreg:1", "mreg:2", "funnel:tour", "funnel:consult", "funnel:ask"]
                assert await registrations_of(555004) == []

            # ---- 13. старт lead_<id> в Max: organic-заявка, партнёра нет ---------------------------------
            max_sent = AsyncMock(return_value={"ok": True})
            max_event = SimpleNamespace(user_id=777002, chat_id=888, username=None, display_name="Инна", text=f"lead_{lead_b['public_id']}")
            with patch.object(guest_bot, "send_max_text", max_sent):
                started_max = await guest_bot.handle_lead_start_max(tenant, max_event, lead_b["public_id"], "t")
            assert started_max == {"ok": True, "route": "max_lead_start", "status": "menu", "attribution": None, "trace_id": "t"}
            sent_kwargs = max_sent.await_args.kwargs
            assert sent_kwargs["chat_id"] == 888 and sent_kwargs["user_id"] == 777002
            assert sent_kwargs["text"].startswith("Вы в WWC.\n\nВыберите ближайшую живую встречу.")
            assert [b["payload"] for row in sent_kwargs["buttons"] for b in row] == ["mreg:1", "mreg:2", "funnel:tour", "funnel:consult", "funnel:ask"]
            lead_b_after = (await rows("select telegram_user_id, max_user_id from website_leads where lead_id = %s", (lead_rows["b"]["lead_id"],)))[0]
            assert lead_b_after == {"telegram_user_id": None, "max_user_id": 777002}
            assert await rows("select 1 from partner_referral_attributions where invitee_actor_id like %s", ("max:%",)) == []   # organic не закрепляется
            assert await registrations_of(777002, "max") == []   # в заявке встречи не было: человек выбирает сам

            # запись в Max кнопкой: та же запись, канал max
            result_max = await registration.register("whieda", "max", 777002, 3)
            assert result_max.status == "registered" and result_max.reply.rows[0][0][1].startswith("mmove:")
            max_regs = await registrations_of(777002, "max")
            assert [(r["meeting_id"], r["status"], r["lead_id"]) for r in max_regs] == [(3, "registered", str(lead_rows["b"]["lead_id"]))]
            async with tenant_connection("whieda") as conn:
                max_guest = await resolve_guest(conn, "whieda", "max", 777002)
            assert max_guest is not None and max_guest.lead.public_id == lead_b["public_id"]

            # Max: партнёрская заявка закрепляет партнёра через канал Max
            lead_e = await save_lead(parse_lead_body(_body("g-e", "+7 999 555-66-77", messenger="max", meeting_id=2, first_ref="igor", active_ref="igor"), "whieda"))
            with patch.object(guest_bot, "send_max_text", max_sent):
                await guest_bot.handle_lead_start_max(
                    tenant, SimpleNamespace(user_id=777003, chat_id=889, username=None, display_name="Яна", text=""), lead_e["public_id"], "t"
                )
            assert max_sent.await_args.kwargs["text"].startswith("Вы пришли от Игорь Волков.\n\nВы записаны на Живую встречу: ")
            assert (await rows("select inviter_actor_id, source from partner_referral_attributions where invitee_actor_id = 'max:whieda:777003'")) == [
                {"inviter_actor_id": "igor-actor", "source": "max_deeplink"}
            ]
            assert [(r["meeting_id"], r["status"]) for r in await registrations_of(777003, "max")] == [(2, "registered")]

            # ---- 14. партнёр в Max не гость (канал привязан к его actor и учётка Max привязана к Telegram) ----------
            async with tenant_connection("whieda") as conn:
                await conn.execute(
                    "insert into lead_actor_channels (tenant_id, actor_id, channel, channel_user_id) values ('whieda', 'petr-actor', 'max', '777010')"
                )
                await conn.execute("update website_leads set max_user_id = 777010 where lead_id = %s", (lead_rows["c"]["lead_id"],))
                via_channel = await resolve_guest(conn, "whieda", "max", 777010)
                await conn.execute("delete from lead_actor_channels where channel_user_id = '777010'")
                await conn.execute(
                    "insert into account_identities (tenant_id, telegram_user_id, provider, external_id) values ('whieda', 7001, 'max', '777010')"
                )
                via_identity = await resolve_guest(conn, "whieda", "max", 777010)
                await conn.execute("update account_identities set revoked_at = now() where external_id = '777010'")
                after_revoke = await resolve_guest(conn, "whieda", "max", 777010)
            assert via_channel is None and via_identity is None
            assert after_revoke is not None   # привязку отозвали — снова обычный гость этой заявки

            # другой арендатор гостей этого не видит
            async with tenant_connection("other") as conn:
                assert await resolve_guest(conn, "other", "telegram", 555001) is None
                assert await load_lead_by_public_id(conn, "other", lead_a["public_id"]) is None

            # ---- 15. «Задать вопрос»: обращение в поддержку у владельца (тот же механизм, что у «Поддержки») --------
            import itertools

            from app.settings import get_settings
            from app.support.service import get_open_ticket_for_user
            from app.telegram.support import try_relay_user_message

            owner_id = 688931415
            monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(owner_id))
            get_settings.cache_clear()
            support_tenant = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={"site_support": True})
            admin_ids = itertools.count(41)
            to_guest = AsyncMock(return_value={"ok": True, "message_id": 1})
            to_owner = AsyncMock(side_effect=lambda *a, **kw: {"ok": True, "message_id": next(admin_ids)})
            header = AsyncMock(return_value={"ok": True, "message_id": 40})
            with binding_context_scope(_binding()), patch.object(guest_bot, "answer_callback_query", AsyncMock()), \
                    patch.object(guest_bot, "send_telegram_text", sent), \
                    patch("app.telegram.support._send", to_guest), patch("app.telegram.support._send_to_admin", to_owner), \
                    patch("app.telegram.support._send_site_header", header):
                assert await get_open_ticket_for_user("whieda", user_telegram_user_id=555001) is None
                asked = await guest_bot.try_handle_guest_callback(support_tenant, callback(555001, "funnel:ask"), trace_id="t")
                assert asked == {"ok": True, "route": "guest_callback", "trace_id": "t", "status": "ask", "support": "ticket_opened"}
                ticket = await get_open_ticket_for_user("whieda", user_telegram_user_id=555001)
                assert ticket is not None and ticket["channel_code"] == "site" and int(ticket["admin_telegram_user_id"]) == owner_id
                # человеку — текст воронки (одно сообщение), владельцу — обычная шапка обращения с именем
                to_guest.assert_awaited_once_with(555001, "Напишите вопрос одним сообщением — передам Виктору")
                assert f"#S-{ticket['ticket_no']}" in header.await_args.args[1]

                # следующий обычный текст гостя — владельцу, тем же путём, что у любого обращения
                relayed = await try_relay_user_message(support_tenant, message(555001, "Как попасть на встречу из другого города?"), trace_id="t")
                assert relayed["route"] == "support_relay" and relayed["direction"] == "user_to_admin" and relayed["duplicate"] is False
                assert "Как попасть на встречу из другого города?" in to_owner.await_args.args[1]
                # вопрос с приветствия — тоже владельцу, а не меню гостя
                second = dataclasses.replace(message(555001, "Привет! Можно перенести на вторник?"), message_id=2)  # другое сообщение Telegram
                greeted = await guest_bot.try_handle_guest_menu_text(support_tenant, second, trace_id="t")
                assert greeted["route"] == "support_relay"
                assert "Можно перенести на вторник?" in to_owner.await_args.args[1]
                # а слово «меню» по-прежнему открывает меню гостя, обращение остаётся открытым
                menu = await guest_bot.try_handle_guest_menu_text(support_tenant, message(555001, "меню"), trace_id="t")
                assert menu["route"] == "guest_menu"
                assert (await get_open_ticket_for_user("whieda", user_telegram_user_id=555001))["ticket_id"] == ticket["ticket_id"]
                # второе «Задать вопрос» — то же обращение, приглашение приходит снова
                to_guest.reset_mock()
                again_asked = await guest_bot.try_handle_guest_callback(support_tenant, callback(555001, "funnel:ask"), trace_id="t")
                assert again_asked["support"] == "ticket_opened"
                assert (await get_open_ticket_for_user("whieda", user_telegram_user_id=555001))["ticket_id"] == ticket["ticket_id"]
                to_guest.assert_awaited_once_with(555001, "Напишите вопрос одним сообщением — передам Виктору")
                assert len(await rows("select 1 from support_tickets where user_telegram_user_id = 555001")) == 1
            conversation = await rows(
                "select direction, text from support_messages where ticket_id = %s::uuid and direction = 'user_to_admin' order by created_at, message_id",
                (str(ticket["ticket_id"]),),
            )
            assert sorted(r["text"] for r in conversation) == [
                "Как попасть на встречу из другого города?", "Привет! Можно перенести на вторник?",
            ]

        db.run_with_app(proof)
