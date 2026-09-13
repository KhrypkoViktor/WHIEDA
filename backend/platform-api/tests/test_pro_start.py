"""`/start pro` — the site's PRO lock sends people to the bot for price and action."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.telegram.processor import process_core_telegram_update


def _update(text: str) -> dict:
    return {
        "message": {
            "text": text,
            "chat": {"id": 300, "type": "private"},
            "from": {"id": 300, "username": "guest"},
        }
    }


def _quiet_linking():
    return (
        patch("app.telegram.processor.link_lead_actor_by_username", AsyncMock(return_value=None)),
        patch("app.telegram.processor.fill_lead_actor_user_id", AsyncMock(return_value=None)),
    )


@pytest.mark.asyncio
async def test_start_pro_offers_renewal_to_partner_with_site(whieda_tenant, whieda_bot_binding):
    send = AsyncMock()
    link, fill = _quiet_linking()
    with link, fill, patch(
        "app.telegram.pro_start.resolve_partner_subscription_by_telegram_user_id",
        AsyncMock(return_value={"ref_code": "olga-samtsova", "paid_until": None, "partner_paid": False}),
    ), patch("app.telegram.pro_start.send_telegram_text", send):
        result = await process_core_telegram_update(
            whieda_tenant, _update("/start pro"), "t-pro", binding=whieda_bot_binding
        )
    assert result["route"] == "pro_start" and result["has_site"] is True
    kwargs = send.await_args.kwargs
    assert kwargs["chat_id"] == "300"
    assert "PRO (сайт)" in kwargs["text"] and "30 WWC$" in kwargs["text"] and "3 месяца" in kwargs["text"]
    assert "W$" not in kwargs["text"].replace("WWC$", "")
    assert kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "renew:start"


@pytest.mark.asyncio
async def test_start_pro_offers_site_to_newcomer(whieda_tenant, whieda_bot_binding):
    send = AsyncMock()
    link, fill = _quiet_linking()
    with link, fill, patch(
        "app.telegram.pro_start.resolve_partner_subscription_by_telegram_user_id",
        AsyncMock(return_value=None),
    ), patch("app.telegram.pro_start.send_telegram_text", send):
        result = await process_core_telegram_update(
            whieda_tenant, _update("/start PRO"), "t-pro2", binding=whieda_bot_binding
        )
    assert result["route"] == "pro_start" and result["has_site"] is False
    assert send.await_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "site:create"


@pytest.mark.asyncio
async def test_start_pro_in_group_is_ignored(whieda_tenant, whieda_bot_binding):
    update = _update("/start pro")
    update["message"]["chat"]["type"] = "supergroup"
    send = AsyncMock()
    with patch("app.telegram.pro_start.send_telegram_text", send):
        result = await process_core_telegram_update(whieda_tenant, update, "t-pro3", binding=whieda_bot_binding)
    assert result["route"] != "pro_start"
    send.assert_not_awaited()
