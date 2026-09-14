"""Telegram renewal flow contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.renewal_requests import (
    _payment_text,
    try_handle_renewal_callback,
    try_handle_renewal_message,
)
from app.telegram.bindings import binding_context_scope
from app.telegram.update_parser import parse_telegram_callback, parse_telegram_message


def _callback(data: str, *, user_id: int = 7001):
    parsed = parse_telegram_callback(
        {
            "callback_query": {
                "id": "renew-callback",
                "data": data,
                "from": {"id": user_id},
                "message": {"chat": {"id": user_id, "type": "private"}},
            }
        }
    )
    assert parsed is not None
    return parsed


def test_payment_text_is_country_specific():
    by = _payment_text(
        {"amount_minor": 5400, "currency": "WUSD", "country_code": "BY", "access_months": 6}
    )
    ru = _payment_text(
        {"amount_minor": 540000, "currency": "RUB", "country_code": "RU", "access_months": 6}
    )
    assert "54 WWC$" in by and "SUNRAYSWORD" in by and "Т-Банк" not in by
    assert "5 400 ₽" in ru and "Т-Банк" in ru and "SUNRAYSWORD" not in ru


@pytest.mark.asyncio
async def test_start_callback_begins_renewal(whieda_tenant, whieda_bot_binding):
    request = {"status": "awaiting_period"}
    with patch("app.telegram.renewal_requests.answer_callback_query", AsyncMock()):
        with patch("app.telegram.renewal_requests._actor", AsyncMock(return_value="actor-1")):
            with patch(
                "app.telegram.renewal_requests.begin_renewal_request",
                AsyncMock(return_value=request),
            ):
                with patch("app.telegram.renewal_requests._prompt", AsyncMock()) as prompt:
                    with binding_context_scope(whieda_bot_binding):
                        result = await try_handle_renewal_callback(
                            whieda_tenant, _callback("renew:start"), trace_id="renew-start"
                        )
    assert result and result["status"] == "awaiting_period"
    prompt.assert_awaited_once_with(7001, request)


@pytest.mark.asyncio
async def test_cancel_callback_closes_open_renewal(whieda_tenant, whieda_bot_binding):
    with patch("app.telegram.renewal_requests.answer_callback_query", AsyncMock()):
        with patch("app.telegram.renewal_requests._actor", AsyncMock(return_value="actor-1")):
            with patch(
                "app.telegram.renewal_requests.cancel_renewal_request",
                AsyncMock(return_value={"status": "cancelled"}),
            ) as cancelled:
                with patch("app.telegram.renewal_requests._deliver", AsyncMock()) as deliver:
                    with binding_context_scope(whieda_bot_binding):
                        result = await try_handle_renewal_callback(
                            whieda_tenant, _callback("renew:cancel"), trace_id="renew-cancel"
                        )
    assert result and result["status"] == "cancelled"
    cancelled.assert_awaited_once_with(whieda_tenant.tenant_id, "actor-1")
    assert "Продление отменено" in deliver.await_args.args[1]


@pytest.mark.asyncio
async def test_slash_command_is_not_captured_by_open_renewal(whieda_tenant):
    message = parse_telegram_message(
        {
            "message": {
                "message_id": 52,
                "text": "/products",
                "chat": {"id": 8001, "type": "private"},
                "from": {"id": 8001},
            }
        }
    )
    assert message is not None
    with patch("app.telegram.renewal_requests._actor", AsyncMock()) as actor:
        result = await try_handle_renewal_message(
            whieda_tenant, message, trace_id="renew-command-bypass"
        )
    assert result is None
    actor.assert_not_awaited()


@pytest.mark.asyncio
async def test_payment_proof_is_forwarded_for_owner_confirmation(
    whieda_tenant, whieda_bot_binding
):
    message = parse_telegram_message(
        {
            "message": {
                "message_id": 51,
                "photo": [{"file_id": "small"}, {"file_id": "proof-file"}],
                "chat": {"id": 8001, "type": "private"},
                "from": {"id": 8001},
            }
        }
    )
    assert message is not None
    pending = {"status": "awaiting_payment"}
    submitted = {
        "request_id": "12345678-1234-1234-1234-123456789012",
        "status": "pending_confirmation",
        "ref_code": "nnm",
        "access_months": 3,
        "amount_minor": 3000,
        "currency": "WUSD",
    }
    with patch("app.telegram.renewal_requests._actor", AsyncMock(return_value="actor-1")):
        with patch(
            "app.telegram.renewal_requests.get_open_renewal_request",
            AsyncMock(return_value=pending),
        ):
            with patch(
                "app.telegram.renewal_requests.submit_renewal_payment_proof",
                AsyncMock(return_value=submitted),
            ):
                with patch(
                    "app.telegram.renewal_requests.get_settings"
                ) as settings:
                    settings.return_value.platform_billing_owner_telegram_id = 9001
                    with patch(
                        "app.telegram.renewal_requests.copy_telegram_message", AsyncMock()
                    ) as copied:
                        with patch("app.telegram.renewal_requests._deliver", AsyncMock()) as deliver:
                            with binding_context_scope(whieda_bot_binding):
                                result = await try_handle_renewal_message(
                                    whieda_tenant, message, trace_id="renew-proof"
                                )
    assert result and result["status"] == "pending_confirmation"
    copied.assert_awaited_once()
    owner_markup = deliver.await_args_list[0].kwargs["reply_markup"]
    callbacks = [button["callback_data"] for button in owner_markup["inline_keyboard"][0]]
    assert callbacks[0].startswith("renew:confirm:")
    assert callbacks[1].startswith("renew:reject:")
