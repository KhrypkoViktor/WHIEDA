"""Failure-mode checks (mocked; no live infra required)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
async def test_advisor_core_returns_fallback_without_legacy(client, monkeypatch):
    from app import settings as settings_module

    settings_module.get_settings.cache_clear()
    monkeypatch.setenv("CORE_ROUTE_ADVISOR", "core")

    async def core_fallback(*_args, **_kwargs):
        return {
            "ok": True,
            "answer_text": "fallback from core",
            "answer_mode": "fallback",
            "route": "structured",
            "product": None,
            "media": {"photo_url": None, "videos": [], "documents": []},
            "clarifications": [],
            "sources": [],
            "context": {},
            "error_id": None,
            "trace_id": "t1",
        }

    monkeypatch.setattr("app.advisor.routes.handle_structured_query", core_fallback)

    legacy = AsyncMock(side_effect=AssertionError("legacy must not be called in core mode"))
    monkeypatch.setattr("app.advisor.routes.post_legacy_json", legacy)

    response = await client.post(
        "/v1/advisor/query",
        json={"session": "s1", "question": "неизвестный запрос xyz"},
        headers={"host": "wwc.best"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer_mode"] == "fallback"
    legacy.assert_not_called()
