from __future__ import annotations

import pytest

from app.leads.service import parse_lead_body


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
