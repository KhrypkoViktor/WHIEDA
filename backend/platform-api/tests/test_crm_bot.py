"""CRM in the bot: trigger words, the cabinet button, the PRO lock text."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.crm import bot
from app.crm.rules import CrmViewer
from app.settings import get_settings
from app.telegram.update_parser import TelegramMessage
from app.tenancy import TenantContext

WITH_CRM = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                         entitlements={"structure_basic": True, "crm": True})
WITHOUT_CRM = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA",
                            entitlements={"structure_basic": True})
PAID = CrmViewer(telegram_user_id=5, is_preview_admin=False, partner_paid=True, ref_code="igor",
                 public_profile={"subdomain": "igor"})
UNPAID = CrmViewer(telegram_user_id=5, is_preview_admin=False, partner_paid=False, ref_code="igor")


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    monkeypatch.delenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", raising=False)
    monkeypatch.delenv("PLATFORM_DISABLED_FEATURES", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _msg(text: str, chat_type: str = "private") -> TelegramMessage:
    return TelegramMessage(chat_id=5, user_id=5, message_id=1, text=text, chat_type=chat_type, file_id=None, raw={})


@pytest.mark.parametrize("text", ["ежедневник", "Ежедневник", "/crm", "CRM", "мои контакты", "Мой ежедневник!", "срм"])
def test_trigger_words(text):
    assert bot.is_crm_text(text)


@pytest.mark.parametrize("text", ["контакты", "мои контакты партнёров", "академия", "ежедневник дела на завтра", ""])
def test_other_text_is_not_mine(text):
    assert not bot.is_crm_text(text)


@pytest.mark.asyncio
async def test_cabinet_button_only_for_those_who_can_open():
    login = AsyncMock(side_effect=lambda url, **_: url + "#wwc-login=t")
    with patch.object(bot, "load_viewer", AsyncMock(return_value=PAID)), patch.object(bot, "with_site_login", login):
        rows = await bot.crm_button_rows(WITH_CRM, 5)
    assert rows == [[{"text": "📒 Ежедневник", "url": "https://igor.wwc.best/crm/#wwc-login=t"}]]

    with patch.object(bot, "load_viewer", AsyncMock(return_value=UNPAID)):
        assert await bot.crm_button_rows(WITH_CRM, 5) == []
    with patch.object(bot, "load_viewer", AsyncMock(side_effect=RuntimeError("db down"))):
        assert await bot.crm_button_rows(WITH_CRM, 5) == []
    with patch.object(bot, "load_viewer", AsyncMock(return_value=PAID)) as viewer:
        assert await bot.crm_button_rows(WITHOUT_CRM, 5) == []
        assert await bot.crm_button_rows(WITH_CRM, None) == []
    viewer.assert_not_awaited()


@pytest.mark.asyncio
async def test_cabinet_button_hidden_outside_pilot(monkeypatch):
    monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", "77")
    get_settings.cache_clear()
    with patch.object(bot, "load_viewer", AsyncMock(return_value=PAID)):
        assert await bot.crm_button_rows(WITH_CRM, 5) == []


@pytest.mark.asyncio
async def test_word_opens_the_diary_with_a_login_link():
    send = AsyncMock(return_value={"ok": True})
    binding = SimpleNamespace(bot_token="token")
    with patch.object(bot, "load_viewer", AsyncMock(return_value=PAID)), patch.object(
        bot, "with_site_login", AsyncMock(return_value="https://igor.wwc.best/crm/#wwc-login=t")
    ), patch.object(bot, "current_bot_binding", return_value=binding), patch.object(bot, "send_telegram_text", send):
        result = await bot.try_handle_crm_text(WITH_CRM, _msg("ежедневник"), trace_id="t1")
    assert result["status"] == "open"
    markup = send.await_args.kwargs["reply_markup"]
    assert markup == {"inline_keyboard": [[{"text": "📒 Открыть ежедневник", "url": "https://igor.wwc.best/crm/#wwc-login=t"}]]}


@pytest.mark.asyncio
async def test_word_without_pro_offers_renewal():
    send = AsyncMock(return_value={"ok": True})
    with patch.object(bot, "load_viewer", AsyncMock(return_value=UNPAID)), patch.object(
        bot, "current_bot_binding", return_value=SimpleNamespace(bot_token="token")
    ), patch.object(bot, "send_telegram_text", send):
        result = await bot.try_handle_crm_text(WITH_CRM, _msg("crm"), trace_id="t1")
    assert result["status"] == "pro_required"
    assert "PRO" in send.await_args.kwargs["text"]
    assert send.await_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "renew:start"


@pytest.mark.asyncio
async def test_group_chat_and_disabled_feature_fall_through(monkeypatch):
    with patch.object(bot, "load_viewer", AsyncMock(return_value=PAID)) as viewer:
        assert await bot.try_handle_crm_text(WITH_CRM, _msg("ежедневник", chat_type="group"), trace_id="t") is None
        assert await bot.try_handle_crm_text(WITHOUT_CRM, _msg("ежедневник"), trace_id="t") is None
        monkeypatch.setenv("PLATFORM_DISABLED_FEATURES", "crm")
        get_settings.cache_clear()
        assert await bot.try_handle_crm_text(WITH_CRM, _msg("ежедневник"), trace_id="t") is None
    viewer.assert_not_awaited()
