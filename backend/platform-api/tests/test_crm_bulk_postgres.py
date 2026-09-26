"""bulk_create_contacts against real PostgreSQL: the phone-book / .vcf / .csv import.

One request is one transaction: duplicates (by phone_e164 inside the account,
and inside the batch itself) are skipped and named, an empty name is skipped
with a reason, 500 rows go in at once, and a row the database refuses rolls
the whole batch back — nothing is half-imported.
"""

from __future__ import annotations

import psycopg
import pytest

from tests.postgres_testkit import temporary_database
from tests.test_crm_postgres import SEED

BOOM_TRIGGER = """
create or replace function crm_test_refuse_boom() returns trigger language plpgsql as $$
begin
  if new.name = 'BOOM' then
    raise exception 'refused by test trigger' using errcode = 'check_violation';
  end if;
  return new;
end $$;
create trigger crm_test_refuse_boom before insert on crm_contacts
  for each row execute function crm_test_refuse_boom();
"""


@pytest.mark.integration
def test_bulk_import_skips_duplicates_and_is_one_transaction(monkeypatch):
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "*")
    monkeypatch.delenv("PLATFORM_DISABLED_FEATURES", raising=False)
    with temporary_database("whieda_crm_bulk") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn)
            conn.execute(SEED)
            conn.execute(BOOM_TRIGGER)
            db.grant_api_role(conn)

        async def proof() -> None:
            from app.crm import service as crm
            from app.db import fetch_all, tenant_connection
            from app.settings import get_settings

            get_settings.cache_clear()

            async def rows(query: str, params: tuple = (), tenant: str = "whieda") -> list[dict]:
                async with tenant_connection(tenant) as conn:
                    return [dict(r) for r in await fetch_all(conn, query, params)]

            igor = await crm.get_or_create_account("whieda", 7001)
            petr = await crm.get_or_create_account("whieda", 7002)
            today = igor["today"]
            anna = await crm.create_contact("whieda", igor, name="Анна", phone="8 928 672-92-88")
            # The same number in Пётр's diary is not a duplicate for Игорь.
            await crm.create_contact("whieda", petr, name="Анна у Петра", phone="+7 999 000-00-01")

            result = await crm.bulk_create_contacts("whieda", igor, [
                {"name": " Аня  Петрова ", "phone": "+7 (928) 672-92-88", "source": "телефон"},   # already there
                {"name": "Борис", "phone": "+7 999 000-00-01", "source": None},                     # Пётр's, not ours
                {"name": "Вера", "phone": "8 999 000 00 02"},                                       # new
                {"name": "Вера 2", "phone": "+79990000002"},                                         # duplicate inside the batch
                {"name": "", "phone": "+7 999 000-00-03"},                                          # no name
                {"name": "   ", "phone": ""},                                                       # no name, no phone
                {"name": "Глеб", "phone": ""},                                                      # no phone: fine
                {"name": "Дарья", "phone": "12-34"},                                                # unrecognised: kept raw
                {"name": "Егор", "phone": None, "source": "спортзал"},                              # no phone: fine, never a duplicate of Глеб
            ])
            assert result["created"] == 5
            assert [(s["name"], s["phone"], s["reason"]) for s in result["skipped"]] == [
                ("Аня Петрова", "+7 (928) 672-92-88", "duplicate"),
                ("Вера 2", "+79990000002", "duplicate"),
                ("", "+7 999 000-00-03", "name_required"),
                ("", "", "name_required"),
            ]
            assert result["skipped"][0]["contact_id"] == anna["id"]
            vera = await rows("select contact_id::text as id from crm_contacts where name = 'Вера'")
            assert result["skipped"][1]["contact_id"] == vera[0]["id"]
            assert result["skipped"][2]["contact_id"] is None

            mine = await rows(
                "select name, phone_e164, phone_raw, source, status, next_step, next_at from crm_contacts "
                "where account_id = %s::uuid order by name", (igor["account_id"],)
            )
            assert [(r["name"], r["phone_e164"], r["phone_raw"], r["source"]) for r in mine] == [
                ("Анна", "+79286729288", "8 928 672-92-88", ""),
                ("Борис", "+79990000001", "+7 999 000-00-01", ""),
                ("Вера", "+79990000002", "8 999 000 00 02", ""),
                ("Глеб", None, None, ""),
                ("Дарья", None, "12-34", ""),
                ("Егор", None, None, "спортзал"),
            ]
            assert {(r["status"], r["next_step"], r["next_at"]) for r in mine} == {("new", "invite", today)}

            # Import again: everything with a number is already there, the rest is added again.
            again = await crm.bulk_create_contacts("whieda", igor, [
                {"name": "Вера", "phone": "+7 999 000-00-02"}, {"name": "Глеб", "phone": ""},
            ])
            assert again["created"] == 1
            assert [(s["name"], s["reason"]) for s in again["skipped"]] == [("Вера", "duplicate")]

            # 500 rows in one call.
            big = await crm.bulk_create_contacts(
                "whieda", igor, [{"name": f"Контакт {i}", "phone": f"+7 900 {i:07d}"} for i in range(500)]
            )
            assert big == {"created": 500, "skipped": []}
            count = await rows("select count(*) as n from crm_contacts where account_id = %s::uuid", (igor["account_id"],))
            assert count == [{"n": 7 + 500}]

            # One transaction: a row the database refuses (trigger above) rolls back the whole batch.
            with pytest.raises(psycopg.errors.CheckViolation):
                await crm.bulk_create_contacts("whieda", igor, [
                    {"name": "Жанна", "phone": "+7 901 000-00-01"}, {"name": "BOOM", "phone": "+7 901 000-00-02"},
                ])
            assert await rows("select 1 from crm_contacts where name in ('Жанна', 'BOOM')") == []

            # Another tenant sees nothing (RLS), Пётр's diary is untouched.
            assert await rows("select contact_id from crm_contacts", tenant="other") == []
            assert [c["name"] for c in await crm.list_contacts("whieda", petr)] == ["Анна у Петра"]

        db.run_with_app(proof)
