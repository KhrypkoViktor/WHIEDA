"""Deep coach route must degrade safely when Dify is unavailable."""

from __future__ import annotations

import pytest

from app.advisor.deep_provider import DisabledDeepAnswerProvider


@pytest.mark.asyncio
async def test_dify_down_disabled_provider_still_responds():
    provider = DisabledDeepAnswerProvider()
    result = await provider.answer("whieda", "помоги с возражением", {"session": "s1"})
    assert result["ok"] is True
    assert result["answer_text"]
    assert "trace_id" not in result or result.get("error_id") is None
