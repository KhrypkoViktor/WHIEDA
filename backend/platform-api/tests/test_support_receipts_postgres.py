"""Служебные записи тикета (09.11.2026): «снова нажал» не чаще раза в сутки, первый файл клиента — один раз."""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

SUPPORT_MIGRATIONS = (*MIGRATIONS, "platform_support_tickets_v8.sql", "platform_support_forum_v9.sql", "platform_service_sales_v10.sql")


@pytest.mark.integration
def test_recent_system_note_and_first_file_lookups():
    with temporary_database("whieda_support_receipts") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SUPPORT_MIGRATIONS, twice=True)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import tenant_connection
            from app.support.service import has_recent_system_message, open_or_reuse_ticket, record_relayed_message, user_file_count

            ticket = await open_or_reuse_ticket(
                "whieda", channel_code="gemini", offer_code="gemini_6m", offer_title="Gemini",
                user_telegram_user_id=60001, user_chat_id=60001, user_display="Ольга", admin_telegram_user_id=688931415,
            )
            tid = str(ticket["ticket_id"])
            markers = ("снова нажал", "снова открыл вопрос")
            assert not await has_recent_system_message("whieda", ticket_id=tid, markers=markers)
            await record_relayed_message("whieda", ticket_id=tid, direction="system", text="Клиент WWC · Заявка #S-1 · Заказ: Gemini")
            assert not await has_recent_system_message("whieda", ticket_id=tid, markers=markers)
            await record_relayed_message(
                "whieda", ticket_id=tid, direction="system", text="Клиент WWC · Заявка #S-1 · клиент снова нажал «Заказать»"
            )
            assert await has_recent_system_message("whieda", ticket_id=tid, markers=markers)
            # Старше суток — уже не считается.
            async with tenant_connection("whieda") as conn:
                await conn.execute("update support_messages set created_at = now() - interval '25 hours' where text like '%снова нажал%'")
            assert not await has_recent_system_message("whieda", ticket_id=tid, markers=markers)

            assert await user_file_count("whieda", ticket_id=tid) == 0
            await record_relayed_message("whieda", ticket_id=tid, direction="user_to_admin", text="привет", source_chat_id=60001, source_message_id=1)
            assert await user_file_count("whieda", ticket_id=tid) == 0
            await record_relayed_message(
                "whieda", ticket_id=tid, direction="user_to_admin", text=None, telegram_file_id="F1", source_chat_id=60001, source_message_id=2
            )
            assert await user_file_count("whieda", ticket_id=tid) == 1

        db.run_with_app(proof)


@pytest.mark.integration
def test_services_payment_text_is_saved_in_default_showcase_and_cleared():
    with temporary_database("whieda_services_payment") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, twice=True)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.academy.showcases import get_services_payment_text, load_showcases, set_services_payment_text

            showcases = await load_showcases("whieda", fresh=True)
            assert any(s.is_default for s in showcases)
            assert await get_services_payment_text("whieda") == ""
            assert await set_services_payment_text("whieda", "Карта Т-Банк 1234\nПолучатель В. Х.") is True
            assert await get_services_payment_text("whieda") == "Карта Т-Банк 1234\nПолучатель В. Х."
            # Остальные настройки обращений не затронуты: добавляется только ключ services.
            showcase = next(s for s in await load_showcases("whieda") if s.is_default)
            assert showcase.support["services"] == {"payment_text": "Карта Т-Банк 1234\nПолучатель В. Х."}
            assert await set_services_payment_text("whieda", None) is True
            assert await get_services_payment_text("whieda") == ""

        db.run_with_app(proof)
