"""Tenant wording profiles (app/tenants): NSP has its own voice, unknown tenants stay neutral."""

from __future__ import annotations

import pytest

from app.tenants import get_tenant_profile, registered_tenant_ids
from app.tenants.nsp import NSP_PROFILE

FORBIDDEN = ("whieda", "wwc.best", "лечит", "излечива", "калькулятор", "dify", "traceback")
GAP_KINDS = (
    "unknown_product",
    "unrouted_message",
    "unknown_followup",
    "unsupported_topic",
    "medical_or_safety_boundary",
    "missing_resource",
    "ambiguous_product",
)


def test_nsp_profile_is_registered():
    assert "nsp-maxim" in registered_tenant_ids()
    assert get_tenant_profile("nsp-maxim") is NSP_PROFILE
    assert get_tenant_profile(" nsp-maxim ") is NSP_PROFILE


def test_unknown_and_home_tenants_have_no_profile():
    assert get_tenant_profile("whieda") is None
    assert get_tenant_profile("nikita-demo") is None
    assert get_tenant_profile(None) is None
    assert get_tenant_profile("") is None


@pytest.mark.parametrize("intent", ["greeting", "capabilities", "help"])
def test_nsp_service_texts_are_clean(intent: str):
    text = NSP_PROFILE.service_text(intent)
    assert text
    lowered = text.lower()
    for fragment in FORBIDDEN:
        assert fragment not in lowered, (intent, fragment)
    assert "NSP" in text


@pytest.mark.parametrize("kind", GAP_KINDS)
def test_nsp_gap_texts_are_clean(kind: str):
    text = NSP_PROFILE.gap_text(kind)
    assert text
    lowered = text.lower()
    for fragment in FORBIDDEN:
        assert fragment not in lowered, (kind, fragment)


def test_nsp_fallback_points_to_support_not_to_a_person():
    for kind in ("unknown_product", "unrouted_message", "unknown_followup", "unsupported_topic"):
        text = NSP_PROFILE.gap_text(kind)
        assert "/support" in text
        assert "@" not in text
        assert "📦 Товары" in text
    assert NSP_PROFILE.contact_handle is None


def test_nsp_medical_boundary_says_not_a_medicine():
    text = NSP_PROFILE.gap_text("medical_or_safety_boundary")
    assert "не лекарства" in text
    assert "врач" in text
    assert "не заменяют лечение" in text


def test_nsp_feature_flags():
    assert NSP_PROFILE.calculator is False
    assert NSP_PROFILE.partner_prices is False
    assert NSP_PROFILE.service_text("smalltalk_status") is None
    assert NSP_PROFILE.gap_text("something_else") is None
