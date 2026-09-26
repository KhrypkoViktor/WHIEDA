"""Marketing opt-in on website leads (38-ФЗ ст. 18, 26.09.2026).

The checkbox is optional: only an explicit «yes» becomes ``marketing_consent = true``;
the lead is accepted either way. The partner's cabinet shows both consents.
"""

from __future__ import annotations

import pytest

from app.admin.services.leads import _lead_detail_item, _lead_list_item
from app.leads.service import parse_lead_body, parse_marketing_consent


def _body(**extra) -> dict:
    return {
        "name": "Test",
        "contact": "+79990000000",
        "product": "Активатор",
        "idempotency_key": "k-marketing",
        **extra,
    }


@pytest.mark.parametrize("value", [True, "true", "TRUE", "1", 1, "on", "yes", "да"])
def test_explicit_yes_counts(value):
    assert parse_marketing_consent(value) is True
    assert parse_lead_body(_body(marketing_consent=value), "whieda").marketing_consent is True


@pytest.mark.parametrize("value", [None, False, "", "false", "0", 0, "off", "no", "нет", "maybe", 2])
def test_anything_else_is_no(value):
    assert parse_marketing_consent(value) is False
    assert parse_lead_body(_body(marketing_consent=value), "whieda").marketing_consent is False


def test_missing_checkbox_is_no_and_lead_still_accepted():
    lead = parse_lead_body(_body(), "whieda")
    assert lead.marketing_consent is False
    assert lead.name == "Test"


def test_cabinet_detail_exposes_both_consents():
    item = _lead_detail_item(
        {
            "lead_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "contact": "+79991234567",
            "status": "new",
            "consent_version": "wwc-personal-data-consent-2026-09-08",
            "consent_at": "2026-09-26T10:00:00+00:00",
            "marketing_consent": True,
            "marketing_consent_at": "2026-09-26T10:00:00+00:00",
        },
    )
    assert item["consent_version"] == "wwc-personal-data-consent-2026-09-08"
    assert item["consent_at"] == "2026-09-26T10:00:00+00:00"
    assert item["marketing_consent"] is True
    assert item["marketing_consent_at"] == "2026-09-26T10:00:00+00:00"
    assert "contact" not in item


def test_cabinet_detail_defaults_when_columns_are_absent():
    item = _lead_detail_item({"lead_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "contact": "+7", "status": "new"})
    assert item["marketing_consent"] is False
    assert item["marketing_consent_at"] is None
    assert item["consent_at"] is None


def test_cabinet_list_exposes_marketing_flag_only():
    item = _lead_list_item({"lead_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "contact": "+7", "marketing_consent": True})
    assert item["marketing_consent"] is True
    assert "marketing_consent_at" not in item
    assert _lead_list_item({"lead_id": "x", "contact": "+7"})["marketing_consent"] is False
