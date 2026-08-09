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


def _enable_core_route(monkeypatch):
    from app import settings as settings_module

    settings_module.get_settings.cache_clear()
    monkeypatch.setenv("CORE_ROUTE_ADVISOR", "core")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"session": "contract-missing-question"},
        {"question": "", "session": "contract-empty-question"},
        {"question": "   ", "session": "contract-blank-question"},
    ],
)
async def test_advisor_missing_question_returns_4xx(client, monkeypatch, payload):
    _enable_core_route(monkeypatch)
    response = await client.post(
        "/v1/advisor/query",
        json=payload,
        headers={"host": "wwc.best"},
    )
    assert 400 <= response.status_code < 500
    assert response.status_code != 500


@pytest.mark.asyncio
async def test_advisor_malformed_json_returns_422(client, monkeypatch):
    _enable_core_route(monkeypatch)
    response = await client.post(
        "/v1/advisor/query",
        content=b"{not-json",
        headers={"host": "wwc.best", "content-type": "application/json"},
    )
    assert response.status_code == 422
    assert response.status_code != 500
