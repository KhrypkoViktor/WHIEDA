"""Regression: service intents must never attach product media (SERVICE-GREETING-NO-MEDIA)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.advisor.sql import formatters as fmt
from app.advisor.sql.engine import run_structured_query
from app.advisor.sql.text import detect_service_intent

SERVICE_CASES = [
    ("SERVICE-GREETING-NO-MEDIA", "привет"),
    ("SERVICE-GREETING-HELLO", "hello"),
    ("SERVICE-SMALLTALK-NO-MEDIA", "как дела"),
    ("SERVICE-CAPABILITIES-NO-MEDIA", "что ты умеешь"),
    ("SERVICE-HELP-NO-MEDIA", "помощь"),
]


def _assert_empty_media(media: dict) -> None:
    assert media.get("photo_url") in (None, "")
    assert media.get("videos") in (None, [])
    assert media.get("documents") in (None, [])
    if media.get("videos"):
        assert len(media["videos"]) == 0
    if media.get("documents"):
        assert len(media["documents"]) == 0


@pytest.mark.parametrize("case_id,question", SERVICE_CASES)
def test_detect_service_intent(case_id: str, question: str):
    assert detect_service_intent(question) is not None, case_id


@pytest.mark.parametrize("case_id,question", SERVICE_CASES)
@pytest.mark.asyncio
async def test_service_intent_response_has_no_media(case_id: str, question: str, whieda_tenant):
    @asynccontextmanager
    async def fake_tenant_connection(_tenant_id: str):
        yield object()

    with patch("app.advisor.sql.engine.tenant_connection", fake_tenant_connection):
        with patch(
            "app.advisor.sql.engine.repo.load_capability_response",
            AsyncMock(return_value="Текст сервисного ответа"),
        ):
            result = await run_structured_query(whieda_tenant, {"question": question}, f"trace-{case_id}")

    assert result["ok"] is True
    assert result["answer_mode"] == "structured_business"
    _assert_empty_media(result.get("media") or {})
    assert result["media"] == fmt.empty_media()


def test_empty_media_helper_is_stable():
    first = fmt.empty_media()
    second = fmt.empty_media()
    assert first == second
    first["videos"].append({"url": "https://evil.example"})
    assert second["videos"] == []
