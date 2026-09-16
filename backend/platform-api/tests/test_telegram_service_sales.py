"""Gemini sales in the tunnel: «Оплачено» → who sold → deposit and WWC$; commands."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.service_sales.service import split_sale
from app.telegram.processor import process_core_telegram_update
from app.telegram.service_sales import _plus_months

OWNER = 688931415
KARINA = 2101187096
CLIENT = 60001
FORUM = -1001234567890
TICKET = "11111111-1111-1111-1111-111111111111"
SALE = "22222222-2222-2222-2222-222222222222"
TARIFF = {"offer_code": "gemini_6m", "retail_minor": 399000, "wholesale_direct_minor": 299000, "wholesale_partner_minor": 249000, "partner_share_wusd_minor": 500}


def _callback(data: str, *, user: int = KARINA, thread: int | None = 77) -> dict:
    message = {"message_id": 5, "chat": {"id": FORUM, "type": "supergroup", "is_forum": True}}
    if thread is not None:
        message.update({"is_topic_message": True, "message_thread_id": thread})
    return {"callback_query": {"id": "cb", "data": data, "from": {"id": user, "first_name": "К"}, "message": message}}


def _forum_message(text: str, *, user: int = OWNER, thread: int | None = 78, message_id: int = 300) -> dict:
    message = {"message_id": message_id, "text": text, "chat": {"id": FORUM, "type": "supergroup", "is_forum": True}, "from": {"id": user, "first_name": "В"}}
    if thread is not None:
        message.update({"is_topic_message": True, "message_thread_id": thread})
    return {"message": message}


def _ticket(**over) -> dict:
    base = {"ticket_id": TICKET, "ticket_no": 7, "channel_code": "gemini", "offer_code": "gemini_6m", "offer_title": "Gemini Pro, лицензия на 6 месяцев",
            "user_telegram_user_id": CLIENT, "user_chat_id": CLIENT, "user_display": "Ольга", "admin_telegram_user_id": KARINA, "status": "open",
            "forum_chat_id": FORUM, "forum_thread_id": 77}
    return {**base, **over}


@pytest.fixture
def sales_env(monkeypatch: pytest.MonkeyPatch):
    from app.settings import get_settings

    monkeypatch.setenv("PLATFORM_SUPPORT_ADMIN_TELEGRAM_ID", str(KARINA))
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    get_settings.cache_clear()
    forum = {"chat_id": FORUM, "binding_id": "whieda-test-binding", "bonuses_thread_id": 90, "reports_thread_id": 78}
    with patch("app.telegram.support.get_forum", AsyncMock(return_value=forum)), patch(
        "app.telegram.service_sales.get_forum", AsyncMock(return_value=forum)
    ), patch("app.telegram.service_sales.answer_callback_query", AsyncMock()), patch(
        "app.telegram.support.answer_callback_query", AsyncMock()
    ):
        yield
    get_settings.cache_clear()


def test_split_sale_direct_and_partner():
    direct = split_sale(TARIFF, "owner")
    partner = split_sale(TARIFF, "partner")
    assert direct == {"retail_minor": 399000, "owed_admin_minor": 299000, "partner_share_wusd_minor": 0, "owner_keeps_minor": 100000}
    assert partner == {"retail_minor": 399000, "owed_admin_minor": 249000, "partner_share_wusd_minor": 500, "owner_keeps_minor": 100000}


def test_plus_months_keeps_the_day_when_possible():
    assert _plus_months(datetime(2026, 9, 16, tzinfo=timezone.utc), 6) == datetime(2027, 3, 16, tzinfo=timezone.utc)
    assert _plus_months(datetime(2026, 8, 31, tzinfo=timezone.utc), 18) == datetime(2028, 2, 29, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_paid_button_asks_the_offer_then_who_sold_with_the_partner_suggested(whieda_tenant, whieda_bot_binding, sales_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.service_sales.send_telegram_text", send), patch(
        "app.telegram.service_sales.get_ticket", AsyncMock(return_value=_ticket())
    ), patch("app.telegram.service_sales.get_sale_for_ticket", AsyncMock(return_value=None)), patch(
        "app.telegram.service_sales.suggest_partner_for_client", AsyncMock(return_value={"partner_ref": "olga-samtsova", "partner_actor_id": "olga-samtsova"})
    ), patch("app.telegram.service_sales.partner_label", AsyncMock(return_value="olga@example.com")):
        first = await process_core_telegram_update(whieda_tenant, _callback(f"sale:paid:{TICKET}"), "s1", binding=whieda_bot_binding)
        second = await process_core_telegram_update(whieda_tenant, _callback(f"sale:offer:{TICKET}:gemini_6m"), "s2", binding=whieda_bot_binding)
    assert first["status"] == "offer_requested" and second["status"] == "seller_requested"
    offers = [b["text"] for row in send.await_args_list[0].kwargs["reply_markup"]["inline_keyboard"] for b in row]
    assert offers == ["Gemini Pro, 6 мес", "Gemini Pro, 18 мес", "Отмена"]
    who = [b["text"] for row in send.await_args_list[1].kwargs["reply_markup"]["inline_keyboard"] for b in row]
    assert who == ["Партнёр: olga@example.com", "Виктор (напрямую)", "Отмена"]
    assert send.await_args_list[1].kwargs["message_thread_id"] == 77
    # Nobody's name in the topic: only the e-mail login and the ticket number.
    assert "Ольга" not in send.await_args_list[1].kwargs["text"]


@pytest.mark.asyncio
async def test_partner_sale_writes_off_deposit_credits_wwc_and_feeds_the_bonuses_topic(whieda_tenant, whieda_bot_binding, sales_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    recorded = {
        "sale_id": SALE, "ticket_id": TICKET, "offer_code": "gemini_6m", "seller": "partner", "partner_ref": "olga-samtsova",
        "retail_minor": 399000, "owed_admin_minor": 249000, "partner_share_wusd_minor": 500, "status": "paid",
        "idempotent": False, "deposit_balance_minor": 1751000, "owner_keeps_minor": 100000,
        "partner_bonus": {"actor_id": "olga-samtsova", "amount_minor": 500, "balance_minor": 4600, "telegram_chat_id": "525317405", "idempotent": False},
    }
    record = AsyncMock(return_value=recorded)
    with patch("app.telegram.service_sales.send_telegram_text", send), patch(
        "app.telegram.service_sales.get_ticket", AsyncMock(return_value=_ticket())
    ), patch("app.telegram.service_sales.suggest_partner_for_client", AsyncMock(return_value={"partner_ref": "olga-samtsova"})), patch(
        "app.telegram.service_sales.record_sale", record
    ), patch("app.telegram.service_sales.partner_label", AsyncMock(return_value="olga@example.com")):
        result = await process_core_telegram_update(whieda_tenant, _callback(f"sale:seller:{TICKET}:gemini_6m:partner"), "s3", binding=whieda_bot_binding)
    assert result["status"] == "recorded"
    assert record.await_args.kwargs["seller"] == "partner" and record.await_args.kwargs["partner_ref"] == "olga-samtsova"
    assert record.await_args.kwargs["admin_telegram_user_id"] == KARINA
    by_chat = {}
    for c in send.await_args_list:
        by_chat.setdefault(c.kwargs["chat_id"], []).append(c.kwargs)
    topic = [m for m in by_chat[str(FORUM)] if m.get("message_thread_id") == 77][0]
    assert "Продажа #S-7 · Gemini Pro, 6 мес · 3 990 ₽" in topic["text"]
    assert "Продал: партнёр olga@example.com" in topic["text"]
    assert "Списано с депозита: 2 490 ₽. Остаток депозита: 17 510 ₽" in topic["text"]
    assert "Партнёру: 5 WWC$" in topic["text"] and "Виктору: 1 000 ₽" in topic["text"]
    assert topic["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == f"sale:until:{SALE}:6"
    partner_push = by_chat["525317405"][0]["text"]
    assert "+5 WWC$ за Gemini клиента #S-7" in partner_push and "Баланс: 46 WWC$" in partner_push
    feed = [m for m in by_chat[str(FORUM)] if m.get("message_thread_id") == 90][0]
    assert "olga@example.com · +5 WWC$ · #S-7 · Gemini Pro, 6 мес" in feed["text"]
    assert str(OWNER) in by_chat  # the owner gets a copy of the summary


@pytest.mark.asyncio
async def test_direct_sale_has_no_partner_share_and_warns_on_low_deposit(whieda_tenant, whieda_bot_binding, sales_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    recorded = {
        "sale_id": SALE, "ticket_id": TICKET, "offer_code": "gemini_6m", "seller": "owner", "partner_ref": None,
        "retail_minor": 399000, "owed_admin_minor": 299000, "partner_share_wusd_minor": 0, "status": "paid",
        "idempotent": False, "deposit_balance_minor": 100000, "owner_keeps_minor": 100000, "partner_bonus": None,
    }
    with patch("app.telegram.service_sales.send_telegram_text", send), patch(
        "app.telegram.service_sales.get_ticket", AsyncMock(return_value=_ticket())
    ), patch("app.telegram.service_sales.record_sale", AsyncMock(return_value=recorded)):
        result = await process_core_telegram_update(whieda_tenant, _callback(f"sale:seller:{TICKET}:gemini_6m:owner", user=OWNER), "s4", binding=whieda_bot_binding)
    assert result["status"] == "recorded"
    topic = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(FORUM)][0]["text"]
    assert "Продал: Виктор (напрямую)" in topic and "Списано с депозита: 2 990 ₽" in topic
    assert "Партнёру" not in topic and "⚠️ Депозита на следующую лицензию не хватает" in topic
    assert not [c for c in send.await_args_list if c.kwargs.get("message_thread_id") == 90]


@pytest.mark.asyncio
async def test_activation_tells_the_client_and_closes_the_ticket(whieda_tenant, whieda_bot_binding, sales_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    sale = {"sale_id": SALE, "ticket_id": TICKET, "offer_code": "gemini_6m", "status": "paid"}
    close = AsyncMock(return_value={"ok": True, "status": "closed"})
    with patch("app.telegram.service_sales.send_telegram_text", send), patch(
        "app.telegram.service_sales.get_sale", AsyncMock(return_value=sale)
    ), patch("app.telegram.service_sales.activate_sale", AsyncMock(return_value={**sale, "status": "activated"})) as activate, patch(
        "app.telegram.service_sales.get_ticket", AsyncMock(return_value=_ticket())
    ), patch("app.telegram.support._close_ticket_everywhere", close):
        result = await process_core_telegram_update(whieda_tenant, _callback(f"sale:until:{SALE}:6"), "s5", binding=whieda_bot_binding)
    assert result["status"] == "activated"
    until = activate.await_args.kwargs["activated_until"]
    assert until == _plus_months(datetime.now(timezone.utc), 6).replace(microsecond=until.microsecond)
    client = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(CLIENT)][0]["text"]
    assert client.startswith("Лицензия Gemini Pro, 6 мес активна до ")
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_strangers_cannot_run_sales(whieda_tenant, whieda_bot_binding, sales_env):
    record = AsyncMock()
    with patch("app.telegram.service_sales.record_sale", record), patch("app.telegram.service_sales.send_telegram_text", AsyncMock()):
        result = await process_core_telegram_update(whieda_tenant, _callback(f"sale:seller:{TICKET}:gemini_6m:owner", user=CLIENT), "s6", binding=whieda_bot_binding)
    assert result["status"] == "forbidden"
    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_topup_asks_karina_to_confirm_and_confirmation_updates_the_deposit(whieda_tenant, whieda_bot_binding, sales_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.service_sales.send_telegram_text", send), patch(
        "app.telegram.service_sales.add_topup", AsyncMock(return_value={"entry_id": "33333333-3333-3333-3333-333333333333", "amount_minor": 2000000})
    ) as add, patch("app.telegram.service_sales.confirm_topup", AsyncMock(return_value={"entry_id": "e", "amount_minor": 2000000, "admin_telegram_user_id": KARINA, "deposit_balance_minor": 2000000})):
        topup = await process_core_telegram_update(whieda_tenant, _forum_message("перевёл 20 000"), "s7", binding=whieda_bot_binding)
        confirm = await process_core_telegram_update(whieda_tenant, _callback("dep:ok:33333333-3333-3333-3333-333333333333", thread=78), "s8", binding=whieda_bot_binding)
    assert topup["status"] == "topup_pending" and add.await_args.kwargs["amount_minor"] == 2000000
    ask = send.await_args_list[0].kwargs
    assert ask["message_thread_id"] == 78 and "Пополнение депозита 20 000 ₽" in ask["text"]
    assert ask["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "dep:ok:33333333-3333-3333-3333-333333333333"
    assert confirm["status"] == "confirmed"
    texts = [c.kwargs["text"] for c in send.await_args_list[1:]]
    assert any("Получено 20 000 ₽. Депозит: 20 000 ₽." in t for t in texts)


@pytest.mark.asyncio
async def test_only_karina_confirms_a_topup(whieda_tenant, whieda_bot_binding, sales_env):
    confirm = AsyncMock()
    with patch("app.telegram.service_sales.confirm_topup", confirm), patch("app.telegram.service_sales.send_telegram_text", AsyncMock()):
        result = await process_core_telegram_update(whieda_tenant, _callback("dep:ok:33333333-3333-3333-3333-333333333333", user=OWNER, thread=78), "s9", binding=whieda_bot_binding)
    assert result["status"] == "forbidden"
    confirm.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_lists_partners_by_email_and_the_deposit(whieda_tenant, whieda_bot_binding, sales_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    report = {
        "since": datetime(2026, 9, 1, tzinfo=timezone.utc), "sales": 3, "retail_minor": 1197000, "owed_minor": 797000, "owner_minor": 300000,
        "by_partner": [{"partner_ref": "olga-samtsova", "sales": 2, "share_wusd_minor": 1000}],
        "deposit_balance_minor": 1203000, "pending_topups": [], "low_balance": False,
    }
    with patch("app.telegram.service_sales.send_telegram_text", send), patch(
        "app.telegram.service_sales.month_report", AsyncMock(return_value=report)
    ), patch("app.telegram.service_sales.partner_label", AsyncMock(return_value="olga@example.com")):
        result = await process_core_telegram_update(whieda_tenant, _forum_message("отчёт", user=KARINA), "s10", binding=whieda_bot_binding)
    assert result["status"] == "report"
    text = send.await_args.kwargs["text"]
    assert "Продаж: 3 на 11 970 ₽" in text and "Депозит сейчас: 12 030 ₽" in text
    assert "• olga@example.com — 2 прод., 10 WWC$" in text
    assert "olga-samtsova" not in text


@pytest.mark.asyncio
async def test_tariff_command_updates_prices_and_explains_the_split(whieda_tenant, whieda_bot_binding, sales_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    new = {**TARIFF, "retail_minor": 399000, "wholesale_direct_minor": 299000, "wholesale_partner_minor": 249000, "partner_share_wusd_minor": 500}
    with patch("app.telegram.service_sales.send_telegram_text", send), patch("app.telegram.service_sales.set_tariff", AsyncMock(return_value=new)) as set_t:
        result = await process_core_telegram_update(whieda_tenant, _forum_message("тариф gemini_6m 3990 2990 2490 5"), "s11", binding=whieda_bot_binding)
    assert result["status"] == "tariff_set"
    assert set_t.await_args.kwargs == {"offer_code": "gemini_6m", "retail_minor": 399000, "wholesale_direct_minor": 299000, "wholesale_partner_minor": 249000, "partner_share_wusd_minor": 500, "updated_by": OWNER}
    text = send.await_args.kwargs["text"]
    assert "прямая — Карине 2 990 ₽, Виктору 1 000 ₽" in text and "партнёрская — Карине 2 490 ₽, партнёру 5 WWC$, Виктору 1 000 ₽" in text


@pytest.mark.asyncio
async def test_service_commands_are_never_relayed_to_a_client(whieda_tenant, whieda_bot_binding, sales_env):
    """«баланс» typed by the administrator inside a ticket topic is a command, not a message to the client."""
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    relay = AsyncMock()
    report = {"since": datetime(2026, 9, 1, tzinfo=timezone.utc), "sales": 0, "retail_minor": 0, "owed_minor": 0, "owner_minor": 0, "by_partner": [], "deposit_balance_minor": 500000, "pending_topups": [], "low_balance": False}
    with patch("app.telegram.service_sales.send_telegram_text", send), patch("app.telegram.service_sales.month_report", AsyncMock(return_value=report)), patch(
        "app.telegram.support._relay_admin_to_user", relay
    ), patch("app.telegram.support.find_ticket_by_forum_thread", AsyncMock(return_value=_ticket())):
        result = await process_core_telegram_update(whieda_tenant, _forum_message("баланс", user=KARINA, thread=77), "s12", binding=whieda_bot_binding)
    assert result["status"] == "balance"
    relay.assert_not_awaited()
    assert "Депозит у администратора: 5 000 ₽." in send.await_args.kwargs["text"]
