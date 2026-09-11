"""Referral-link parsing and Telegram routing contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.referral_bonus.service import parse_referral_start_token, telegram_actor_id
from app.telegram.processor import process_core_telegram_update


def test_referral_start_token_accepts_only_opaque_codes():
    assert parse_referral_start_token("ref_aBcD-123_efGh") == "aBcD-123_efGh"
    assert parse_referral_start_token("opaque-site-token") is None
    assert parse_referral_start_token("ref_short") == ""
    assert parse_referral_start_token("ref_code with spaces") == ""


def test_telegram_actor_id_is_tenant_scoped_and_stable():
    assert telegram_actor_id("whieda", 123) == "telegram:whieda:123"
    assert telegram_actor_id("nsp", 123) == "telegram:nsp:123"


@pytest.mark.asyncio
async def test_referral_start_routes_before_generic_site_token(whieda_tenant, whieda_bot_binding):
    update = {
        "message": {
            "text": "/start ref_aBcD-123_efGh",
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200},
        }
    }
    with patch(
        "app.telegram.processor.handle_referral_start_token",
        AsyncMock(return_value={"ok": True, "route": "referral_start", "status": "attributed"}),
    ) as referral:
        with patch("app.telegram.processor.handle_start_token", AsyncMock()) as site_token:
            result = await process_core_telegram_update(
                whieda_tenant, update, "referral-route", binding=whieda_bot_binding
            )
    assert result["route"] == "referral_start"
    referral.assert_awaited_once()
    site_token.assert_not_called()
