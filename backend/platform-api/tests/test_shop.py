"""Мастерская WWC (03.10.2026): каталог, цены по стране, ссылка shop_ с ref и без,
заказ в заявке владельца, чек, «Оплачено» / «Отклонить», «витрина», ручки сайта."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.settings import get_settings
from app.shop.service import (
    ShopError,
    clean_item_fields,
    item_cta,
    item_visible,
    price_byn_minor,
    price_for_country,
    price_rub_minor,
    prices_out,
    public_item,
    unavailable_reason,
)
from app.telegram.processor import process_core_telegram_update
from app.telegram.shop import (
    CALLBACK_DATA_LIMIT,
    buy_callback,
    card_keyboard,
    card_text,
    parse_shop_start_token,
)
from tests.test_content_access import HOST, content_app  # noqa: F401  (fixture)

OWNER = 688931415
KARINA = 2101187096
BUYER = 70007
FORUM = -1009876543210
TICKET = "33333333-3333-3333-3333-333333333333"
ORDER = "44444444-4444-4444-4444-444444444444"
ORDER_TOKEN = ORDER.replace("-", "")


def _item(**over) -> dict:
    base = {
        "code": "preza-vozrazheniya", "kind": "digital", "category": "materials",
        "title": "Презентация «Мастерство работы с возражениями»", "subtitle": "PDF, 21 слайд",
        "description_md": "Презентация в **PDF**: 21 слайд.", "description_html": "",
        "price_wusd_minor": 500, "price_rub_minor": 50000, "price_byn_minor": None, "price_text": None,
        "cover_media_id": None, "course_slug": None, "file_media_id": "55555555-5555-5555-5555-555555555555",
        "external_url": None, "partner_share_wusd_minor": 0, "confirmer": "owner", "status": "pilot",
        "sort_order": 60, "requisites_note": None, "delivery_note": None,
    }
    return {**base, **over}


COURSE = _item(code="kurs-vozrazheniya", kind="course", category="courses", title="Курс «Мастерство работы с возражениями»",
               subtitle="Курс в Академии WWC", price_wusd_minor=2500, price_rub_minor=250000, course_slug="vozrazheniya",
               file_media_id=None)
SERVICE = _item(code="snyat-blok", kind="service", category="services", title="Сессия «Снять блок»",
                subtitle="1,5 часа онлайн", price_wusd_minor=10000, price_rub_minor=1000000, file_media_id=None,
                delivery_note="Напишите здесь 2–3 удобных времени для встречи.")
GEMINI = _item(code="gemini", kind="external", category="tools", title="Gemini Pro — лицензия", status="published",
               price_wusd_minor=3990, price_rub_minor=399000, external_url="?start=gemini", file_media_id=None,
               price_text="6 мес — 3 990 ₽ · 18 мес — 4 490 ₽", confirmer="services_admin")


def _order(**over) -> dict:
    base = {
        "order_id": ORDER, "item_code": "preza-vozrazheniya", "item_title": "Презентация «Мастерство работы с возражениями»",
        "telegram_user_id": BUYER, "ticket_id": TICKET, "partner_ref_code": "olga-samtsova", "partner_ref_source": "link",
        "country_code": "RU", "currency": "RUB", "amount_minor": 50000, "status": "new", "created": True,
    }
    return {**base, **over}


def _ticket(**over) -> dict:
    base = {
        "ticket_id": TICKET, "ticket_no": 41, "channel_code": "shop", "offer_code": "preza-vozrazheniya",
        "offer_title": "Презентация «Мастерство работы с возражениями»", "user_telegram_user_id": BUYER,
        "user_chat_id": BUYER, "user_display": "Инна (@inna)", "admin_telegram_user_id": OWNER, "status": "open",
        "forum_chat_id": None, "forum_thread_id": None, "created": True,
    }
    return {**base, **over}


@pytest.fixture
def shop_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.setenv("PLATFORM_SUPPORT_ADMIN_TELEGRAM_ID", str(KARINA))
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", "")
    monkeypatch.setenv("PLATFORM_ACADEMY_SITE_BASE", "https://wwc.best")
    get_settings.cache_clear()
    with patch("app.telegram.processor._link_partner_chat", AsyncMock()), patch(
        "app.telegram.processor.first_start_consent_notice", AsyncMock(return_value=None)
    ), patch("app.telegram.processor.offer_marketing_consent", AsyncMock(return_value=False)), patch(
        "app.telegram.shop.answer_callback_query", AsyncMock()
    ):
        yield
    get_settings.cache_clear()


def _private(text: str = "", *, user: int = BUYER, photo: bool = False, message_id: int = 500) -> dict:
    message = {"message_id": message_id, "chat": {"id": user, "type": "private"}, "from": {"id": user, "first_name": "Инна", "username": "inna"}}
    if text:
        message["text"] = text
    if photo:
        message["photo"] = [{"file_id": "receipt-file", "width": 10, "height": 10}]
    return {"message": message}


def _callback(data: str, *, user: int = BUYER, chat: int | None = None, group: bool = False, thread: int | None = None) -> dict:
    chat_id = chat if chat is not None else user
    message = {"message_id": 7, "chat": {"id": chat_id, "type": "supergroup" if group else "private"}}
    if thread is not None:
        message.update({"is_topic_message": True, "message_thread_id": thread})
    return {"callback_query": {"id": "cb", "data": data, "from": {"id": user, "first_name": "Инна", "username": "inna"}, "message": message}}


# ---- catalog and prices ----------------------------------------------------------------


def test_prices_fall_back_to_wusd_and_byn_is_three_and_a_half():
    item = _item(price_rub_minor=None, price_byn_minor=None, price_wusd_minor=500)
    assert price_rub_minor(item) == 50000 and price_byn_minor(item) == 1750
    assert prices_out(item) == {"wusd": 5, "rub": 500, "byn": 17.5}
    assert prices_out(_item(price_wusd_minor=10000, price_rub_minor=1000000)) == {"wusd": 100, "rub": 10000, "byn": 350}
    assert price_byn_minor(_item(price_byn_minor=36000)) == 36000  # своя цена в BYN важнее расчёта


def test_price_by_country_russia_in_roubles_belarus_in_wwc():
    assert price_for_country(_item(), "RU") == ("RUB", 50000)
    assert price_for_country(_item(), "by") == ("WUSD", 500)
    with pytest.raises(ShopError):
        price_for_country(_item(), "KZ")


def test_pilot_is_for_preview_admins_only_and_drafts_for_nobody():
    assert item_visible(_item(status="published"), admin=False)
    assert not item_visible(_item(status="pilot"), admin=False)
    assert item_visible(_item(status="pilot"), admin=True)
    assert not item_visible(_item(status="draft"), admin=True)
    assert not item_visible(_item(status="archived"), admin=True)
    assert not item_visible(None, admin=True)


def test_cta_goes_to_the_bot_and_gemini_keeps_its_own_flow():
    assert item_cta(_item(), "WHIEDA_Advisor_bot") == {
        "kind": "bot", "start": "shop_preza-vozrazheniya", "url": "https://t.me/WHIEDA_Advisor_bot?start=shop_preza-vozrazheniya",
    }
    assert item_cta(GEMINI, "WHIEDA_Advisor_bot") == {"kind": "bot", "start": "gemini", "url": "https://t.me/WHIEDA_Advisor_bot?start=gemini"}
    assert item_cta(_item(kind="external", external_url="https://example.com/x"), None) == {
        "kind": "link", "start": None, "url": "https://example.com/x",
    }
    assert item_cta(_item(), None)["url"] is None  # без имени бота — только токен, ссылку соберёт сайт


def test_public_card_has_no_partner_share_and_renders_markdown():
    card = public_item(_item(), cover_url=None, bot_username="b")
    assert "partner_share_wusd" not in card and not any("share" in key for key in card)
    assert card["description_html"] == "<p>Презентация в <strong>PDF</strong>: 21 слайд.</p>"
    assert card["prices"] == {"wusd": 5, "rub": 500, "byn": 17.5} and card["available"] is True
    assert public_item(_item(kind="course", course_slug=None), cover_url=None, bot_username="b")["available"] is False
    assert public_item(GEMINI, cover_url=None, bot_username="b")["price_text"] == "6 мес — 3 990 ₽ · 18 мес — 4 490 ₽"


def test_course_without_slug_is_not_for_sale():
    assert unavailable_reason(_item(kind="course", course_slug=None)) == "course_not_ready"
    assert unavailable_reason(COURSE) is None and unavailable_reason(GEMINI) == "external"


def test_admin_fields_are_validated_and_markdown_is_rendered():
    fields = clean_item_fields(
        {"price_wusd": 7.5, "price_rub": None, "status": "published", "description_md": "# Заголовок", "title": " Новое "},
        kind="digital",
    )
    assert fields["price_wusd_minor"] == 750 and fields["price_rub_minor"] is None
    assert fields["status"] == "published" and fields["title"] == "Новое"
    assert fields["description_html"] == "<h1>Заголовок</h1>"
    for body, kind, code in (
        ({"status": "live"}, "digital", "bad_status"),
        ({"partner_share_wusd": 1}, "digital", "unknown_fields"),
        ({"course_slug": "kurs"}, "digital", "course_slug_only_for_course"),
        ({"file_media_id": "55555555-5555-5555-5555-555555555555"}, "course", "file_only_for_digital"),
        ({"price_wusd": -1}, "digital", "bad_price_wusd"),
        ({"title": ""}, "digital", "title_required"),
        ({"external_url": "javascript:alert(1)"}, "external", "bad_external_url"),
    ):
        with pytest.raises(ShopError) as caught:
            clean_item_fields(body, kind=kind)
        assert caught.value.code == code


# ---- the start link and the card --------------------------------------------------------


def test_shop_start_token_with_and_without_ref():
    assert parse_shop_start_token("shop_preza-vozrazheniya") == ("preza-vozrazheniya", None)
    assert parse_shop_start_token("shop_preza-vozrazheniya_olga-samtsova") == ("preza-vozrazheniya", "olga-samtsova")
    assert parse_shop_start_token("SHOP_Lending_NNM") == ("lending", "nnm")
    assert parse_shop_start_token("shop_bad code") == ("", None)
    assert parse_shop_start_token("course_abc") is None and parse_shop_start_token("gemini") is None


def test_buy_callback_keeps_the_ref_while_it_fits_telegram_limit():
    assert buy_callback("lending", "RU", "nnm") == "shop:buy:lending:RU:nnm"
    assert buy_callback("lending", "BY", None) == "shop:buy:lending:BY"
    long_ref = "a" * 40
    data = buy_callback("preza-vozrazheniya", "RU", long_ref)
    assert data == "shop:buy:preza-vozrazheniya:RU" and len(data.encode()) <= CALLBACK_DATA_LIMIT


def test_card_asks_the_country_and_shows_both_prices():
    text = card_text(_item())
    assert "Цена: 500 ₽ или 5 WWC$." in text and "Откуда будете оплачивать?" in text
    assert "**" not in text and "Презентация в PDF: 21 слайд." in text
    rows = card_keyboard(_item(), ref="nnm", country=None)["inline_keyboard"]
    assert [b["text"] for row in rows for b in row] == ["Купить — 500 ₽ · Россия", "Купить — 5 WWC$ · Беларусь"]
    assert rows[0][0]["callback_data"] == "shop:buy:preza-vozrazheniya:RU:nnm"
    by_first = card_keyboard(SERVICE, ref=None, country="BY")["inline_keyboard"]
    assert by_first[0][0]["text"] == "Заказать — 100 WWC$ · Беларусь"
    assert card_keyboard(_item(kind="course", course_slug=None), ref=None, country=None) is None


# ---- the bot ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_link_shows_the_card_to_the_owner_in_pilot(whieda_tenant, whieda_bot_binding, shop_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.shop.send_telegram_text", send), patch(
        "app.telegram.shop.get_item", AsyncMock(return_value=_item())
    ), patch("app.telegram.shop.known_country", AsyncMock(return_value=None)):
        result = await process_core_telegram_update(
            whieda_tenant, _private("/start shop_preza-vozrazheniya_olga-samtsova", user=OWNER), "t1", binding=whieda_bot_binding
        )
    assert result["route"] == "shop" and result["status"] == "card"
    sent = send.await_args.kwargs
    assert sent["chat_id"] == str(OWNER) and sent["text"].startswith("Презентация «Мастерство работы с возражениями»")
    buttons = [b["callback_data"] for row in sent["reply_markup"]["inline_keyboard"] for b in row]
    assert buttons == ["shop:buy:preza-vozrazheniya:RU:olga-samtsova", "shop:buy:preza-vozrazheniya:BY:olga-samtsova"]


@pytest.mark.asyncio
async def test_pilot_item_is_not_shown_to_a_stranger(whieda_tenant, whieda_bot_binding, shop_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.shop.send_telegram_text", send), patch("app.telegram.shop.get_item", AsyncMock(return_value=_item())):
        result = await process_core_telegram_update(whieda_tenant, _private("/start shop_preza-vozrazheniya"), "t2", binding=whieda_bot_binding)
    assert result["status"] == "not_available"
    assert "https://wwc.best/masterskaya/" in send.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_gemini_card_in_the_shop_opens_the_gemini_flow(whieda_tenant, whieda_bot_binding, shop_env):
    shown = AsyncMock(return_value={"ok": True, "route": "services", "trace_id": "t3"})
    with patch("app.telegram.shop.get_item", AsyncMock(return_value=GEMINI)), patch("app.telegram.shop.show_services", shown):
        result = await process_core_telegram_update(whieda_tenant, _private("/start shop_gemini"), "t3", binding=whieda_bot_binding)
    assert result["route"] == "services"
    shown.assert_awaited_once()


@pytest.mark.asyncio
async def test_buy_opens_a_ticket_with_the_owner_an_order_and_sends_requisites(whieda_tenant, whieda_bot_binding, shop_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 11})
    support_send = AsyncMock(return_value={"ok": True, "message_id": 12})
    open_ticket = AsyncMock(return_value=_ticket())
    opened = AsyncMock(return_value=_order())
    created_topic = AsyncMock(return_value={"ok": True, "message_thread_id": 901})
    attach = AsyncMock(return_value={"forum_chat_id": FORUM, "forum_thread_id": 901})
    record = AsyncMock(return_value={"duplicate": False})
    with patch("app.telegram.shop.send_telegram_text", send), patch("app.telegram.support.send_telegram_text", support_send), patch(
        "app.telegram.shop.get_item", AsyncMock(return_value=_item(status="published"))
    ), patch("app.telegram.shop.ensure_telegram_actor", AsyncMock(return_value="telegram:whieda:70007")), patch(
        "app.telegram.shop.resolve_partner_ref", AsyncMock(return_value=("olga-samtsova", "link"))
    ) as resolve, patch("app.telegram.shop.open_or_reuse_ticket", open_ticket), patch(
        "app.telegram.support.get_forum", AsyncMock(return_value={"chat_id": FORUM, "binding_id": "whieda-test-binding"})
    ) as forum, patch("app.telegram.support.create_forum_topic", created_topic), patch(
        "app.telegram.support.attach_forum_topic", attach
    ), patch("app.telegram.shop.open_order", opened), patch("app.telegram.shop.record_relayed_message", record):
        result = await process_core_telegram_update(
            whieda_tenant, _callback("shop:buy:preza-vozrazheniya:RU:olga-samtsova"), "t4", binding=whieda_bot_binding
        )
    assert result["status"] == "order_opened" and result["ticket"] == "#S-41"
    assert resolve.await_args.kwargs == {"telegram_user_id": BUYER, "link_ref": "olga-samtsova"}
    ticket_kw = open_ticket.await_args.kwargs
    assert ticket_kw["channel_code"] == "shop" and ticket_kw["admin_telegram_user_id"] == OWNER  # не Карина
    assert forum.await_args.kwargs["kind"] == "site"  # форум владельца, не форум Gemini
    assert created_topic.await_args.kwargs["name"] == "Мастерская · Презентация «Мастерство работы с возражениями» · Инна (@inna)"
    order_kw = opened.await_args.kwargs
    assert order_kw["country_code"] == "RU" and order_kw["ticket_id"] == TICKET
    assert (order_kw["partner_ref_code"], order_kw["partner_ref_source"]) == ("olga-samtsova", "link")
    header = support_send.await_args.kwargs
    assert header["chat_id"] == str(FORUM) and header["message_thread_id"] == 901
    assert "Покупатель: Инна (@inna)" in header["text"] and "Заказ: Презентация «Мастерство работы с возражениями» — 500 ₽ (5 WWC$)" in header["text"]
    assert "Кто привёл: olga-samtsova (по ссылке партнёра)" in header["text"] and "Ждём чек." in header["text"]
    assert "Оплачено" not in str(header["reply_markup"])  # «Оплачено» — только под чеком
    to_buyer = send.await_args.kwargs
    assert to_buyer["chat_id"] == str(BUYER)
    assert "Заказ #S-41: Презентация «Мастерство работы с возражениями» — 500 ₽ (5 WWC$)." in to_buyer["text"]
    assert "Т-Банк" in to_buyer["text"] and "пришлите сюда скриншот чека" in to_buyer["text"]
    assert record.await_args.kwargs["delivered_chat_id"] == FORUM


@pytest.mark.asyncio
async def test_belarus_pays_in_wwc_to_the_belarus_account(whieda_tenant, whieda_bot_binding, shop_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 11})
    order = _order(country_code="BY", currency="WUSD", amount_minor=500, partner_ref_code=None, partner_ref_source=None)
    with patch("app.telegram.shop.send_telegram_text", send), patch("app.telegram.support.send_telegram_text", AsyncMock(return_value={"ok": True, "message_id": 2})), patch(
        "app.telegram.shop.get_item", AsyncMock(return_value=_item(status="published"))
    ), patch("app.telegram.shop.ensure_telegram_actor", AsyncMock(return_value="a")), patch(
        "app.telegram.shop.resolve_partner_ref", AsyncMock(return_value=(None, None))
    ), patch("app.telegram.shop.open_or_reuse_ticket", AsyncMock(return_value=_ticket(created=False))), patch(
        "app.telegram.shop.open_order", AsyncMock(return_value=order)
    ), patch("app.telegram.shop.record_relayed_message", AsyncMock()):
        result = await process_core_telegram_update(whieda_tenant, _callback("shop:buy:preza-vozrazheniya:BY"), "t5", binding=whieda_bot_binding)
    assert result["status"] == "order_opened"
    text = send.await_args.kwargs["text"]
    assert "5 WWC$ (500 ₽)" in text and "SUNRAYSWORD" in text


@pytest.mark.asyncio
async def test_receipt_goes_to_the_owner_with_paid_and_reject_buttons(whieda_tenant, whieda_bot_binding, shop_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 21})
    copy = AsyncMock(return_value={"ok": True, "message_id": 20})
    relay = AsyncMock()
    ticket = _ticket(forum_chat_id=FORUM, forum_thread_id=901)
    with patch("app.telegram.shop.send_telegram_text", send), patch("app.telegram.shop.copy_telegram_message", copy), patch(
        "app.telegram.shop.take_receipt", AsyncMock(return_value=[_order(status="receipt")])
    ) as take, patch("app.telegram.shop.get_ticket", AsyncMock(return_value=ticket)), patch(
        "app.telegram.shop.record_relayed_message", AsyncMock()
    ), patch("app.telegram.processor.try_handle_support_message", relay):
        result = await process_core_telegram_update(whieda_tenant, _private(photo=True), "t6", binding=whieda_bot_binding)
    assert result["route"] == "shop" and result["status"] == "receipt"
    assert take.await_args.kwargs == {"telegram_user_id": BUYER, "file_id": "receipt-file"}
    relay.assert_not_called()  # чек не уходит в заявку обычным вложением
    assert copy.await_args.kwargs["chat_id"] == str(FORUM) and copy.await_args.kwargs["message_thread_id"] == 901
    to_owner = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(FORUM)][0]
    assert "Чек по заказу: Презентация «Мастерство работы с возражениями» — 500 ₽" in to_owner["text"]
    buttons = to_owner["reply_markup"]["inline_keyboard"][0]
    assert buttons == [
        {"text": "Оплачено 500 ₽", "callback_data": f"shop:paid:{ORDER_TOKEN}"},
        {"text": "Отклонить", "callback_data": f"shop:reject:{ORDER_TOKEN}"},
    ]
    to_buyer = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(BUYER)][0]
    assert to_buyer["text"].startswith("Чек получен.")


@pytest.mark.asyncio
async def test_photo_without_a_waiting_order_is_left_to_the_tunnel(whieda_tenant, whieda_bot_binding, shop_env):
    relay = AsyncMock(return_value={"ok": True, "route": "support_relay"})
    with patch("app.telegram.shop.take_receipt", AsyncMock(return_value=[])), patch("app.telegram.processor.try_handle_support_message", relay):
        result = await process_core_telegram_update(whieda_tenant, _private(photo=True), "t7", binding=whieda_bot_binding)
    assert result["route"] == "support_relay"


@pytest.mark.asyncio
async def test_paid_in_the_owners_forum_opens_the_file_and_says_where(whieda_tenant, whieda_bot_binding, shop_env):
    from app.telegram.support import is_support_forum_traffic

    update = _callback(f"shop:paid:{ORDER_TOKEN}", user=OWNER, chat=FORUM, group=True, thread=901)
    assert is_support_forum_traffic(update)
    send = AsyncMock(return_value={"ok": True, "message_id": 31})
    result_value = {"order": _order(status="delivered"), "item": _item(), "idempotent": False, "delivered": True}
    with patch("app.telegram.shop.send_telegram_text", send), patch(
        "app.telegram.shop.confirm_order", AsyncMock(return_value=result_value)
    ) as confirm, patch("app.telegram.shop.get_ticket", AsyncMock(return_value=_ticket(forum_chat_id=FORUM, forum_thread_id=901))):
        result = await process_core_telegram_update(whieda_tenant, update, "t8", binding=whieda_bot_binding)
    assert result["status"] == "paid" and result["delivered"] is True
    assert confirm.await_args.kwargs == {"order_id": ORDER, "paid_by": OWNER}
    to_buyer = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(BUYER)][0]
    assert "Файл — в личном кабинете, раздел «Покупки»." in to_buyer["text"]
    assert to_buyer["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://wwc.best/me/#purchases"
    in_topic = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(FORUM)][0]
    assert in_topic["message_thread_id"] == 901 and in_topic["text"].startswith("Оплачено: Презентация")


@pytest.mark.asyncio
async def test_paid_course_links_to_the_academy_and_a_missing_course_warns_the_owner(whieda_tenant, whieda_bot_binding, shop_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 31})
    course_order = _order(item_code="kurs-vozrazheniya", item_title=COURSE["title"], amount_minor=250000)
    delivered = {"order": course_order, "item": COURSE, "idempotent": False, "delivered": True}
    missing = {"order": course_order, "item": COURSE, "idempotent": False, "delivered": False}
    with patch("app.telegram.shop.send_telegram_text", send), patch(
        "app.telegram.shop.confirm_order", AsyncMock(side_effect=[delivered, missing])
    ), patch("app.telegram.shop.get_ticket", AsyncMock(return_value=_ticket())):
        await process_core_telegram_update(whieda_tenant, _callback(f"shop:paid:{ORDER_TOKEN}", user=OWNER), "t9", binding=whieda_bot_binding)
        await process_core_telegram_update(whieda_tenant, _callback(f"shop:paid:{ORDER_TOKEN}", user=OWNER), "t10", binding=whieda_bot_binding)
    to_buyer = [c.kwargs for c in send.await_args_list if c.kwargs["chat_id"] == str(BUYER)]
    assert to_buyer[0]["reply_markup"]["inline_keyboard"][0][0]["url"] == "https://wwc.best/academy/?course=vozrazheniya"
    assert "Доступ к курсу откроем отдельно" in to_buyer[1]["text"]
    to_owner = [c.kwargs["text"] for c in send.await_args_list if c.kwargs["chat_id"] == str(OWNER)]
    assert "Курс открыт покупателю." in to_owner[0] and "⚠️ Курс не найден в Академии" in to_owner[1]


@pytest.mark.asyncio
async def test_paid_service_sends_the_delivery_note_and_keeps_the_ticket(whieda_tenant, whieda_bot_binding, shop_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 31})
    order = _order(item_code="snyat-blok", item_title=SERVICE["title"], amount_minor=1000000, status="paid")
    with patch("app.telegram.shop.send_telegram_text", send), patch(
        "app.telegram.shop.confirm_order", AsyncMock(return_value={"order": order, "item": SERVICE, "idempotent": False, "delivered": False})
    ), patch("app.telegram.shop.get_ticket", AsyncMock(return_value=_ticket())), patch(
        "app.telegram.support._close_ticket_everywhere", AsyncMock()
    ) as close:
        result = await process_core_telegram_update(whieda_tenant, _callback(f"shop:paid:{ORDER_TOKEN}", user=OWNER), "t11", binding=whieda_bot_binding)
    assert result["status"] == "paid"
    close.assert_not_awaited()
    to_buyer = [c.kwargs["text"] for c in send.await_args_list if c.kwargs["chat_id"] == str(BUYER)][0]
    assert to_buyer == "Оплата подтверждена: Сессия «Снять блок».\nНапишите здесь 2–3 удобных времени для встречи."


@pytest.mark.asyncio
async def test_only_the_owner_confirms_a_shop_payment(whieda_tenant, whieda_bot_binding, shop_env):
    confirm = AsyncMock()
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.shop.confirm_order", confirm), patch("app.telegram.shop.send_telegram_text", send):
        result = await process_core_telegram_update(
            whieda_tenant, _callback(f"shop:paid:{ORDER_TOKEN}", user=KARINA, chat=FORUM, group=True, thread=901), "t12", binding=whieda_bot_binding
        )
    assert result["status"] == "forbidden"
    confirm.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_paid_press_is_idempotent_and_reject_tells_the_buyer(whieda_tenant, whieda_bot_binding, shop_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.shop.send_telegram_text", send), patch(
        "app.telegram.shop.confirm_order", AsyncMock(return_value={"order": _order(status="delivered"), "item": _item(), "idempotent": True, "delivered": True})
    ), patch("app.telegram.shop.reject_order", AsyncMock(return_value={"order": _order(status="cancelled"), "idempotent": False})), patch(
        "app.telegram.shop.get_ticket", AsyncMock(return_value=_ticket())
    ):
        again = await process_core_telegram_update(whieda_tenant, _callback(f"shop:paid:{ORDER_TOKEN}", user=OWNER), "t13", binding=whieda_bot_binding)
        rejected = await process_core_telegram_update(whieda_tenant, _callback(f"shop:reject:{ORDER_TOKEN}", user=OWNER), "t14", binding=whieda_bot_binding)
    assert again["status"] == "already_decided" and rejected["status"] == "rejected"
    texts = [(c.kwargs["chat_id"], c.kwargs["text"]) for c in send.await_args_list]
    assert (str(OWNER), "Заказ «Презентация «Мастерство работы с возражениями»» уже оплачен, доступ открыт.") in texts
    assert any(chat == str(BUYER) and text.startswith("Оплату по заказу") for chat, text in texts)
    assert not any(chat == str(BUYER) and "подтверждена" in text for chat, text in texts)


@pytest.mark.asyncio
async def test_showcase_command_lists_and_switches_statuses_for_the_owner_only(whieda_tenant, whieda_bot_binding, shop_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.shop.send_telegram_text", send), patch(
        "app.telegram.shop.list_items", AsyncMock(return_value=[_item(), GEMINI])
    ), patch("app.telegram.shop.set_item_status", AsyncMock(return_value=_item(status="published"))) as set_status:
        listed = await process_core_telegram_update(whieda_tenant, _private("витрина", user=OWNER), "t15", binding=whieda_bot_binding)
        switched = await process_core_telegram_update(
            whieda_tenant, _private("витрина preza-vozrazheniya published", user=OWNER), "t16", binding=whieda_bot_binding
        )
    assert listed["status"] == "listed" and switched["status"] == "updated"
    assert "preza-vozrazheniya · pilot · Презентация" in send.await_args_list[0].kwargs["text"]
    assert set_status.await_args.args == ("whieda", "preza-vozrazheniya", "published")
    assert send.await_args_list[1].kwargs["text"] == "preza-vozrazheniya: published."


@pytest.mark.asyncio
async def test_showcase_word_from_a_stranger_is_not_a_command(whieda_tenant, whieda_bot_binding, shop_env):
    from app.telegram.shop import try_handle_shop_message
    from app.telegram.update_parser import parse_telegram_message

    msg = parse_telegram_message(_private("витрина"))
    assert await try_handle_shop_message(whieda_tenant, msg, trace_id="t17") is None


# ---- site API ----------------------------------------------------------------------------


async def _call(app, method, path, **kw):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, path, headers={**HOST, **kw.pop("headers", {})}, **kw)


@pytest.mark.asyncio
async def test_public_shop_is_cached_for_guests_and_private_for_a_session(content_app):  # noqa: F811
    catalog = AsyncMock(return_value={"ok": True, "preview": False, "items": []})
    with patch("app.shop.routes.public_catalog", catalog):
        guest = await _call(content_app, "GET", "/api/v1/public/shop")
        staging = await _call(content_app, "GET", "/v1/public/shop")
    assert guest.status_code == 200 and guest.json() == {"ok": True, "preview": False, "items": []}
    assert guest.headers["cache-control"] == "public, max-age=60" and staging.status_code == 200
    assert catalog.await_args_list[0].kwargs == {"viewer_id": None}
    session = {"session_id": "s", "tenant_id": "whieda", "telegram_user_id": OWNER,
               "expires_at": datetime.now(timezone.utc) + timedelta(days=1)}
    with patch("app.shop.routes.public_catalog", catalog), patch("app.shop.routes.read_session_cookie", return_value="raw"), patch(
        "app.shop.routes.validate_content_session", AsyncMock(return_value=session)
    ):
        signed = await _call(content_app, "GET", "/api/v1/public/shop")
    assert signed.headers["cache-control"] == "private, no-store"
    assert catalog.await_args.kwargs == {"viewer_id": OWNER}


@pytest.mark.asyncio
async def test_purchases_files_and_admin_need_a_session_and_admin_needs_the_owner(content_app, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", "")
    get_settings.cache_clear()
    assert (await _call(content_app, "GET", "/api/v1/content-access/me/purchases")).status_code == 401
    buyer = {"session_id": "s", "tenant_id": "whieda", "telegram_user_id": BUYER, "expires_at": datetime.now(timezone.utc) + timedelta(days=1)}
    owner = {**buyer, "telegram_user_id": OWNER}
    purchases = {"ok": True, "orders": [], "access": []}
    with patch("app.content_access.routes.read_session_cookie", return_value="raw"), patch(
        "app.content_access.routes.validate_content_session", AsyncMock(return_value=buyer)
    ), patch("app.shop.routes.list_purchases", AsyncMock(return_value=purchases)) as listed, patch(
        "app.shop.routes.file_link", AsyncMock(side_effect=ShopError(403, "purchase_required"))
    ):
        mine = await _call(content_app, "GET", "/api/v1/content-access/me/purchases")
        file = await _call(content_app, "GET", "/api/v1/content-access/shop/files/preza-vozrazheniya")
        admin = await _call(content_app, "GET", "/api/v1/content-access/shop/admin/items")
    assert mine.status_code == 200 and mine.json() == purchases and listed.await_args.args == ("whieda", BUYER)
    assert mine.headers["cache-control"] == "private, no-store"
    assert file.status_code == 403 and file.json()["error"] == "purchase_required"
    assert admin.status_code == 403 and admin.json()["error"] == "shop_admin_required"
    updated = _item(status="published")
    with patch("app.content_access.routes.read_session_cookie", return_value="raw"), patch(
        "app.content_access.routes.validate_content_session", AsyncMock(return_value=owner)
    ), patch("app.shop.routes.update_item", AsyncMock(return_value=updated)) as update, patch(
        "app.shop.routes.admin_item_out", AsyncMock(return_value={"code": "preza-vozrazheniya", "status": "published"})
    ):
        patched = await _call(
            content_app, "PATCH", "/api/v1/content-access/shop/admin/items/preza-vozrazheniya",
            json={"status": "published", "price_rub": 600},
        )
    assert patched.status_code == 200 and patched.json()["item"]["status"] == "published"
    assert update.await_args.args == ("whieda", "preza-vozrazheniya", {"status": "published", "price_rub": 600})
    assert update.await_args.kwargs == {"updated_by": OWNER}
    get_settings.cache_clear()
