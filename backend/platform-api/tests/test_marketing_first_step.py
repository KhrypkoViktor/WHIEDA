"""Вопрос о рассылке после первого полезного шага (11.11.2026): вход на сайт через бота, оформленная заявка «Заказать сайт»,
запись на встречу. На первом /start его нет (см. test_telegram_consent.py)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.funnel import guest_bot, registration
from app.funnel.guest import Reply
from app.telegram import site_requests
from app.telegram.bindings import binding_context_scope
from app.telegram.consent import (
    TRIGGER_MEETING_REGISTRATION,
    TRIGGER_SITE_LOGIN,
    TRIGGER_SITE_REQUEST,
)
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

TENANT = TenantContext(tenant_id="whieda", status="active", display_name="WHIEDA", entitlements={})


# ---- вход на сайт через бота -----------------------------------------------------------------------


def _login_update(user_id: int = 210, chat_id: int = 110) -> dict:
    return {
        "update_id": 700001,
        "message": {"text": "/start content_access_tokXYZ", "chat": {"id": chat_id}, "from": {"id": user_id}},
    }


@pytest.mark.asyncio
async def test_a_confirmed_site_login_asks_about_the_newsletter_once_after_the_confirmation(whieda_tenant, whieda_bot_binding):
    from app.telegram.processor import process_core_telegram_update

    order: list[str] = []

    async def deliver(*args, **kwargs):
        order.append("login_message")

    async def offer(*args, **kwargs):
        order.append("offer")
        return True

    confirm = AsyncMock(return_value={"ok": True, "status": "approved", "challenge_id": "c-1", "return_to": "/academy/"})
    with patch("app.telegram.content_access.confirm_content_from_telegram", confirm), patch(
        "app.telegram.content_access.record_click_consent", AsyncMock(return_value=True)
    ), patch("app.telegram.content_access.deliver_text", deliver), patch(
        "app.telegram.content_access.offer_marketing_consent", side_effect=offer
    ) as offered:
        result = await process_core_telegram_update(whieda_tenant, _login_update(), "t-login", binding=whieda_bot_binding)
    assert result["route"] == "content_access" and result["ok"] is True
    offered.assert_called_once_with(
        "whieda", telegram_user_id=210, telegram_chat_id=110, trace_id="t-login", trigger=TRIGGER_SITE_LOGIN
    )
    assert order == ["login_message", "offer"]


@pytest.mark.asyncio
async def test_a_declined_site_login_does_not_ask(whieda_tenant, whieda_bot_binding):
    from app.telegram.processor import process_core_telegram_update

    confirm = AsyncMock(side_effect=HTTPException(status_code=410, detail={"error": "challenge_expired"}))
    with patch("app.telegram.content_access.confirm_content_from_telegram", confirm), patch(
        "app.telegram.content_access.deliver_text", AsyncMock()
    ), patch("app.telegram.content_access.offer_marketing_consent", AsyncMock()) as offered:
        await process_core_telegram_update(whieda_tenant, _login_update(), "t-expired", binding=whieda_bot_binding)
    offered.assert_not_awaited()


# ---- заявка «Заказать сайт» ------------------------------------------------------------------------


def _msg(*, text: str | None = None, file_id: str | None = None) -> TelegramMessage:
    return TelegramMessage(
        chat_id=100, user_id=200, message_id=7, text=text, chat_type="private", file_id=file_id, raw={}
    )


@pytest.fixture
def site_request_world(monkeypatch):
    state = SimpleNamespace(request={"request_id": "r-1", "status": "awaiting_payment"})
    monkeypatch.setattr(site_requests, "_actor", AsyncMock(return_value="actor-1"))
    monkeypatch.setattr(site_requests, "get_open_site_request", AsyncMock(side_effect=lambda *a, **k: state.request))
    monkeypatch.setattr(site_requests, "request_is_stale", lambda request: False)
    monkeypatch.setattr(site_requests, "_prompt_for_request", AsyncMock())
    monkeypatch.setattr(site_requests, "_notify_owner_step", AsyncMock())
    monkeypatch.setattr(site_requests, "_copy_to_owner", AsyncMock())
    monkeypatch.setattr(site_requests, "_send_to_owner", AsyncMock())
    state.offer = AsyncMock(return_value=True)
    monkeypatch.setattr(site_requests, "offer_marketing_consent", state.offer)
    return state


@pytest.mark.asyncio
async def test_a_finished_site_request_asks_about_the_newsletter(site_request_world, monkeypatch):
    submitted = {
        "request_id": "r-1", "status": "payment_submitted", "partner_name": "Анна", "requested_subdomain": "anna",
        "total_amount_minor": 1100000, "currency": "RUB", "plan_code": "site",
    }
    monkeypatch.setattr(site_requests, "submit_site_payment_proof", AsyncMock(return_value=submitted))
    result = await site_requests.try_handle_site_request_message(TENANT, _msg(file_id="receipt"), trace_id="t-1")
    assert result["route"] == "site_request" and result["status"] == "payment_submitted"
    site_request_world.offer.assert_awaited_once_with(
        "whieda", telegram_user_id=200, telegram_chat_id=100, trace_id="t-1", trigger=TRIGGER_SITE_REQUEST
    )


@pytest.mark.asyncio
async def test_an_unfinished_site_request_step_does_not_ask(site_request_world, monkeypatch):
    site_request_world.request = {"request_id": "r-1", "status": "awaiting_name"}
    monkeypatch.setattr(
        site_requests, "set_site_request_name",
        AsyncMock(return_value={"request_id": "r-1", "status": "awaiting_subdomain"}),
    )
    result = await site_requests.try_handle_site_request_message(TENANT, _msg(text="Анна Петрова"), trace_id="t-1")
    assert result["status"] == "awaiting_subdomain"
    site_request_world.offer.assert_not_awaited()


# ---- запись на встречу -----------------------------------------------------------------------------


USER = 4242_0007


def _cb(data: str) -> TelegramCallbackQuery:
    return TelegramCallbackQuery(
        chat_id=USER, user_id=USER, callback_query_id="cq-1", data=data, chat_type="private", raw={}
    )


@pytest.fixture
def guest_world(monkeypatch, whieda_bot_binding):
    monkeypatch.setattr(guest_bot, "send_telegram_text", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(guest_bot, "answer_callback_query", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(guest_bot, "guest_offers_blocked", AsyncMock(return_value=False))
    offer = AsyncMock(return_value=True)
    monkeypatch.setattr(guest_bot, "offer_marketing_consent", offer)
    with binding_context_scope(whieda_bot_binding):
        yield offer


@pytest.mark.asyncio
async def test_a_meeting_registration_asks_about_the_newsletter(guest_world, monkeypatch):
    monkeypatch.setattr(registration, "register", AsyncMock(return_value=registration.ActionResult(Reply("Вы записаны."), "registered")))
    result = await guest_bot.try_handle_guest_callback(TENANT, _cb("mreg:12"), trace_id="t-1")
    assert result["status"] == "registered"
    guest_world.assert_awaited_once_with(
        "whieda", telegram_user_id=USER, telegram_chat_id=USER, trace_id="t-1", trigger=TRIGGER_MEETING_REGISTRATION
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["already", "closed", "not_found"])
async def test_a_registration_that_did_not_happen_does_not_ask(guest_world, monkeypatch, status):
    monkeypatch.setattr(registration, "register", AsyncMock(return_value=registration.ActionResult(Reply("..."), status)))
    await guest_bot.try_handle_guest_callback(TENANT, _cb("mreg:12"), trace_id="t-1")
    guest_world.assert_not_awaited()
