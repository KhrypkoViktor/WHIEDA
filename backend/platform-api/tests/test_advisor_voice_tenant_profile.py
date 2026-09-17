"""voice.py hands non-home tenants their own registered wording (app.tenants)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.advisor import voice
from app.tenancy import TenantContext


def _tenant(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id=tenant_id, status="active", display_name="X", entitlements={})


class _Profile:
    display_name = "NSP"
    medical_boundary = "Не лекарства — к врачу."
    help_text = "Помощь NSP"

    def service_text(self, intent_id: str):
        return {"greeting": "Здравствуйте, NSP!"}.get(intent_id)

    def gap_text(self, kind: str):
        return "Меню NSP" if kind == "unrouted_message" else None


def test_registered_tenant_profile_wins_for_greeting_boundary_and_gaps():
    with patch("app.advisor.voice._tenant_profile", lambda tid: _Profile() if tid == "nsp-maxim" else None):
        t = _tenant("nsp-maxim")
        assert voice.service_fallback(t, "greeting") == "Здравствуйте, NSP!"
        assert voice.service_fallback(t, "help") == "Помощь NSP"
        assert voice.discomfort_boundary_text(t) == "Не лекарства — к врачу."
        assert voice.gap_text_for("nsp-maxim", "unrouted_message") == "Меню NSP"
        # kinds the profile does not cover fall back to the neutral text, never to WHIEDA's
        assert "WHIEDA" not in voice.gap_text_for("nsp-maxim", "ambiguous_product")


def test_home_tenant_and_unknown_tenants_are_untouched():
    with patch("app.advisor.voice._tenant_profile", lambda tid: None):
        assert "WHIEDA" in voice.service_fallback(_tenant("whieda"), "greeting")
        assert voice.gap_text_for("other", "unrouted_message") == voice._NEUTRAL_MENU


def test_missing_tenants_package_is_not_an_error():
    with patch.dict("sys.modules", {"app.tenants": None}):
        assert voice._tenant_profile("nsp-maxim") is None
