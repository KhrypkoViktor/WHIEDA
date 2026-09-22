from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.settings import get_settings
from app.subscriptions.service import SubscriptionError
from app.telegram.billing import (
    _amount,
    _intent_keyboard,
    is_billing_command_candidate,
    parse_billing_command,
    try_handle_billing_callback,
    try_handle_billing_message,
)
from app.telegram.bindings import binding_context_scope
from app.telegram.processor import process_core_telegram_update


UTC = timezone.utc


def _owner(monkeypatch, user_id: int = 7001) -> None:
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(user_id))
    get_settings.cache_clear()


def _message(text: str, *, user_id: int = 7001, message_id: int = 41) -> dict:
    return {
        "update_id": 1000 + message_id,
        "message": {
            "message_id": message_id,
            "text": text,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id},
        },
    }


def _callback(data: str, *, user_id: int = 7001) -> dict:
    return {
        "update_id": 2001,
        "callback_query": {
            "id": "callback-1",
            "data": data,
            "from": {"id": user_id},
            "message": {"chat": {"id": user_id, "type": "private"}},
        },
    }


def test_parse_billing_commands_and_minor_units():
    rub = parse_billing_command("ОПЛАТА @OnlineElena 3000 RUB")
    assert (rub.kind, rub.identifier, rub.amount_minor, rub.currency) == (
        "pay",
        "@OnlineElena",
        300000,
        "RUB",
    )
    whieda_dollars = parse_billing_command("/pay ref:fedorov 30 W$")
    assert (whieda_dollars.amount_minor, whieda_dollars.currency) == (3000, "WUSD")
    half_year = parse_billing_command("оплата ref:fedorov 54 W$ 6")
    assert half_year.access_months == 6
    canonical_alias = parse_billing_command("/pay ref:fedorov 30,50 wusd")
    assert (canonical_alias.amount_minor, canonical_alias.currency) == (3050, "WUSD")
    assert parse_billing_command("/status ref:fedorov").kind == "status"
    assert parse_billing_command("/due").kind == "due"


@pytest.mark.parametrize(
    "text",
    [
        "оплата @name 10,50 RUB",
        "оплата @name 10 USD",
        "оплата @name 0 RUB",
        "оплата @name 10 BYN",
        "оплата @name 10 W$ extra",
        "оплата @name 10 W$ 5",
        "статус",
    ],
)
def test_parse_billing_rejects_invalid_or_extra_fields(text: str):
    with pytest.raises(SubscriptionError):
        parse_billing_command(text)


def test_billing_candidate_does_not_capture_free_text():
    assert is_billing_command_candidate("оплата @name 10 RUB")
    assert not is_billing_command_candidate("расскажи про оплату и сайт")
    assert not is_billing_command_candidate("оплатапотом @name 10 RUB")


@pytest.mark.parametrize(
    "text",
    ["цена спирулина", "цена активатора клеток", "цена", "статус заказа", "статус моей подписки"],
)
def test_price_and_status_questions_belong_to_the_advisor(text: str):
    """22.09.2026: «цена спирулина» answered «Команда недоступна» to everyone
    except the owner, because any argument made it look like a billing command."""
    assert not is_billing_command_candidate(text)


@pytest.mark.parametrize(
    "text",
    ["цена ref:olga PRO 15 WWC$", "статус @olga_samtsova", "статус ref:makarova"],
)
def test_owner_price_and_status_commands_still_route_to_billing(text: str):
    assert is_billing_command_candidate(text)


def test_internal_whieda_dollar_code_is_displayed_as_wwc_dollar():
    # The internal code stayed WUSD; the money in messages is WWC$ since the
    # currency naming of 14.09.2026 (1 W$ = 1 WWC$ = 100 ₽).
    assert _amount(3000, "WUSD") == "30 WWC$"


def test_callback_payload_fits_telegram_limit():
    markup = _intent_keyboard(uuid.uuid4())
    for button in markup["inline_keyboard"][0]:
        assert len(button["callback_data"].encode("utf-8")) <= 64


