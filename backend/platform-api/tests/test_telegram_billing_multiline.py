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
async def test_owner_payment_command_works_on_the_production_minimal_profile(whieda_tenant, whieda_bot_binding, owner_env, quiet, monkeypatch):
    """Prod (minimal) dropped «оплата ref:fedorov 3000 rub 3» into the advisor on 19.09.2026."""
    from app.settings import get_settings

    monkeypatch.setenv("PLATFORM_TELEGRAM_UI_PROFILE", "minimal")
    get_settings.cache_clear()
    deliver = AsyncMock()
    intent = AsyncMock(return_value={
        "intent_id": "11111111-1111-1111-1111-111111111111", "ref_code": "fedorov", "display_name": "Фёдоров",
        "hostname": "fedorov.wwc.best", "currency": "RUB", "amount_minor": 300000, "access_months": 3, "paid_until": None,
        "club_paid_until": None, "lines": None, "price_reasons": {},
        "period_end": datetime(2026, 12, 21, tzinfo=timezone.utc), "grace_until": datetime(2026, 12, 24, tzinfo=timezone.utc),
    })
    advisor = AsyncMock()
    try:
        with patch("app.telegram.billing._deliver", deliver), patch("app.telegram.billing.create_lines_intent", intent), patch(
            "app.telegram.billing.create_payment_intent", intent
        ), patch("app.telegram.processor.handle_advisor_query", advisor):
            result = await process_core_telegram_update(whieda_tenant, _msg("оплата ref:fedorov 3000 rub 3"), "t-min", binding=whieda_bot_binding)
    finally:
        get_settings.cache_clear()
    assert result["status"] == "preview"
    advisor.assert_not_awaited()


def test_bare_price_and_status_words_are_not_billing_commands():
    from app.telegram.billing import is_billing_command_candidate

    assert not is_billing_command_candidate("цена")
    assert not is_billing_command_candidate("статус")
    assert is_billing_command_candidate("цена ref:olga PRO 15 WWC$ скидка")
    assert is_billing_command_candidate("статус ref:kira")
    assert is_billing_command_candidate("оплата ref:kira 3000 rub 3")
