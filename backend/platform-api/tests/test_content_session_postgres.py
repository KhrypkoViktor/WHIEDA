"""Проверка сессии входа одним UPDATE … RETURNING на настоящей базе (24.10.2026).

Срок, отзыв, чужой арендатор (RLS у роли приложения) и last_seen_at — настоящим SQL по
настоящим миграциям; кэш в памяти — как в бою (отдельное подключение администратора читает
то, что записало приложение)."""

from __future__ import annotations

import datetime as dt
import uuid

import psycopg
import pytest

from tests.postgres_testkit import MIGRATIONS, temporary_database

SESSION_MIGRATIONS = (*MIGRATIONS, "platform_identity_journey_v1.sql", "platform_content_access_v1.sql")
LONG_AGO = dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)


def _seed(conn: psycopg.Connection, raw: str, *, tenant: str = "whieda", expires: str = "now() + interval '1 day'",
          revoked: bool = False) -> str:
    from app.content_access.service import _hash_value

    visitor = str(uuid.uuid4())
    conn.execute("insert into visitor_sessions (tenant_id, session_id) values (%s, %s::uuid)", (tenant, visitor))
    return conn.execute(
        f"insert into content_access_sessions (tenant_id, visitor_session_id, session_hash, telegram_user_id, expires_at, "
        f"revoked_at, last_seen_at) values (%s, %s::uuid, %s, 4242, {expires}, "
        f"{'now()' if revoked else 'null'}, %s) returning session_id::text",
        (tenant, visitor, _hash_value(raw), LONG_AGO),
    ).fetchone()[0]


@pytest.mark.integration
def test_one_statement_validation_keeps_every_rule_of_the_old_select_and_update():
    with temporary_database("whieda_session") as db:
        with psycopg.connect(db.admin_dsn, autocommit=True) as conn:
            db.apply_migrations(conn, SESSION_MIGRATIONS)
            good = _seed(conn, "good")
            expired = _seed(conn, "expired", expires="now() - interval '1 second'")
            revoked = _seed(conn, "revoked", revoked=True)
            _seed(conn, "nsp-token", tenant="nsp")
            db.grant_api_role(conn)

        def last_seen(session_id: str) -> dt.datetime:
            with psycopg.connect(db.admin_dsn, autocommit=True) as admin:
                return admin.execute(
                    "select last_seen_at from content_access_sessions where session_id = %s::uuid", (session_id,)
                ).fetchone()[0]

        async def proof() -> None:
            from app.content_access.service import (
                clear_session_cache,
                revoke_content_session,
                validate_content_session,
            )

            clear_session_cache()

            session = await validate_content_session("whieda", "good")
            assert session is not None
            assert str(session["session_id"]) == good
            assert session["tenant_id"] == "whieda"
            assert session["telegram_user_id"] == 4242
            assert session["scope"] == "telegram_verified"
            assert session["revoked_at"] is None
            assert session["expires_at"] > dt.datetime.now(dt.timezone.utc)
            assert last_seen(good) > LONG_AGO  # время последнего захода записано тем же запросом

            assert await validate_content_session("whieda", "expired") is None
            assert await validate_content_session("whieda", "revoked") is None
            assert await validate_content_session("whieda", "no-such-token") is None
            # Чужой арендатор: ни по RLS, ни по явному условию сессия не находится.
            assert await validate_content_session("whieda", "nsp-token") is None
            for untouched in (expired, revoked):
                assert last_seen(untouched) == LONG_AGO

            # Выход выбрасывает запись из кэша; следующая проверка идёт в базу и отказывает.
            assert await validate_content_session("whieda", "good") is not None
            assert await revoke_content_session("whieda", "good") is True
            assert await validate_content_session("whieda", "good") is None
            assert await revoke_content_session("whieda", "good") is False
            clear_session_cache()

        db.run_with_app(proof)
