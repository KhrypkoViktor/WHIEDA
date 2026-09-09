from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.leads.service import parse_lead_body, save_lead


def test_parse_lead_fills_ref_from_catalog_page_url():
    lead = parse_lead_body(
        {
            "name": "A",
            "contact": "b",
            "product": "c",
            "idempotency_key": "k-profit-url",
            "page_url": "https://wwc.best/catalog/wentong?ref=profit",
        },
        tenant_id="whieda",
    )
    assert lead.active_ref_code == "profit"
    assert lead.first_ref_code == "profit"


def test_parse_lead_fills_ref_from_partner_subdomain():
    lead = parse_lead_body(
        {
            "name": "A",
            "contact": "b",
            "product": "c",
            "idempotency_key": "k-elena-host",
            "page_url": "https://elena.wwc.best/partner/",
        },
        tenant_id="whieda",
    )
    assert lead.active_ref_code == "onlineelena"


def test_parse_lead_keeps_explicit_active_ref():
    lead = parse_lead_body(
        {
            "name": "A",
            "contact": "b",
            "product": "c",
            "idempotency_key": "k-keep-active",
            "active_ref": "igoref",
            "initial_ref": "onlineelena",
            "page_url": "https://wwc.best/catalog/?ref=profit",
        },
        tenant_id="whieda",
    )
    assert lead.active_ref_code == "igoref"
    assert lead.first_ref_code == "onlineelena"


def test_parse_lead_ignores_tenant_in_body():
    lead = parse_lead_body(
        {
            "name": "A",
            "contact": "b",
            "product": "c",
            "idempotency_key": "k",
            "tenant": "evil",
        },
        tenant_id="whieda",
    )
    assert lead.tenant_id == "whieda"


@pytest.mark.asyncio
async def test_save_lead_keeps_first_touch_but_gates_ownership_by_subscription(monkeypatch):
    lead = parse_lead_body(
        {
            "name": "A",
            "contact": "b",
            "product": "c",
            "idempotency_key": "k-routing-contract",
            "initial_ref": "expired-partner",
            "active_ref": "expired-partner",
        },
        tenant_id="whieda",
    )
    captured = {}

    @asynccontextmanager
    async def fake_connection(tenant_id: str):
        captured["tenant_id"] = tenant_id
        yield object()

    async def fake_fetch_one(conn, query, params=None):
        captured["query"] = query
        captured["params"] = params
        return {
            "lead_id": "00000000-0000-0000-0000-000000000001",
            "public_id": "lead-1",
            "created": False,
            "attributed_owner_id": "organic-owner",
            "assigned_owner_id": "organic-owner",
        }

    monkeypatch.setattr("app.leads.service.tenant_connection", fake_connection)
    monkeypatch.setattr("app.leads.service.fetch_one", fake_fetch_one)
    monkeypatch.setattr(
        "app.leads.service.get_settings",
        lambda: SimpleNamespace(
            lead_consent_version="test-consent",
            platform_organic_owner_id="organic-owner",
        ),
    )

    row = await save_lead(lead)

    assert row["assigned_owner_id"] == "organic-owner"
    assert captured["tenant_id"] == "whieda"
    assert captured["params"]["organic_owner_id"] == "organic-owner"
    assert captured["params"]["first_ref"] == "expired-partner"
    assert captured["query"].count("partner_subscription_state") == 2
    assert "%(first_ref)s" in captured["query"]
    assert "'viktor'" not in captured["query"]
