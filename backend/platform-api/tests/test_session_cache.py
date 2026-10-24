"""Проверка сессии входа: один запрос к базе, а дальше 30 секунд из памяти (24.10.2026).

Подмена базы считает подключения и запросы. Настоящий SQL (срок, отзыв, арендатор под RLS)
проверяет ``test_content_session_postgres.py``."""

from __future__ import annotations

import datetime as dt
import re

import pytest

import app.content_access.service as service
from app.content_access.service import (
    SESSION_CACHE_TTL_SEC,
    clear_session_cache,
    revoke_content_session,
    validate_content_session,
)
from tests.fake_db import FakeDb

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
RAW = "raw-session-token"


class _Sessions(FakeDb):
    """Таблица content_access_sessions: UPDATE … RETURNING и отзыв понимают условия настоящего SQL."""

    def __init__(self, clock: "_Clock") -> None:
        super().__init__(self._answer)
        self.clock = clock
        self.rows: dict[str, dict] = {}
        self.seen_at: dict[str, dt.datetime] = {}
        self.on_validate = None  # вызывается между UPDATE и записью в кэш (гонка с выходом)

    def add(self, raw: str, *, tenant_id: str = "whieda", expires_in: float = 3600, revoked: bool = False) -> dict:
        digest = service._hash_value(raw)
        row = {
            "session_id": f"sid-{len(self.rows) + 1}",
            "tenant_id": tenant_id,
            "visitor_session_id": "visitor-1",
            "telegram_user_id": 777,
            "scope": "telegram_verified",
            "expires_at": self.clock.utc + dt.timedelta(seconds=expires_in),
            "revoked_at": self.clock.utc if revoked else None,
        }
        self.rows[digest] = row
        return row

    def _answer(self, query: str, params) -> list[dict]:
        if query.startswith("update content_access_sessions set last_seen_at"):
            session_hash, tenant_id, now = params
            row = self.rows.get(session_hash)
            if not row or row["tenant_id"] != tenant_id or row["revoked_at"] is not None or row["expires_at"] < now:
                return []
            self.seen_at[row["session_id"]] = self.clock.utc
            if self.on_validate:
                self.on_validate(session_hash, tenant_id)
            return [dict(row)]
        if query.startswith("update content_access_sessions set revoked_at"):
            session_hash, tenant_id = params
            row = self.rows.get(session_hash)
            if not row or row["tenant_id"] != tenant_id or row["revoked_at"] is not None:
                return []
            row["revoked_at"] = self.clock.utc
            return [{"session_id": row["session_id"]}]
        raise AssertionError(f"unexpected statement: {query}")


class _Clock:
    """Одни часы и для срока сессии (UTC), и для времени жизни записи в кэше."""

    def __init__(self) -> None:
        self.utc = T0
        self.mono = 5000.0

    def advance(self, seconds: float) -> None:
        self.utc += dt.timedelta(seconds=seconds)
        self.mono += seconds

    def __call__(self) -> float:  # app.ttl_cache._now
        return self.mono


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr("app.ttl_cache._now", fake)
    monkeypatch.setattr(service, "_utcnow", lambda: fake.utc)
    return fake


@pytest.fixture
def db(monkeypatch, clock):
    fake = _Sessions(clock)
    monkeypatch.setattr(service, "tenant_connection", fake.tenant_connection)
    clear_session_cache()
    yield fake
    clear_session_cache()


@pytest.mark.asyncio
async def test_validation_is_one_statement_in_one_transaction(db):
    db.add(RAW)

    session = await validate_content_session("whieda", RAW)

    assert session is not None
    assert db.opened == 1
    assert len(db.statements) == 1
    statement = db.statements[0]
    assert statement.startswith("update content_access_sessions set last_seen_at = now()")
    assert re.search(r"\breturning\b", statement)
    assert not re.search(r"\bselect\b", statement)


@pytest.mark.asyncio
async def test_session_payload_keeps_the_columns_callers_read(db):
    db.add(RAW)

    session = await validate_content_session("whieda", RAW)

    assert set(session) == {
        "session_id",
        "tenant_id",
        "visitor_session_id",
        "telegram_user_id",
        "scope",
        "expires_at",
        "revoked_at",
    }
    assert session["telegram_user_id"] == 777
    assert session["scope"] == "telegram_verified"
    assert session["revoked_at"] is None


