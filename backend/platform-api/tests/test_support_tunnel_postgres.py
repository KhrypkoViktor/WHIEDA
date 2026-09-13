"""Support tunnel on a real database: tickets, relayed messages, reply routing."""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

SUPPORT_MIGRATIONS = (*MIGRATIONS, "platform_support_tickets_v8.sql")


@pytest.mark.integration
def test_ticket_lifecycle_and_reply_routing():
    with temporary_database("whieda_support") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SUPPORT_MIGRATIONS, twice=True)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.support.service import (
                close_ticket,
                find_ticket_by_admin_message,
                get_open_ticket_for_user,
                list_open_tickets_for_admin,
                open_or_reuse_ticket,
                record_relayed_message,
            )

            # 1. An order opens a ticket; a second order from the same person joins it.
            first = await open_or_reuse_ticket(
                "whieda", channel_code="gemini", offer_code="gemini_18m", offer_title="Gemini Pro, 18 мес",
                user_telegram_user_id=60001, user_chat_id=60001, user_display="Ольга (@olga)",
                admin_telegram_user_id=688931415,
            )
            assert first["created"] is True and first["status"] == "open"
            assert int(first["ticket_no"]) >= 1
            again = await open_or_reuse_ticket(
                "whieda", channel_code="gemini", offer_code="gemini_12m", offer_title="Gemini Pro, 1 год",
                user_telegram_user_id=60001, user_chat_id=60001, user_display="Ольга (@olga)",
                admin_telegram_user_id=688931415,
            )
            assert again["created"] is False and again["ticket_id"] == first["ticket_id"]

            # 2. The bot delivered the user's text to the admin as message 501; a
            #    Reply to 501 resolves back to the ticket.
            await record_relayed_message(
                "whieda", ticket_id=first["ticket_id"], direction="user_to_admin", text="Хочу за 4490",
                source_chat_id=60001, source_message_id=11, delivered_chat_id=688931415, delivered_message_id=501,
            )
            routed = await find_ticket_by_admin_message("whieda", admin_chat_id=688931415, message_id=501)
            assert routed["ticket_id"] == first["ticket_id"] and int(routed["user_chat_id"]) == 60001
            assert await find_ticket_by_admin_message("whieda", admin_chat_id=688931415, message_id=999) is None

            # 3. Telegram retries: the same source message is not recorded twice.
            duplicate = await record_relayed_message(
                "whieda", ticket_id=first["ticket_id"], direction="user_to_admin", text="Хочу за 4490",
                source_chat_id=60001, source_message_id=11, delivered_chat_id=688931415, delivered_message_id=502,
            )
            assert duplicate["duplicate"] is True

            # 4. Open tickets for the admin, newest activity first; user lookup.
            other = await open_or_reuse_ticket(
                "whieda", channel_code="gemini", offer_code=None, offer_title=None,
                user_telegram_user_id=60002, user_chat_id=60002, user_display="Иван",
                admin_telegram_user_id=688931415,
            )
            open_for_admin = await list_open_tickets_for_admin("whieda", admin_telegram_user_id=688931415)
            assert [t["user_telegram_user_id"] for t in open_for_admin] == [60002, 60001]
            assert (await get_open_ticket_for_user("whieda", user_telegram_user_id=60002))["ticket_id"] == other["ticket_id"]

            # 5. Closing frees the slot: the next order opens a fresh ticket.
            closed = await close_ticket("whieda", ticket_id=first["ticket_id"], closed_by="admin")
            assert closed["status"] == "closed"
            assert await get_open_ticket_for_user("whieda", user_telegram_user_id=60001) is None
            fresh = await open_or_reuse_ticket(
                "whieda", channel_code="gemini", offer_code="gemini_18m", offer_title="Gemini Pro, 18 мес",
                user_telegram_user_id=60001, user_chat_id=60001, user_display="Ольга (@olga)",
                admin_telegram_user_id=688931415,
            )
            assert fresh["created"] is True and fresh["ticket_id"] != first["ticket_id"]
            assert int(fresh["ticket_no"]) > int(first["ticket_no"])

        db.run_with_app(proof)
