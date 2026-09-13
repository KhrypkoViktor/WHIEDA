"""The site→Telegram link exchange must survive a failing memory write.

The token is marked used and the identity link is committed before the memory
fact is written. If that later write fails (production has no
``user_memory_facts`` table yet), the person must still get their link result —
otherwise the token is burnt and a retry is impossible.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.identity.service import LinkTokenExchangeResult, exchange_telegram_link_token


class _FakeCursor:
    def __init__(self) -> None:
        self.statements: list[str] = []

    async def __aenter__(self) -> "_FakeCursor":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, sql: str, params: object = None) -> None:
        self.statements.append(" ".join(sql.split()).lower())


class _FakeConn:
    def __init__(self) -> None:
        self.cursor_obj = _FakeCursor()

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj


@pytest.mark.anyio
async def test_exchange_returns_link_when_memory_write_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    conn = _FakeConn()
    token_row = {
        "token_id": "tok-1",
        "session_id": "11111111-1111-1111-1111-111111111111",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "used_at": None,
        "first_ref": "olga-samtsova",
        "attributed_owner_id": "olga-samtsova",
        "assigned_owner_id": None,
        "journey_type": "referral",
        "context": {"last_product_sku": "SKU-1", "last_product_name": "Set"},
    }

    @asynccontextmanager
    async def fake_conn(tenant_id: str):
        yield conn

    monkeypatch.setattr("app.identity.service.tenant_connection", fake_conn)
    monkeypatch.setattr("app.identity.service.fetch_one", AsyncMock(return_value=token_row))
    monkeypatch.setattr(
        "app.identity.service.load_public_ref",
        AsyncMock(return_value={"owner_id": "olga-samtsova", "public_profile": {"display_name": "Ольга"}}),
    )
    memory = AsyncMock(side_effect=RuntimeError('relation "user_memory_facts" does not exist'))

    with patch("app.memory.service.upsert_memory_fact", memory), caplog.at_level(logging.WARNING):
        result = await exchange_telegram_link_token(
            "whieda", "raw-token", telegram_user_id=200, telegram_chat_id=200
        )

    assert isinstance(result, LinkTokenExchangeResult)
    assert result.first_ref == "olga-samtsova"
    assert result.mentor_display_name == "Ольга"
    # The durable part happened: token used, link written, event recorded.
    joined = " ".join(conn.cursor_obj.statements)
    assert "update identity_link_tokens set used_at" in joined
    assert "insert into telegram_identity_links" in joined
    assert "insert into interaction_events" in joined
    # The failure is loud in logs, not swallowed silently.
    memory.assert_awaited()
    assert any(r.getMessage() == "identity_exchange_memory_failed" for r in caplog.records)