@pytest.mark.asyncio
async def test_raw_token_is_trimmed_and_only_its_hash_reaches_the_database(db):
    db.add(RAW)

    assert await validate_content_session("whieda", f"  {RAW}\n") is not None
    assert db.conns[0].params[0][0] == service._hash_value(RAW)
    assert RAW not in str(db.conns[0].params)


@pytest.mark.asyncio
async def test_second_validation_within_ttl_does_no_sql(db, clock):
    db.add(RAW)
    first = await validate_content_session("whieda", RAW)

    clock.advance(SESSION_CACHE_TTL_SEC - 1)
    second = await validate_content_session("whieda", RAW)

    assert second == first
    assert db.opened == 1
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_validation_after_ttl_goes_to_the_database_again(db, clock):
    db.add(RAW)
    await validate_content_session("whieda", RAW)

    clock.advance(SESSION_CACHE_TTL_SEC + 1)
    await validate_content_session("whieda", RAW)

    assert db.opened == 2
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_cached_session_is_a_copy(db):
    db.add(RAW)
    first = await validate_content_session("whieda", RAW)
    first["scope"] = "partner_verified"  # правка в одном запросе

    second = await validate_content_session("whieda", RAW)

    assert second["scope"] == "telegram_verified"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["unknown", "revoked", "expired", "other_tenant"],
)
async def test_invalid_sessions_are_refused_and_never_cached(db, case):
    if case == "revoked":
        db.add(RAW, revoked=True)
    elif case == "expired":
        db.add(RAW, expires_in=-1)
    elif case == "other_tenant":
        db.add(RAW, tenant_id="nsp")
    for _ in range(2):
        assert await validate_content_session("whieda", RAW) is None

    assert db.opened == 2  # отказ каждый раз проверяется заново
    assert db.seen_at == {}  # last_seen_at у недействительной сессии не трогается


@pytest.mark.asyncio
async def test_cache_never_serves_a_session_to_another_tenant(db):
    db.add(RAW, tenant_id="whieda")
    assert await validate_content_session("whieda", RAW) is not None

    assert await validate_content_session("nsp", RAW) is None
    assert db.opened == 2


@pytest.mark.asyncio
async def test_cache_lives_no_longer_than_the_session(db, clock):
    db.add(RAW, expires_in=5)
    assert await validate_content_session("whieda", RAW) is not None

    clock.advance(6)  # сессия истекла, хотя до 30 секунд кэша ещё далеко
    assert await validate_content_session("whieda", RAW) is None
    assert db.opened == 2


@pytest.mark.asyncio
async def test_logout_evicts_the_cached_session(db):
    db.add(RAW)
    assert await validate_content_session("whieda", RAW) is not None
    assert await validate_content_session("whieda", RAW) is not None
    assert db.opened == 1

    assert await revoke_content_session("whieda", RAW) is True

    assert await validate_content_session("whieda", RAW) is None
    assert db.opened == 3  # проверка, выход, повторная проверка уже в базе


@pytest.mark.asyncio
async def test_logout_of_someone_elses_token_leaves_other_sessions_cached(db):
    db.add(RAW)
    db.add("other-token")
    await validate_content_session("whieda", RAW)

    await revoke_content_session("whieda", "other-token")
    await validate_content_session("whieda", RAW)

    assert db.opened == 2  # проверка RAW и выход; повторная проверка RAW — из памяти


@pytest.mark.asyncio
async def test_logout_between_the_update_and_the_cache_write_does_not_resurrect_the_session(db):
    """Выход пришёл, пока параллельная проверка уже прошла в базе: в кэш она не попадает."""
    db.add(RAW)
    digest = service._hash_value(RAW)
    db.on_validate = lambda session_hash, tenant_id: service._evict_cached_session(tenant_id, session_hash)

    assert await validate_content_session("whieda", RAW) is not None
    db.on_validate = None
    assert await validate_content_session("whieda", RAW) is not None

    assert db.opened == 2  # первая проверка не закэширована
    assert digest in db.rows


@pytest.mark.asyncio
async def test_revoke_of_an_unknown_token_is_false_and_harmless(db):
    assert await revoke_content_session("whieda", "never-issued") is False
