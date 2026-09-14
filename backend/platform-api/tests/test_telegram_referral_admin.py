from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.settings import get_settings
from app.telegram.bindings import binding_context_scope
from app.telegram.processor import process_core_telegram_update
from app.telegram.referral_admin import (
    is_referral_admin_command_candidate,
    try_handle_referral_admin_callback,
    try_handle_referral_admin_message,
)


def _owner(monkeypatch, user_id: int = 7001) -> None:
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(user_id))
    get_settings.cache_clear()


def _message(text: str, *, user_id: int = 7001) -> dict:
    return {
        "update_id": 5001,
        "message": {
            "message_id": 77,
            "text": text,
            "chat": {"id": user_id, "type": "private"},
            "from": {"id": user_id},
        },
    }


def _callback(data: str, *, user_id: int = 7001) -> dict:
    return {
        "update_id": 5002,
        "callback_query": {
            "id": "callback-refadmin",
            "data": data,
            "from": {"id": user_id},
            "message": {"chat": {"id": user_id, "type": "private"}},
        },
    }


def test_admin_referral_candidates_do_not_capture_regular_messages():
    assert is_referral_admin_command_candidate("бонусы ref:igor")
    assert is_referral_admin_command_candidate("корректировка-бонусов ref:igor +10 W$ тест")
    assert not is_referral_admin_command_candidate("расскажи про бонусы")
    assert not is_referral_admin_command_candidate("реферер потом")


@pytest.mark.asyncio
async def test_non_owner_cannot_reach_referral_database(monkeypatch, whieda_tenant, whieda_bot_binding):
    _owner(monkeypatch)
    with binding_context_scope(whieda_bot_binding):
        with patch("app.telegram.referral_admin.referral_actor_by_ref", AsyncMock()) as actor:
            with patch("app.telegram.referral_admin.send_telegram_text", AsyncMock()) as deliver:
                result = await try_handle_referral_admin_message(
                    whieda_tenant, _message("бонусы ref:igor", user_id=9999)
                )
    assert result["status"] == "forbidden"
    actor.assert_not_called()
    deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_admin_assign_sends_confirmable_preview(monkeypatch, whieda_tenant, whieda_bot_binding):
    _owner(monkeypatch)
    intent_id = uuid.uuid4()
    create = AsyncMock(return_value={"intent_id": intent_id})
    with binding_context_scope(whieda_bot_binding):
        with patch("app.telegram.referral_admin.create_referral_admin_intent", create):
            with patch("app.telegram.referral_admin.send_telegram_text", AsyncMock()) as deliver:
                result = await try_handle_referral_admin_message(
                    whieda_tenant, _message("реферер ref:igor ref:elena")
                )
    assert result["status"] == "assign_preview"
    create.assert_awaited_once_with(
        "whieda",
        operation="assign_referrer",
        payload={"invitee_ref": "igor", "inviter_ref": "elena"},
        telegram_chat_id=7001,
        telegram_user_id=7001,
    )
    button = deliver.await_args.kwargs["reply_markup"]["inline_keyboard"][0][0]
    assert button["callback_data"] == f"refadmin:confirm:{intent_id.hex}"


@pytest.mark.asyncio
async def test_admin_adjustment_requires_reason_and_confirms_once(monkeypatch, whieda_tenant, whieda_bot_binding):
    _owner(monkeypatch)
    intent_id = uuid.uuid4()
    create = AsyncMock(return_value={"intent_id": intent_id})
    with binding_context_scope(whieda_bot_binding):
        with patch("app.telegram.referral_admin.create_referral_admin_intent", create):
            with patch("app.telegram.referral_admin.send_telegram_text", AsyncMock()):
                result = await try_handle_referral_admin_message(
                    whieda_tenant, _message("корректировка-бонусов ref:igor -10,50 W$ возврат")
                )
    assert result["status"] == "adjust_preview"
    assert create.await_args.kwargs["payload"] == {
        "ref_code": "igor", "amount_minor": -1050, "reason": "возврат"
    }

    confirm = AsyncMock(return_value={"operation": "adjust_bonus", "amount_minor": -1050, "idempotent": False})
    with binding_context_scope(whieda_bot_binding):
        with patch("app.telegram.referral_admin.confirm_referral_admin_intent", confirm):
            with patch("app.telegram.referral_admin.answer_callback_query", AsyncMock()) as ack:
                with patch("app.telegram.referral_admin.send_telegram_text", AsyncMock()) as deliver:
                    callback_result = await try_handle_referral_admin_callback(
                        whieda_tenant, _callback(f"refadmin:confirm:{intent_id.hex}")
                    )
    assert callback_result["status"] == "confirmed"
    confirm.assert_awaited_once_with(
        "whieda",
        intent_id=str(intent_id),
        telegram_chat_id=7001,
        telegram_user_id=7001,
    )
    ack.assert_awaited_once()
    # People read WWC$; the owner may still type W$ or WWC$ in the command.
    assert "−10,50 WWC$" in deliver.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_referral_admin_routes_before_advisor(monkeypatch, whieda_tenant, whieda_bot_binding):
    _owner(monkeypatch)
    referral_admin = AsyncMock(return_value={"ok": True, "route": "referral_admin"})
    with patch("app.telegram.processor.try_handle_referral_admin_message", referral_admin):
        with patch("app.telegram.processor.handle_advisor_query", AsyncMock()) as advisor:
            result = await process_core_telegram_update(
                whieda_tenant,
                _message("бонусы ref:igor"),
                "trace-refadmin",
                binding=whieda_bot_binding,
            )
    assert result["route"] == "referral_admin"
    advisor.assert_not_called()
