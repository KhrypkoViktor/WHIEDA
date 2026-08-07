"""Lead + visitor session journey link tests."""

from __future__ import annotations

from app.leads.service import parse_lead_body


def test_parse_lead_accepts_visitor_session_id():
    lead = parse_lead_body(
        {
            "name": "Test",
            "contact": "tg@test",
            "product": "Activators",
            "idempotency_key": "k-visitor-1",
            "visitor_session_id": "00000000-0000-4000-8000-000000000099",
            "journey_type": "product",
            "route": "product",
        },
        tenant_id="whieda",
    )
    assert lead.visitor_session_id == "00000000-0000-4000-8000-000000000099"
    assert lead.metadata["journey_type"] == "product"
    assert lead.metadata["route"] == "product"
