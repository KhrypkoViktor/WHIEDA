"""Брошенные заявки продления закрываются сами (владелец, 09.11.2026)."""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import temporary_database


@pytest.mark.integration
def test_stale_renewal_requests_are_cancelled_but_fresh_and_paid_stay():
    with temporary_database("whieda_renewal_stale") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(
                """
                insert into lead_actors (actor_id, tenant_id, display_name, telegram_chat_id) values
                  ('a-old', 'whieda', 'Татьяна', '6001'), ('a-new', 'whieda', 'Ольга', '6002'),
                  ('a-pending', 'whieda', 'Вера', '6003'), ('a-done', 'whieda', 'Нина', '6004');
                insert into referral_profiles (ref_code, tenant_id, owner_id, display_mode, enabled) values
                  ('old', 'whieda', 'a-old', 'named', true), ('new', 'whieda', 'a-new', 'named', true),
                  ('pending', 'whieda', 'a-pending', 'named', true), ('done', 'whieda', 'a-done', 'named', true);
                insert into partner_renewal_requests (tenant_id, actor_id, ref_code, status, updated_at) values
                  ('whieda', 'a-old', 'old', 'awaiting_payment', now() - interval '30 hours'),
                  ('whieda', 'a-new', 'new', 'awaiting_payment', now() - interval '3 hours'),
                  ('whieda', 'a-pending', 'pending', 'awaiting_period', now() - interval '25 hours'),
                  ('whieda', 'a-done', 'done', 'rejected', now() - interval '90 hours');
                """
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, tenant_connection
            from app.renewal_requests.service import cancel_stale_renewal_requests

            assert await cancel_stale_renewal_requests("whieda") == 2
            assert await cancel_stale_renewal_requests("whieda") == 0
            async with tenant_connection("whieda") as conn:
                rows = await fetch_all(conn, "select ref_code, status from partner_renewal_requests order by ref_code")
            assert {r["ref_code"]: r["status"] for r in rows} == {
                "done": "rejected", "new": "awaiting_payment", "old": "cancelled", "pending": "cancelled",
            }

        db.run_with_app(proof)
