"""Marketing opt-in on real PostgreSQL (V17, 26.09.2026).

  * the migration adds ``website_leads.marketing_consent`` (false by default) and
    ``marketing_consent_at`` to the legacy leads table, and creates
    ``telegram_marketing_consents`` — twice without error;
  * the bot's answer is one row per person: «yes» sets ``opted_in`` and the date,
    «no» keeps the opt-in date as history and stamps ``opted_out_at``;
    a first answer «no» is recorded too, so the question is not asked again;
  * the recipient filter the broadcast worker uses (``opted_in``) sees only the
    people who said yes, and the API role sees only its own tenant (RLS).
"""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import temporary_database

# The legacy leads table the way n8n created it (whieda_website_leads_p0_v1.sql),
# reduced to the columns this test touches. It is not part of the testkit chain,
# so V17 must find it here to prove `alter table if exists` really adds the columns.
LEGACY_LEADS = """
create extension if not exists pgcrypto;
create table website_leads (
  lead_id uuid primary key default gen_random_uuid(),
  tenant_id text not null,
  name text not null,
  contact text not null,
  product_name text not null,
  idempotency_key text not null,
  consent_at timestamptz not null default now(),
  consent_version text not null default 'website-order-v1',
  created_at timestamptz not null default now(),
  unique (tenant_id, idempotency_key)
);
"""


@pytest.mark.integration
def test_marketing_consent_columns_rows_and_rls():
    with temporary_database("whieda_marketing_consent") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            conn.execute(LEGACY_LEADS)
            db.apply_migrations(conn, twice=True)

            columns = {
                row[0]: (row[1], row[2])
                for row in conn.execute(
                    "select column_name, is_nullable, column_default from information_schema.columns "
                    "where table_name = 'website_leads' and column_name like 'marketing_%'"
                )
            }
            assert columns["marketing_consent"] == ("NO", "false")
            assert columns["marketing_consent_at"][0] == "YES"
            conn.execute(
                "insert into website_leads (tenant_id, name, contact, product_name, idempotency_key) "
                "values ('whieda', 'Аня', '+79990000000', 'Стельки', 'lead-1')"
            )
            assert conn.execute("select marketing_consent, marketing_consent_at from website_leads").fetchone() == (
                False,
                None,
            )
            assert conn.execute(
                "select relrowsecurity from pg_class where relname = 'telegram_marketing_consents'"
            ).fetchone() == (True,)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.db import fetch_all, tenant_connection
            from app.telegram.consent import (
                MARKETING_CONSENT_VERSION,
                marketing_consent_state,
                record_marketing_consent,
            )

            async def opted_in_ids(tenant: str = "whieda") -> list[int]:
                async with tenant_connection(tenant) as conn:
                    rows = await fetch_all(
                        conn,
                        "select telegram_user_id from telegram_marketing_consents where opted_in order by 1",
                    )
                return [row["telegram_user_id"] for row in rows]

            assert await marketing_consent_state("whieda", 7001) is None

            yes = await record_marketing_consent("whieda", telegram_user_id=7001, telegram_chat_id=7001, opted_in=True)
            assert yes["opted_in"] is True and yes["opted_in_at"] is not None and yes["opted_out_at"] is None
            assert await marketing_consent_state("whieda", 7001) is True
            assert await opted_in_ids() == [7001]

            # Отказ: opted_in false, дата отказа; дата согласия остаётся как история.
            no = await record_marketing_consent("whieda", telegram_user_id=7001, telegram_chat_id=7001, opted_in=False)
            assert no["opted_in"] is False and no["opted_out_at"] is not None
            assert no["opted_in_at"] == yes["opted_in_at"]
            assert await marketing_consent_state("whieda", 7001) is False
            assert await opted_in_ids() == []

            # Первый ответ «нет» тоже записан — вопрос больше не задаётся.
            first_no = await record_marketing_consent("whieda", telegram_user_id=7002, telegram_chat_id=7002, opted_in=False)
            assert first_no["opted_in"] is False and first_no["opted_in_at"] is None and first_no["opted_out_at"] is not None
            assert await marketing_consent_state("whieda", 7002) is False

            again = await record_marketing_consent("whieda", telegram_user_id=7002, telegram_chat_id=7002, opted_in=True)
            assert again["opted_in"] is True and again["opted_in_at"] is not None
            assert await opted_in_ids() == [7002]

            async with tenant_connection("whieda") as conn:
                rows = await fetch_all(conn, "select consent_version, source from telegram_marketing_consents")
            assert {(row["consent_version"], row["source"]) for row in rows} == {(MARKETING_CONSENT_VERSION, "bot")}

            # RLS: другой тенант не видит этих строк и не задаёт вопрос заново по чужим данным.
            assert await marketing_consent_state("other-tenant", 7002) is None
            assert await opted_in_ids("other-tenant") == []

        db.run_with_app(proof)
