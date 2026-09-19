"""Privacy-policy notice on /start: shown once per user, recorded, never blocking."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.consent import (
    CONSENT_POLICY_VERSION,
    consent_notice,
    first_start_consent_notice,
    is_start_command,
)
from app.telegram.processor import process_core_telegram_update


def test_is_start_command_matches_plain_and_deep_links():
    assert is_start_command("/start")
    assert is_start_command("/start abc123")
    assert is_start_command("/start@WHIEDA_Advisor_bot")
    assert not is_start_command("/started")
    assert not is_start_command("привет")
    assert not is_start_command("")


def test_notice_only_for_tenants_with_published_policy():
    assert "https://wwc.best/privacy-policy/" in (consent_notice("whieda") or "")
    assert consent_notice("nsp") is None


@pytest.mark.asyncio
async def test_notice_returned_only_on_first_record():
    with patch("app.telegram.consent.record_telegram_consent", AsyncMock(return_value=True)) as rec:
        assert await first_start_consent_notice("whieda", telegram_user_id=1, telegram_chat_id=1)
    rec.assert_awaited_once_with("whieda", telegram_user_id=1, telegram_chat_id=1)
    with patch("app.telegram.consent.record_telegram_consent", AsyncMock(return_value=False)):
        assert await first_start_consent_notice("whieda", telegram_user_id=1, telegram_chat_id=1) is None


@pytest.mark.asyncio
async def test_storage_failure_skips_notice_without_raising():
    with patch(
        "app.telegram.consent.record_telegram_consent",
        AsyncMock(side_effect=RuntimeError("db down")),
    ):
        assert await first_start_consent_notice("whieda", telegram_user_id=1, telegram_chat_id=1) is None


def _start_update(text: str = "/start") -> dict:
    return {
        "message": {
            "text": text,
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200, "username": "someone"},
        }
    }


@pytest.mark.asyncio
async def test_start_sends_notice_before_panel(whieda_tenant, whieda_bot_binding):
    sent: list[str] = []

    async def fake_deliver(chat_id, text):
        sent.append(text)

    with patch("app.telegram.processor.link_lead_actor_by_username", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.fill_lead_actor_user_id", AsyncMock(return_value=None)
    ), patch("app.telegram.processor.merge_anonymous_actor_into_partner", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.first_start_consent_notice", AsyncMock(return_value=consent_notice("whieda"))
    ) as notice, patch("app.telegram.processor.deliver_text", fake_deliver), patch(
        "app.telegram.processor.handle_newcomer_panel",
        AsyncMock(return_value={"ok": True, "route": "newcomer_panel"}),
    ):
        result = await process_core_telegram_update(
            whieda_tenant, _start_update(), "t-consent", binding=whieda_bot_binding
        )
    assert result["route"] == "newcomer_panel"
    notice.assert_awaited_once_with("whieda", telegram_user_id=200, telegram_chat_id=100, trace_id="t-consent")
    assert sent == [consent_notice("whieda")]


@pytest.mark.asyncio
async def test_non_start_message_never_touches_consent(whieda_tenant, whieda_bot_binding):
    with patch("app.telegram.processor.link_lead_actor_by_username", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.fill_lead_actor_user_id", AsyncMock(return_value=None)
    ), patch("app.telegram.processor.merge_anonymous_actor_into_partner", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.first_start_consent_notice", AsyncMock(return_value=None)
    ) as notice, patch("app.telegram.processor.handle_onboarding", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.handle_navigation_text", AsyncMock(return_value=None)
    ), patch("app.telegram.processor.handle_advisor_query", AsyncMock(return_value={"ok": True, "route": "advisor"})), patch(
        "app.telegram.support.get_open_ticket_for_user", AsyncMock(return_value=None)
    ):
        await process_core_telegram_update(
            whieda_tenant, _start_update("какая цена у кордицепса"), "t-no", binding=whieda_bot_binding
        )
    notice.assert_not_awaited()


def test_policy_version_is_a_date():
    assert len(CONSENT_POLICY_VERSION) == 10
