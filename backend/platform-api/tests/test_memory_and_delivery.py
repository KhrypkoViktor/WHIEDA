"""Memory facts and report delivery tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.reports.delivery import format_leader_digest_message


def test_format_leader_digest_message():
    digest = {
        "period_start": "2026-08-01T00:00:00+00:00",
        "period_end": "2026-08-07T00:00:00+00:00",
        "metrics": {
            "route_visitors": 10,
            "telegram_links_created": 3,
            "telegram_opened": 2,
            "new_leads": 1,
            "onboarding_active": 2,
            "onboarding_completed": 0,
            "open_escalations": 1,
        },
        "attention_actions": ["Проверить эскалации", "—", "—"],
    }
    text = format_leader_digest_message(digest)
    assert "Недельный отчёт" in text
    assert "10" in text
    assert "эскалации" in text.lower() or "Эскалации" in text


@pytest.mark.asyncio
async def test_memory_fact_http(client, monkeypatch):
    monkeypatch.setattr(
        "app.memory.routes.upsert_memory_fact",
        AsyncMock(return_value={"ok": True, "fact_id": "f1", "fact_key": "preferred_product"}),
    )
    resp = await client.put(
        "/v1/memory-facts",
        json={
            "subject_type": "telegram_user",
            "subject_id": "12345",
            "fact_key": "preferred_product",
            "fact_value": {"sku": "SKU-1"},
        },
        headers={"host": "wwc.best"},
    )
    assert resp.status_code == 200
    assert resp.json()["fact_key"] == "preferred_product"
