"""Gemini sales on a real database: tariff, one sale per ticket, deposit, partner WWC$."""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

SALES_MIGRATIONS = (*MIGRATIONS, "platform_support_tickets_v8.sql", "platform_support_forum_v9.sql", "platform_service_sales_v10.sql")
KARINA = 2101187096
OWNER = 688931415


@pytest.mark.integration
def test_sale_lifecycle_deposit_and_partner_share():
    with temporary_database("whieda_service_sales") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SALES_MIGRATIONS, twice=True)
            conn.execute(
                """
                insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id, email) values
                  ('proof-elena', 'whieda', 'Елена', 364, '364', 'elena@example.com'),
                  ('proof-olga', 'whieda', 'Ольга', 525, '525', null),
                  ('telegram:whieda:437', 'whieda', 'Инна', 437, '437', null);
                insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode) values
                  ('elena', 'whieda', 'proof-elena', 'named'), ('olga', 'whieda', 'proof-olga', 'named');
                insert into partner_referral_attributions (tenant_id, invitee_actor_id, inviter_actor_id, invite_code, source) values
                  ('whieda', 'proof-olga', 'proof-elena', null, 'admin_manual'),
                  ('whieda', 'telegram:whieda:437', 'proof-olga', null, 'admin_manual');
                """
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.service_sales.service import (
                activate_sale, add_topup, confirm_topup, deposit_balance, get_tariff, month_report,
                partner_label, record_sale, set_tariff, suggest_partner_for_client,
            )
            from app.support.service import open_or_reuse_ticket

            # 1. Seeded tariff; the owner changes it by command.
            tariff = await get_tariff("whieda", "gemini_6m")
            assert (tariff["retail_minor"], tariff["wholesale_direct_minor"], tariff["wholesale_partner_minor"], tariff["partner_share_wusd_minor"]) == (399000, 299000, 249000, 500)
            await set_tariff("whieda", offer_code="gemini_18m", retail_minor=399000, wholesale_direct_minor=299000, wholesale_partner_minor=249000, partner_share_wusd_minor=500, updated_by=OWNER)

            # 2. Deposit: a top-up counts only after the administrator confirms it.
            pending = await add_topup("whieda", admin_telegram_user_id=KARINA, amount_minor=2000000, sent_by=OWNER)
            assert await deposit_balance("whieda", admin_telegram_user_id=KARINA) == 0
            confirmed = await confirm_topup("whieda", entry_id=str(pending["entry_id"]), confirmed_by=KARINA)
            assert confirmed["deposit_balance_minor"] == 2000000
            assert await confirm_topup("whieda", entry_id=str(pending["entry_id"]), confirmed_by=KARINA) is None

            # 3. Inna (brought by Olga) buys: the suggestion is Olga; a partner sale writes off 2 490 and credits 5 WWC$.
            ticket = await open_or_reuse_ticket(
                "whieda", channel_code="gemini", offer_code="gemini_6m", offer_title="Gemini Pro, 6 мес",
                user_telegram_user_id=437, user_chat_id=437, user_display="Инна", admin_telegram_user_id=KARINA,
            )
            suggestion = await suggest_partner_for_client("whieda", client_telegram_user_id=437)
            assert suggestion["partner_ref"] == "olga"
            sale = await record_sale("whieda", ticket=ticket, offer_code="gemini_6m", seller="partner", partner_ref="olga", admin_telegram_user_id=KARINA, created_by=KARINA)
            assert sale["idempotent"] is False and sale["owed_admin_minor"] == 249000 and sale["deposit_balance_minor"] == 2000000 - 249000
            assert sale["partner_bonus"]["amount_minor"] == 500 and sale["partner_bonus"]["balance_minor"] == 500
            assert sale["partner_bonus"]["telegram_chat_id"] == "525" and sale["owner_keeps_minor"] == 100000
            again = await record_sale("whieda", ticket=ticket, offer_code="gemini_6m", seller="partner", partner_ref="olga", admin_telegram_user_id=KARINA, created_by=KARINA)
            assert again["idempotent"] is True
            assert await deposit_balance("whieda", admin_telegram_user_id=KARINA) == 2000000 - 249000

            # 4. Olga buys for herself and the owner sold directly: 2 990 off, no partner share.
            own = await open_or_reuse_ticket(
                "whieda", channel_code="gemini", offer_code="gemini_6m", offer_title="Gemini Pro, 6 мес",
                user_telegram_user_id=525, user_chat_id=525, user_display="Ольга", admin_telegram_user_id=KARINA,
            )
            direct = await record_sale("whieda", ticket=own, offer_code="gemini_6m", seller="owner", partner_ref=None, admin_telegram_user_id=KARINA, created_by=OWNER)
            assert direct["owed_admin_minor"] == 299000 and direct["partner_bonus"] is None
            assert direct["deposit_balance_minor"] == 2000000 - 249000 - 299000

            # 5. Nameless labels: e-mail when known, a short hash otherwise.
            assert await partner_label("whieda", "elena") == "elena@example.com"
            assert await partner_label("whieda", "olga") == "olga"  # no e-mail yet: the site login

            # 6. Activation happens once; the month report sums it all.
            from datetime import datetime, timezone
            activated = await activate_sale("whieda", sale_id=str(sale["sale_id"]), activated_until=datetime(2027, 3, 16, tzinfo=timezone.utc))
            assert activated["status"] == "activated"
            assert await activate_sale("whieda", sale_id=str(sale["sale_id"]), activated_until=datetime(2027, 3, 16, tzinfo=timezone.utc)) is None
            report = await month_report("whieda", admin_telegram_user_id=KARINA, since=datetime(2026, 1, 1, tzinfo=timezone.utc))
            assert report["sales"] == 2 and report["retail_minor"] == 798000 and report["owed_minor"] == 548000 and report["owner_minor"] == 200000
            assert report["by_partner"] == [{"partner_ref": "olga", "sales": 1, "share_wusd_minor": 500}]
            assert report["deposit_balance_minor"] == 1452000 and report["low_balance"] is False

        db.run_with_app(proof)


@pytest.mark.integration
def test_licence_reminder_and_low_deposit_notice_fire_once():
    from datetime import datetime, timedelta, timezone
    from unittest.mock import AsyncMock, patch

    with temporary_database("whieda_service_notices") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SALES_MIGRATIONS)
            conn.execute(
                """
                insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id) values
                  ('telegram:whieda:437', 'whieda', 'Инна', 437, '437');
                """
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.service_sales.reminders import send_service_notices
            from app.service_sales.service import activate_sale, record_sale
            from app.support.service import open_or_reuse_ticket

            ticket = await open_or_reuse_ticket(
                "whieda", channel_code="gemini", offer_code="gemini_6m", offer_title="Gemini Pro, 6 мес",
                user_telegram_user_id=437, user_chat_id=437, user_display="Инна", admin_telegram_user_id=KARINA,
            )
            sale = await record_sale("whieda", ticket=ticket, offer_code="gemini_6m", seller="owner", partner_ref=None, admin_telegram_user_id=KARINA, created_by=OWNER)
            now = datetime.now(timezone.utc)
            await activate_sale("whieda", sale_id=str(sale["sale_id"]), activated_until=now + timedelta(days=5))
            send = AsyncMock(return_value={"ok": True, "message_id": 1})
            with patch("app.service_sales.reminders.send_telegram_text", send):
                first = await send_service_notices(tenant_id="whieda", binding_id="whieda-advisor-bot", bot_token="t", admin_telegram_user_id=KARINA, owner_telegram_user_id=OWNER, now=now)
                second = await send_service_notices(tenant_id="whieda", binding_id="whieda-advisor-bot", bot_token="t", admin_telegram_user_id=KARINA, owner_telegram_user_id=OWNER, now=now)
            # Deposit is −2 990 (no top-ups): below one licence → one notice; the licence nudge once.
            assert first == {"licence_reminders": 1, "low_deposit_notice": True}
            assert second == {"licence_reminders": 0, "low_deposit_notice": False}
            chats = sorted(c.kwargs["chat_id"] for c in send.await_args_list)
            assert chats == sorted(["437", str(KARINA), str(OWNER)])
            assert "заканчивается" in [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == "437"][0]["text"]

        db.run_with_app(proof)
