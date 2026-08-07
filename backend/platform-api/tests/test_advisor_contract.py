from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_advisor_greeting(client, monkeypatch):
    from app import settings as settings_module

    settings_module.get_settings.cache_clear()
    monkeypatch.setenv("CORE_ROUTE_ADVISOR", "core")
    monkeypatch.setattr("app.advisor.service._upsert_session_context", AsyncMock())
    monkeypatch.setattr(
        "app.advisor.service.run_structured_query",
        AsyncMock(
            return_value={
                "ok": True,
                "answer_text": "Здравствуйте!",
                "answer_mode": "structured_business",
                "route": "structured",
                "product": None,
                "media": {"photo_url": None, "videos": [], "documents": []},
                "clarifications": [],
                "sources": [],
                "context": {},
                "error_id": None,
                "trace_id": "test",
            }
        ),
    )

    response = await client.post(
        "/v1/advisor/query",
        json={"session": "s1", "question": "Привет", "tenant": "evil"},
        headers={"host": "wwc.best"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["route"] == "structured"