@pytest.mark.parametrize(
    "command",
    ["оплата garbage", "статус @onlineelena", "/due"],
)
@pytest.mark.asyncio
async def test_non_owner_is_rejected_before_parser_or_database(
    monkeypatch, whieda_tenant, whieda_bot_binding, command
):
    _owner(monkeypatch)
    update = _message(command, user_id=9999)
    with binding_context_scope(whieda_bot_binding):
        with patch("app.telegram.billing.create_payment_intent", AsyncMock()) as create:
            with patch("app.telegram.billing.resolve_partner_for_billing", AsyncMock()) as resolve:
                with patch("app.telegram.billing.list_due_subscriptions", AsyncMock()) as due:
                    with patch("app.telegram.billing.send_telegram_text", AsyncMock()) as deliver:
                        result = await try_handle_billing_message(whieda_tenant, update)
    assert result["status"] == "forbidden"
    create.assert_not_called()
    resolve.assert_not_called()
    due.assert_not_called()
    deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_payment_command_sends_preview_without_writing_ledger(
    monkeypatch, whieda_tenant, whieda_bot_binding
):
    _owner(monkeypatch)
    intent_id = uuid.uuid4()
    now = datetime(2026, 9, 9, 9, tzinfo=UTC)
    intent = {
        "intent_id": intent_id,
        "display_name": "Елена",
        "ref_code": "onlineelena",
        "hostname": "elena.wwc.best",
        "amount_minor": 300000,
        "currency": "RUB",
        "access_months": 3,
        "paid_until": now,
        "period_end": datetime(2026, 12, 9, 9, tzinfo=UTC),
        "grace_until": datetime(2026, 12, 12, 9, tzinfo=UTC),
    }
    create = AsyncMock(return_value=intent)
    with binding_context_scope(whieda_bot_binding):
        with patch("app.telegram.billing.create_payment_intent", create):
            with patch("app.telegram.billing.send_telegram_text", AsyncMock()) as deliver:
                result = await try_handle_billing_message(
                    whieda_tenant, _message("оплата @onlineelena 3000 RUB")
                )
    assert result["status"] == "preview"
    create.assert_awaited_once()
    assert "Станет оплачено до" in deliver.await_args.kwargs["text"]
    assert deliver.await_args.kwargs["reply_markup"]["inline_keyboard"]


@pytest.mark.asyncio
async def test_confirm_callback_records_once_and_reports_actual_period(
    monkeypatch, whieda_tenant, whieda_bot_binding
):
    _owner(monkeypatch)
    intent_id = uuid.uuid4()
    period_end = datetime(2026, 12, 9, 9, tzinfo=UTC)
    payment = {
        "payment_id": uuid.uuid4(),
        "period_end": period_end,
        "idempotent": False,
    }
    confirm = AsyncMock(return_value=payment)
    token = str(intent_id).replace("-", "")
    with binding_context_scope(whieda_bot_binding):
        with patch("app.telegram.billing.confirm_payment_intent", confirm):
            with patch("app.telegram.billing.answer_callback_query", AsyncMock()) as ack:
                with patch("app.telegram.billing.send_telegram_text", AsyncMock()) as deliver:
                    result = await try_handle_billing_callback(
                        whieda_tenant, _callback(f"billing:confirm:{token}")
                    )
    assert result["status"] == "confirmed"
    confirm.assert_awaited_once_with(
        "whieda",
        intent_id=str(intent_id),
        telegram_chat_id=7001,
        telegram_user_id=7001,
    )
    ack.assert_awaited_once()
    assert "Платёж записан" in deliver.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_billing_callback_runs_before_catalog_callback(
    monkeypatch, whieda_tenant, whieda_bot_binding
):
    _owner(monkeypatch)
    billing = AsyncMock(return_value={"ok": True, "route": "billing_callback"})
    with patch("app.telegram.processor.try_handle_billing_callback", billing):
        with patch("app.telegram.processor.handle_callback_query", AsyncMock()) as catalog:
            result = await process_core_telegram_update(
                whieda_tenant,
                _callback(f"billing:confirm:{uuid.uuid4().hex}"),
                "trace-billing-callback",
                binding=whieda_bot_binding,
            )
    assert result["route"] == "billing_callback"
    catalog.assert_not_called()


@pytest.mark.asyncio
async def test_billing_message_runs_before_onboarding_and_advisor(
    monkeypatch, whieda_tenant, whieda_bot_binding
):
    _owner(monkeypatch)
    billing = AsyncMock(return_value={"ok": True, "route": "billing"})
    with patch("app.telegram.processor.try_handle_billing_message", billing):
        with patch("app.telegram.processor.handle_onboarding", AsyncMock()) as onboarding:
            with patch("app.telegram.processor.handle_advisor_query", AsyncMock()) as advisor:
                result = await process_core_telegram_update(
                    whieda_tenant,
                    _message("оплата @onlineelena 3000 RUB"),
                    "trace-billing",
                    binding=whieda_bot_binding,
                )
    assert result["route"] == "billing"
    onboarding.assert_not_called()
    advisor.assert_not_called()
