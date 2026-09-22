"""Referral-link parsing and Telegram routing contracts."""

from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.referral_bonus.service import (
    _auto_redeem_platform_points,
    accept_referral_start,
    ensure_telegram_actor,
    parse_referral_start_token,
    telegram_actor_id,
)
from app.telegram.processor import process_core_telegram_update
from app.telegram.referral_bonus import (
    SUPPORT_URL,
    _dashboard_keyboard,
    invitation_text,
    is_referral_command,
)


def test_referral_start_token_accepts_only_opaque_codes():
    assert parse_referral_start_token("ref_aBcD-123_efGh") == "aBcD-123_efGh"
    assert parse_referral_start_token("opaque-site-token") is None
    assert parse_referral_start_token("ref_short") == ""
    assert parse_referral_start_token("ref_code with spaces") == ""


def test_new_telegram_actor_inserts_match_partial_unique_index():
    """The live unique index excludes NULL Telegram IDs, so conflict needs its predicate."""
    expected = "on conflict (tenant_id, telegram_user_id)\n              where telegram_user_id is not null"
    assert expected in inspect.getsource(ensure_telegram_actor).lower()
    assert expected in inspect.getsource(accept_referral_start).lower()


def test_telegram_actor_id_is_tenant_scoped_and_stable():
    assert telegram_actor_id("whieda", 123) == "telegram:whieda:123"
    assert telegram_actor_id("nsp", 123) == "telegram:nsp:123"


def test_referral_command_is_exact_and_does_not_capture_normal_text():
    assert is_referral_command("/referral")
    assert is_referral_command("/cabinet")
    assert is_referral_command("/invite")
    assert is_referral_command("/support")
    assert is_referral_command("/referral@WHIEDA_bot")
    assert not is_referral_command("referral")
    assert not is_referral_command("/referral now")


def test_cabinet_keyboard_opens_site_and_copies_full_invitation():
    markup = _dashboard_keyboard(
        bot_username="WHIEDA_bot",
        invite_code="invite-code-123",
        site_url="https://dev.wwc.best/",
        has_site=True,
        minimal=False,
    )
    rows = markup["inline_keyboard"]
    assert rows[0][0] == {"text": "Мой сайт", "url": "https://dev.wwc.best/"}
    assert rows[1][0] == {"text": "Продлить платформу", "callback_data": "renew:start"}
    link = "https://t.me/WHIEDA_bot?start=ref_invite-code-123"
    assert rows[2][0]["copy_text"]["text"] == invitation_text(link)
    assert rows[3][0]["text"] == "Отправить приглашение"
    assert rows[4][0]["callback_data"] == "referral:list"
    assert rows[6][0] == {"text": "Подключить Gemini Pro", "callback_data": "svc:card:gemini"}
    assert rows[7][0] == {"text": "🤖 WWC Bot", "callback_data": "nav:wwcbot"}
    assert rows[8][0] == {"text": "Поддержка", "url": SUPPORT_URL}
    assert len(invitation_text(link)) <= 256


def test_cabinet_without_site_offers_the_bot_dialog_not_the_google_form():
    """Owner, 18–19.09.2026: the Google form opened inside Telegram's browser and
    people lost the order; the bot's own dialog (country → subdomain → photo →
    text → receipt) is the way. Share text must not carry '+' for spaces."""
    markup = _dashboard_keyboard(
        bot_username="WHIEDA_bot", invite_code="c", site_url="https://wwc.best/", has_site=False, minimal=False,
        telegram_user_id=525317405,
    )
    row = markup["inline_keyboard"][1][0]
    assert row == {"text": "Заказать сайт WWC", "callback_data": "site:create"}
    assert not any("docs.google.com" in str(b.get("url", "")) for r in markup["inline_keyboard"] for b in r)
    share = markup["inline_keyboard"][3][0]["url"]
    assert share.startswith("https://t.me/share/url?url=https%3A%2F%2Ft.me%2FWHIEDA_bot%3Fstart%3Dref_c&text=")
    assert "+" not in share and "%20" in share


