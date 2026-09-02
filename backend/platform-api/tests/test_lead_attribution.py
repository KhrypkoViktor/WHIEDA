from __future__ import annotations

import pytest

from app.leads.service import parse_lead_body


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
