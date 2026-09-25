"""add_lead_card_for_public_id: карточка по заявке, записанной мимо save_lead (n8n)."""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import temporary_database
from tests.test_crm_postgres import LEAD_TABLES, SEED  # таблицы лидов и акторы как в основном тесте CRM


@pytest.mark.integration
def test_card_from_public_id_is_idempotent_and_skips_staging(monkeypatch):
    monkeypatch.setenv("PLATFORM_CRM_LEAD_CARDS", "true")
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "*")
    with temporary_database("whieda_crm_internal") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(LEAD_TABLES)
            conn.execute("alter table website_leads add column deleted_at timestamptz")  # есть на бою, нет в схеме теста CRM
            conn.execute(SEED)
            conn.execute(
                """
                insert into website_leads (public_id, tenant_id, name, contact, product_name, page_url,
                  attributed_owner_id, assigned_owner_id, idempotency_key, consent_version)
                values
                  ('L-PROD0001', 'whieda', 'Ольга', '+7 999 111-22-33', 'Стельки', 'https://igor.wwc.best/catalog/', 'igor-actor', 'igor-actor', 'k1', 'v1'),
                  ('L-STAG0001', 'whieda', 'Тест', '+7 999 111-22-34', 'Стельки', 'https://staging.wwc.best/catalog/', 'igor-actor', 'igor-actor', 'k2', 'v1');
                """
            )
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.crm import service as crm
            from app.db import fetch_all, tenant_connection
            from app.settings import get_settings

            get_settings.cache_clear()

            async def rows(query: str, params: tuple = ()) -> list[dict]:
                async with tenant_connection("whieda") as conn:
                    return [dict(r) for r in await fetch_all(conn, query, params)]

            # Владелец ещё не открывал ежедневник — карточки нет, ответ спокойный.
            first = await crm.add_lead_card_for_public_id("whieda", "L-PROD0001")
            assert first == {"ok": True, "card_id": None, "reason": "skipped"}

            await crm.get_or_create_account("whieda", 7001)  # igor-actor открыл ежедневник
            made = await crm.add_lead_card_for_public_id("whieda", "L-PROD0001")
            assert made["ok"] and made["card_id"]
            again = await crm.add_lead_card_for_public_id("whieda", "L-PROD0001")  # n8n повторил вызов
            assert again["card_id"] is None
            cards = await rows("select name, phone_e164, source, status from crm_contacts order by created_at")
            assert cards == [{"name": "Ольга", "phone_e164": "+79991112233", "source": "сайт", "status": "new"}]

            assert await crm.add_lead_card_for_public_id("whieda", "L-STAG0001") == {"ok": True, "card_id": None, "reason": "staging_lead"}
            assert await crm.add_lead_card_for_public_id("whieda", "L-NOPE") == {"ok": False, "error": "lead_not_found"}
            assert len(await rows("select 1 from crm_contacts")) == 1

        db.run_with_app(proof)