def test_minimal_cabinet_keyboard_contains_only_ready_partner_actions():
    markup = _dashboard_keyboard(
        bot_username="WHIEDA_bot",
        invite_code="invite-code-123",
        site_url="https://dev.wwc.best/",
        has_site=True,
        minimal=True,
    )
    rows = markup["inline_keyboard"]
    assert [row[0]["text"] for row in rows] == [
        "Мой сайт",
        "Скопировать реферальную ссылку",
        "Отправить приглашение",
        "Калькулятор",
        "Подключить Gemini Pro",
        "🤖 WWC Bot",
        "Поддержка",
    ]
    assert rows[1][0]["copy_text"]["text"] == (
        "https://t.me/WHIEDA_bot?start=ref_invite-code-123"
    )
    # The only callback on production is the services card (owner signed it off 15.09.2026).
    assert [row[0]["callback_data"] for row in rows if "callback_data" in row[0]] == ["svc:card:gemini", "nav:wwcbot"]


@pytest.mark.asyncio
async def test_three_thousand_points_are_spent_and_extend_site_by_three_months():
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    source_payment_id = str(uuid.uuid4())
    responses = [
        {"locked": None},
        {"plan_code": "platform_3m", "access_months": 3, "price_wusd_minor": 3000},
        {"ref_code": "partner"},
        {"amount_minor": 3000},
        {"paid_until": now},
        {"entry_id": uuid.uuid4()},
        {"paid_until": datetime(2026, 12, 11, tzinfo=timezone.utc)},
    ]
    with patch("app.referral_bonus.service._utc_now", return_value=now):
        with patch("app.referral_bonus.service.fetch_one", AsyncMock(side_effect=responses)) as fetch:
            result = await _auto_redeem_platform_points(
                object(),
                tenant_id="whieda",
                actor_id="actor-1",
                source_payment_id=source_payment_id,
            )
    assert result == {
        "redeemed_blocks": 1,
        "access_months": 3,
        "spent_points": 3000,
        "balance_points": 0,
        "paid_until": datetime(2026, 12, 11, tzinfo=timezone.utc),
        "days_remaining": 91,
    }
    debit_call = fetch.await_args_list[5]
    assert debit_call.args[2][2] == -3000


@pytest.mark.asyncio
async def test_partial_points_balance_is_kept_without_extending_site():
    responses = [
        {"locked": None},
        {"plan_code": "platform_3m", "access_months": 3, "price_wusd_minor": 3000},
        {"ref_code": "partner"},
        {"amount_minor": 600},
    ]
    with patch("app.referral_bonus.service.fetch_one", AsyncMock(side_effect=responses)) as fetch:
        result = await _auto_redeem_platform_points(
            object(),
            tenant_id="whieda",
            actor_id="actor-1",
            source_payment_id=str(uuid.uuid4()),
        )
    assert result == {"redeemed_blocks": 0, "balance_points": 600}
    assert fetch.await_count == 4


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


@pytest.mark.asyncio
async def test_referral_command_routes_before_onboarding_and_advisor(whieda_tenant, whieda_bot_binding):
    update = {
        "message": {
            "text": "/referral",
            "chat": {"id": 101, "type": "private"},
            "from": {"id": 201},
        }
    }
    with patch(
        "app.telegram.processor.try_handle_referral_message",
        AsyncMock(return_value={"ok": True, "route": "referral"}),
    ) as referral:
        with patch("app.telegram.processor.handle_onboarding", AsyncMock()) as onboarding:
            with patch("app.telegram.processor.handle_advisor_query", AsyncMock()) as advisor:
                result = await process_core_telegram_update(
                    whieda_tenant, update, "referral-command", binding=whieda_bot_binding
                )
    assert result["route"] == "referral"
    referral.assert_awaited_once()
    onboarding.assert_not_called()
    advisor.assert_not_called()


@pytest.mark.asyncio
async def test_platform_command_routes_before_open_site_request(whieda_tenant, whieda_bot_binding):
    update = {
        "message": {
            "text": "/support",
            "chat": {"id": 101, "type": "private"},
            "from": {"id": 201},
        }
    }
    referral = AsyncMock(return_value={"ok": True, "route": "support"})
    with patch("app.telegram.processor.try_handle_referral_message", referral):
        with patch("app.telegram.processor.try_handle_site_request_message", AsyncMock()) as request:
            result = await process_core_telegram_update(
                whieda_tenant, update, "support-route", binding=whieda_bot_binding
            )
    assert result["route"] == "support"
    referral.assert_awaited_once()
    request.assert_not_called()
