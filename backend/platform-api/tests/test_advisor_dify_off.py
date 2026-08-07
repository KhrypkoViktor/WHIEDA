"""Structure Basic must never call Dify/RAG providers."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.advisor.sql.engine import run_structured_query


@pytest.mark.asyncio
async def test_structured_greeting_never_invokes_deep_provider(whieda_tenant):
    deep = AsyncMock(side_effect=AssertionError("Dify must not be called"))

    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch(
            "app.advisor.sql.engine.repo.load_capability_response",
            AsyncMock(return_value="Здравствуйте"),
        ):
            result = await run_structured_query(whieda_tenant, {"question": "привет"}, "dify-off")

    assert result["route"] == "structured"
    deep.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_deep_provider_returns_structured_fallback():
    from app.advisor.deep_provider import DisabledDeepAnswerProvider

    provider = DisabledDeepAnswerProvider()
    result = await provider.answer("whieda", "коуч", {})
    assert result["route"] == "structured"
    assert result["answer_mode"] == "fallback"
