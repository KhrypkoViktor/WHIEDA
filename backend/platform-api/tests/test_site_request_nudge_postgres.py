"""Напоминание незаконченным заявкам: выборка и ключ дня на настоящей базе (24.10.2026).

Границы суток и семи дней, статусы awaiting_*, три защиты от дублей — настоящим SQL по
настоящим миграциям; Telegram и обращения подменены."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

SITE_MIGRATIONS = (*MIGRATIONS, "platform_service_sales_v10.sql")
UTC = dt.timezone.utc

_uid = iter(range(81000, 82000))


def _request(conn: psycopg.Connection, status: str, *, hours: float, actor: str | None = None) -> tuple[str, str, int]:
    """Заявка, стоящая без движения ``hours`` часов. Возвращает (request_id, actor_id, telegram_user_id)."""
    user = next(_uid)
    actor = actor or f"actor{user}"
    conn.execute(
        "insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id) "
        "values (%s, 'whieda', %s, %s, %s) on conflict do nothing",
        (actor, f"Имя{user}", user, str(user)),
    )
    row = conn.execute("select telegram_user_id from lead_actors where actor_id = %s", (actor,)).fetchone()
    request_id = conn.execute(
        "insert into partner_site_requests (tenant_id, actor_id, status, partner_name, created_at, updated_at) "
        "values ('whieda', %s, %s, %s, now() - make_interval(hours => %s), now() - make_interval(hours => %s)) "
        "returning request_id::text",
        (actor, status, f"Имя{user} Фамилия", hours + 1, hours),
    ).fetchone()[0]
    return request_id, actor, int(row[0])


def _notice(conn: psycopg.Connection, request_id: str, *, days: float) -> None:
    conn.execute(
        "insert into service_notice_log (tenant_id, notice_key, sent_on, created_at) "
        "values ('whieda', %s, current_date, now() - make_interval(days => %s))",
        ("site_nudge:" + request_id, days),
    )


def _our_message(conn: psycopg.Connection, user: int, direction: str, *, hours: float) -> None:
    ticket = conn.execute(
        "insert into support_tickets (tenant_id, channel_code, user_telegram_user_id, user_chat_id, user_display, admin_telegram_user_id) "
        "values ('whieda', 'site', %s, %s, 'x', 1) returning ticket_id",
        (user, user),
    ).fetchone()[0]
    conn.execute(
        "insert into support_messages (tenant_id, ticket_id, direction, text, created_at) "
        "values ('whieda', %s, %s, 'привет', now() - make_interval(hours => %s))",
        (ticket, direction, hours),
    )


@pytest.mark.integration
def test_selection_boundaries_statuses_and_all_three_guards():
    with temporary_database("whieda_site_nudge") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SITE_MIGRATIONS)
            want: dict[str, str] = {}  # request_id -> зачем он в списке
            skip: dict[str, str] = {}

            # Границы: дольше 24 часов и не дольше 7 суток; статусы awaiting_*.
            want[_request(conn, "awaiting_name", hours=25)[0]] = "сутки с часом"
            skip[_request(conn, "awaiting_name", hours=23)[0]] = "ещё нет суток"
            want[_request(conn, "awaiting_plan", hours=6 * 24 + 23)[0]] = "6 суток 23 часа"
            skip[_request(conn, "awaiting_plan", hours=7 * 24 + 1)[0]] = "старше 7 суток"
            want[_request(conn, "awaiting_payment", hours=48)[0]] = "ждём оплату"
            want[_request(conn, "awaiting_contacts", hours=48)[0]] = "ждём контакты"
            for status in ("pending_confirmation", "pending_provisioning", "provisioned", "cancelled", "rejected"):
                skip[_request(conn, status, hours=48)[0]] = status

            # Защита 1: этой заявке напоминание уже уходило.
            sent_before, _, _ = _request(conn, "awaiting_name", hours=48)
            _notice(conn, sent_before, days=1)
            skip[sent_before] = "ключ заявки"

            # Защита 2: человеку по любой его заявке за 30 дней. Первая заявка закрыта, вторая открыта.
            recent_old, actor, _ = _request(conn, "cancelled", hours=24 * 20)
            _notice(conn, recent_old, days=5)
            skip[_request(conn, "awaiting_name", hours=48, actor=actor)[0]] = "напоминали 5 дней назад"
            stale_old, actor, _ = _request(conn, "cancelled", hours=24 * 60)
            _notice(conn, stale_old, days=31)
            want[_request(conn, "awaiting_name", hours=48, actor=actor)[0]] = "напоминали 31 день назад"

            # Защита 3: тому, кому мы сами писали в обращении за последние сутки.
            request_id, _, user = _request(conn, "awaiting_name", hours=48)
            _our_message(conn, user, "admin_to_user", hours=2)
            skip[request_id] = "писали 2 часа назад"
            request_id, _, user = _request(conn, "awaiting_name", hours=48)
            _our_message(conn, user, "admin_to_user", hours=25)
            want[request_id] = "писали 25 часов назад"
            request_id, _, user = _request(conn, "awaiting_name", hours=48)
            _our_message(conn, user, "user_to_admin", hours=1)
            want[request_id] = "человек писал нам, мы ему — нет"

            db.grant_api_role(conn)

        async def proof() -> None:
            from app.site_requests import nudge

            found = await nudge.find_candidates()
            assert {row["rid"] for row in found} == set(want), (
                {skip.get(r) or want.get(r) for r in {row["rid"] for row in found} ^ set(want)}
            )
            assert not {row["rid"] for row in found} & set(skip)
            # Самые давние — первыми.
            assert [row["upd"] for row in found] == sorted(row["upd"] for row in found)
            # Свои границы — для ручного запуска («--min-hours», «--max-days»).
            wide = {row["rid"] for row in await nudge.find_candidates(min_hours=22, max_days=8)}
            assert wide >= set(want) and len(wide) == len(want) + 2

        db.run_with_app(proof)


@pytest.mark.integration
def test_worker_step_sends_once_a_day_only_from_production_and_never_twice_to_one_request(monkeypatch: pytest.MonkeyPatch):
    with temporary_database("whieda_site_nudge_step") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SITE_MIGRATIONS)
            first, _, _ = _request(conn, "awaiting_name", hours=30)
            second, _, _ = _request(conn, "awaiting_plan", hours=50)
            _request(conn, "pending_confirmation", hours=50)  # чек уже прислан — не трогаем
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, tenant_connection
            from app.settings import get_settings
            from app.site_requests import nudge

            sent_to: list[str] = []
            binding = SimpleNamespace(bot_token="tok", binding_id="whieda-advisor-bot", tenant=SimpleNamespace(tenant_id="whieda"))

            async def open_ticket(tenant_id, **kwargs):
                return {"created": False, "ticket_id": "t", "forum_chat_id": None, "forum_thread_id": None}

            async def send(**kwargs):
                sent_to.append(kwargs["chat_id"])
                return {"ok": True, "message_id": 1}

            monkeypatch.setattr(nudge, "resolve_bot_binding_context", AsyncMock(return_value=binding))
            monkeypatch.setattr(nudge, "open_or_reuse_ticket", open_ticket)
            monkeypatch.setattr(nudge, "record_relayed_message", AsyncMock(return_value={}))
            monkeypatch.setattr(nudge, "send_telegram_text", send)
            monkeypatch.setattr(nudge.support, "_admin_for_kind", lambda kind: 42)
            monkeypatch.setattr(nudge.support, "ticket_label", lambda ticket: "#S-1")

            morning = dt.datetime(2026, 10, 24, 7, 0, tzinfo=UTC)  # 10:00 МСК
            # Staging делит базу с боем — не шлёт и ключ дня не занимает.
            monkeypatch.setenv("ENVIRONMENT", "staging")
            get_settings.cache_clear()
            assert await nudge.site_request_nudge_step(morning) == 0 and sent_to == []
            # Бой: оба письма ушли.
            monkeypatch.setenv("ENVIRONMENT", "production")
            get_settings.cache_clear()
            assert await nudge.site_request_nudge_step(morning) == 2
            assert len(sent_to) == 2
            # Тот же день — ничего; на следующий день заявки уже помечены, повторов нет.
            assert await nudge.site_request_nudge_step(morning + dt.timedelta(minutes=5)) == 0
            assert await nudge.site_request_nudge_step(morning + dt.timedelta(days=1)) == 0
            assert len(sent_to) == 2
            async with tenant_connection("whieda") as conn:
                keys = {row["notice_key"] for row in await fetch_all(conn, "select notice_key from service_notice_log")}
            assert keys == {"site_nudge:" + first, "site_nudge:" + second, nudge.DAILY_KEY}
            get_settings.cache_clear()

        db.run_with_app(proof)
