"""Leader digest report tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.reports.service import build_leader_digest, render_digest_csv


@pytest.mark.asyncio
async def test_build_leader_digest_shape():
    from contextlib import asynccontextmanager

    sessions = {"total": 5}
    leads = {"total": 2}
    onboarding = {"active": 1, "completed": 0, "paused": 0}
    escalations = {"open_count": 1}
    links = {"created": 3, "opened": 2}

    async def fake_fetch_one(conn, sql, params):
        if "visitor_sessions" in sql:
            return sessions
        if "website_leads" in sql:
            return leads
        if "onboarding_enrollments" in sql and "filter" in sql:
            return onboarding
        if "mentor_escalations" in sql:
            return escalations
        if "telegram_link_created" in sql:
            return links
        return {}

    @asynccontextmanager
    async def fake_conn(_tenant_id: str):
        yield object()

    with patch("app.reports.service.tenant_connection", fake_conn):
        with patch("app.reports.service.fetch_one", fake_fetch_one):
            with patch(
                "app.reports.service.fetch_all",
                AsyncMock(return_value=[{"event_type": "route_opened", "cnt": 4}]),
            ):
                digest = await build_leader_digest("whieda", days=7)

    assert digest["ok"] is True
    assert digest["metrics"]["route_visitors"] == 5
    assert digest["metrics"]["new_leads"] == 2
    assert len(digest["attention_actions"]) == 3


def test_render_digest_csv_utf8_headers():
    digest = {
        "period_start": "2026-08-01",
        "period_end": "2026-08-07",
        "metrics": {"route_visitors": 1, "telegram_links_created": 0, "telegram_opened": 0, "new_leads": 0,
                    "onboarding_active": 0, "onboarding_completed": 0, "onboarding_paused": 0, "open_escalations": 0},
        "attention_actions": ["—", "—", "—"],
    }
    csv_text = render_digest_csv(digest)
    assert "раздел" in csv_text
    assert "Посетители" in csv_text


@pytest.mark.asyncio
async def test_leader_digest_http(client, monkeypatch):
    monkeypatch.setattr(
        "app.reports.routes.build_leader_digest",
        AsyncMock(
            return_value={
                "ok": True,
                "period_days": 7,
                "metrics": {"route_visitors": 0, "new_leads": 0},
                "attention_actions": ["—", "—", "—"],
            }
        ),
    )
    resp = await client.get("/v1/reports/leader-digest", headers={"host": "wwc.best"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
