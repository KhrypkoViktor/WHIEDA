"""Owner's multi-line «оплата», personal «цена», and «статус» with CLUB."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.subscriptions.pricing import PaymentLine
from app.subscriptions.service import SubscriptionError
from app.telegram.processor import process_core_telegram_update

OWNER = 688931415
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)


def _msg(text: str, message_id: int = 500) -> dict:
    return {"message": {"message_id": message_id, "text": text, "chat": {"id": OWNER, "type": "private"}, "from": {"id": OWNER}}}


@pytest.fixture
def owner_env(monkeypatch: pytest.MonkeyPatch):
    from app.settings import get_settings

    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def quiet(monkeypatch: pytest.MonkeyPatch):
    with patch("app.telegram.processor.link_lead_actor_by_username", AsyncMock(return_value=None)), patch(
        "app.telegram.processor.fill_lead_actor_user_id", AsyncMock(return_value=None)
    ), patch("app.telegram.processor.merge_anonymous_actor_into_partner", AsyncMock(return_value=None)):
        yield


@pytest.mark.asyncio
async def test_multiline_payment_shows_receipt_preview(whieda_tenant, whieda_bot_binding, owner_env, quiet):
    deliver = AsyncMock()
    intent = AsyncMock(return_value={
        "intent_id": "11111111-1111-1111-1111-111111111111", "ref_code": "zinaida", "display_name": "Зинаида Гладышева",
        "hostname": "zinaida.wwc.best", "currency": "WUSD", "received_minor": 10500, "paid_until": None, "club_paid_until": None,
        "lines": [
            {"product_code": "platform_subscription", "amount_minor": 3000, "currency": "WUSD", "access_months": 3, "promo": False, "note": "", "list_price_minor": 3000},
            {"product_code": "club_subscription", "amount_minor": 7500, "currency": "WUSD", "access_months": 3, "promo": True, "note": "пакет PRO + клуб", "list_price_minor": 12000},
        ],
        "price_reasons": {},
    })
    with patch("app.telegram.billing._deliver", deliver), patch("app.telegram.billing.create_lines_intent", intent):
        result = await process_core_telegram_update(
            whieda_tenant, _msg("оплата ref:zinaida\nпакет 105 WWC$\nполучено 105 WWC$"), "t1", binding=whieda_bot_binding
        )
    assert result["status"] == "preview"
    kwargs = intent.await_args.kwargs
    assert kwargs["identifier"] == "ref:zinaida" and kwargs["received_minor"] == 10500 and kwargs["telegram_message_id"] == 500
    assert [(l.product_code, l.amount_minor, l.promo) for l in kwargs["lines"]] == [("platform_subscription", 3000, False), ("club_subscription", 7500, True)]
    text = deliver.await_args.args[1]
    assert "Зинаида Гладышева" in text and "PRO (сайт): 30 WWC$ (3 000 ₽), 3 мес." in text
    assert "CLUB: 75 WWC$ (7 500 ₽), 3 мес. — акция" in text and "Получено: 105 WWC$" in text
    assert "Сумма сходится" in text
    buttons = [b["text"] for b in deliver.await_args.kwargs["reply_markup"]["inline_keyboard"][0]]
    assert buttons == ["Подтвердить", "Отмена"]


@pytest.mark.asyncio
async def test_bad_total_or_off_price_line_records_nothing(whieda_tenant, whieda_bot_binding, owner_env, quiet):
    deliver = AsyncMock()
    intent = AsyncMock(side_effect=SubscriptionError("CLUB: 75 WWC$ вместо 120 WWC$. Добавьте слово «акция» или исправьте сумму."))
    with patch("app.telegram.billing._deliver", deliver), patch("app.telegram.billing.create_lines_intent", intent):
        result = await process_core_telegram_update(
            whieda_tenant, _msg("оплата ref:zinaida\nклуб 75 WWC$ 3"), "t2", binding=whieda_bot_binding
        )
    assert result["status"] == "invalid_command"
    assert "Добавьте слово «акция»" in deliver.await_args.args[1]


@pytest.mark.asyncio
async def test_price_command_previews_and_confirms_personal_price(whieda_tenant, whieda_bot_binding, owner_env, quiet):
    deliver = AsyncMock()
    with patch("app.telegram.billing._deliver", deliver), patch(
        "app.telegram.billing.resolve_partner_for_billing", AsyncMock(return_value={"ref_code": "olga-samtsova", "display_name": "Ольга Самцова", "hostname": "olga-samtsova.wwc.best"})
    ):
        result = await process_core_telegram_update(
            whieda_tenant, _msg("цена ref:olga-samtsova PRO 15 WWC$ постоянная скидка 50% за участие в проекте"), "t3", binding=whieda_bot_binding
        )
    assert result["status"] == "price_preview"
    text = deliver.await_args.args[1]
    assert "Ольга Самцова" in text and "PRO (сайт): 15 WWC$ (1 500 ₽)" in text and "постоянная скидка 50%" in text
    cb = deliver.await_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert cb.startswith("billing:price:")

    set_price = AsyncMock(return_value={"ref_code": "olga-samtsova", "product_code": "platform_subscription", "price_wusd_minor": 1500, "reason": "постоянная скидка 50% за участие в проекте"})
    with patch("app.telegram.billing._deliver", deliver), patch("app.telegram.billing.set_personal_price", set_price), patch(
        "app.telegram.billing.answer_callback_query", AsyncMock()
    ):
        callback = {"callback_query": {"id": "c1", "data": cb, "from": {"id": OWNER}, "message": {"message_id": 7, "chat": {"id": OWNER, "type": "private"}}}}
        result = await process_core_telegram_update(whieda_tenant, callback, "t4", binding=whieda_bot_binding)
    assert result["status"] == "price_set"
    set_price.assert_awaited_once()
    assert set_price.await_args.kwargs["price_wusd_minor"] == 1500
    assert "Готово" in deliver.await_args.args[1]


@pytest.mark.asyncio
async def test_status_shows_pro_and_club(whieda_tenant, whieda_bot_binding, owner_env, quiet):
    deliver = AsyncMock()
    partner = {"ref_code": "zinaida", "display_name": "Зинаида Гладышева", "hostname": "zinaida.wwc.best",
               "paid_until": datetime(2026, 12, 14, tzinfo=timezone.utc), "club_paid_until": datetime(2026, 12, 14, tzinfo=timezone.utc)}
    with patch("app.telegram.billing._deliver", deliver), patch("app.telegram.billing.resolve_partner_for_billing", AsyncMock(return_value=partner)):
        result = await process_core_telegram_update(whieda_tenant, _msg("статус ref:zinaida"), "t5", binding=whieda_bot_binding)
    assert result["status"] == "status"
    text = deliver.await_args.args[1]
    assert "PRO (сайт)" in text and "CLUB: до 14.12.2026" in text


@pytest.mark.asyncio
async def test_bonus_line_reaches_the_intent_and_the_preview(whieda_tenant, whieda_bot_binding, owner_env, quiet):
    deliver = AsyncMock()
    intent = AsyncMock(return_value={
        "intent_id": "22222222-2222-2222-2222-222222222222", "ref_code": "olesya", "display_name": "Олеся Вселенная",
        "hostname": "olesya.wwc.best", "currency": "WUSD", "received_minor": 10000, "paid_until": None, "club_paid_until": None,
        "bonus_minor": 500, "bonus_balance_minor": 1200,
        "lines": [
            {"product_code": "platform_subscription", "amount_minor": 3000, "currency": "WUSD", "access_months": 3, "promo": False, "note": "", "list_price_minor": 3000},
            {"product_code": "club_subscription", "amount_minor": 7500, "currency": "WUSD", "access_months": 3, "promo": True, "note": "пакет PRO + клуб", "list_price_minor": 12000},
            {"product_code": "bonus_offset", "amount_minor": 500, "currency": "WUSD", "access_months": 0, "promo": False, "note": "", "list_price_minor": None},
        ],
        "price_reasons": {},
    })
    with patch("app.telegram.billing._deliver", deliver), patch("app.telegram.billing.create_lines_intent", intent):
        result = await process_core_telegram_update(
            whieda_tenant, _msg("оплата ref:olesya\nпакет 105 WWC$\nполучено 100 WWC$\nбонусами 5 WWC$", 501), "t6", binding=whieda_bot_binding
        )
    assert result["status"] == "preview"
    assert intent.await_args.kwargs["bonus_minor"] == 500 and intent.await_args.kwargs["received_minor"] == 10000
    text = deliver.await_args.args[1]
    assert "Получено: 100 WWC$" in text and "Бонусами: 5 WWC$ (на балансе 12 WWC$, останется 7 WWC$)" in text
    assert "bonus_offset" not in text and "строки = получено + бонусы" in text


@pytest.mark.asyncio
async def test_confirmed_payment_reports_the_bonus_offset(whieda_tenant, whieda_bot_binding, owner_env, quiet):
    deliver = AsyncMock()
    payment = {
        "payment_id": "33333333-3333-3333-3333-333333333333", "tenant_id": "whieda", "ref_code": "olesya", "multiline": True,
        "period_end": datetime(2026, 12, 21, tzinfo=timezone.utc), "paid_until": datetime(2026, 12, 21, tzinfo=timezone.utc),
        "club_paid_until": datetime(2026, 12, 21, tzinfo=timezone.utc), "idempotent": False, "referral_bonus": None,
        "lines": [{"product_code": "platform_subscription", "amount_minor": 3000, "currency": "WUSD"}],
        "bonus_offset": {"bonus_offset_minor": 500, "bonus_balance_minor": 700, "actor_id": "olesya-vselennaya"},
    }
    with patch("app.telegram.billing._deliver", deliver), patch("app.telegram.billing.confirm_payment_intent", AsyncMock(return_value=payment)), patch(
        "app.telegram.billing.notify_payment_participants", AsyncMock()
    ), patch("app.telegram.billing.answer_callback_query", AsyncMock()):
        callback = {"callback_query": {"id": "c2", "data": "billing:confirm:33333333333333333333333333333333", "from": {"id": OWNER}, "message": {"message_id": 8, "chat": {"id": OWNER, "type": "private"}}}}
        result = await process_core_telegram_update(whieda_tenant, callback, "t7", binding=whieda_bot_binding)
    assert result["status"] == "confirmed"
    text = deliver.await_args.args[1]
    assert "Платёж записан." in text and "Бонусами списано: 5 WWC$. Остаток бонусов: 7 WWC$." in text and "PRO до: 21.12.2026" in text


@pytest.mark.asyncio
async def test_unlimited_previews_and_confirms(whieda_tenant, whieda_bot_binding, owner_env, quiet):
    deliver = AsyncMock()
    partner = {"ref_code": "dev", "display_name": "Виктор Хрипко", "hostname": "dev.wwc.best", "paid_until": datetime(2026, 9, 21, 21, tzinfo=timezone.utc)}
    with patch("app.telegram.billing._deliver", deliver), patch("app.telegram.billing.resolve_partner_for_billing", AsyncMock(return_value=partner)):
        result = await process_core_telegram_update(whieda_tenant, _msg("безлимит ref:dev", 502), "t8", binding=whieda_bot_binding)
    assert result["status"] == "unlimited_preview"
    text = deliver.await_args.args[1]
    assert "Виктор Хрипко (ref:dev)" in text and "Безлимит" in text and "без платежа" in text
    cb = deliver.await_args.kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert cb.startswith("billing:unlimited:")

    unlimited = AsyncMock(return_value={"ref_code": "dev", "paid_until": datetime(2099, 12, 31, 20, 59, 59, tzinfo=timezone.utc)})
    with patch("app.telegram.billing._deliver", deliver), patch("app.telegram.billing.set_unlimited_access", unlimited), patch(
        "app.telegram.billing.answer_callback_query", AsyncMock()
    ):
        callback = {"callback_query": {"id": "c3", "data": cb, "from": {"id": OWNER}, "message": {"message_id": 9, "chat": {"id": OWNER, "type": "private"}}}}
        result = await process_core_telegram_update(whieda_tenant, callback, "t9", binding=whieda_bot_binding)
    assert result["status"] == "unlimited_set"
    unlimited.assert_awaited_once_with("whieda", ref_code="dev")
    assert "2099" in deliver.await_args.args[1]
    # A stranger's callback token is refused, nothing is written.
    with patch("app.telegram.billing._deliver", deliver), patch("app.telegram.billing.set_unlimited_access", unlimited), patch(
        "app.telegram.billing.answer_callback_query", AsyncMock()
    ):
        again = await process_core_telegram_update(whieda_tenant, callback, "t10", binding=whieda_bot_binding)
    assert again["status"] == "invalid_callback" and unlimited.await_count == 1
