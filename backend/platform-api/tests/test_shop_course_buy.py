"""Курс продаётся одной кнопкой «Купить» всем (владелец, 06.10.2026): карточка Мастерской
у платного курса Академии, клубная цена 75 WWC$ для участника клуба, «Купить курс» на замке."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.academy.service import AcademyError
from app.shop.service import ShopError, clean_item_fields, price_for_country, priced_for, prices_out
from app.telegram.academy import offer_lock_text, purchase_lock_text
from app.telegram.processor import process_core_telegram_update
from app.telegram.shop import card_keyboard, card_text
from tests.test_shop import BUYER, TICKET, _callback, _item, _order, _ticket, shop_env  # noqa: F401  (fixture)

ONLINE = _item(
    code="kurs-online-start", kind="course", category="courses", title="Курс «Онлайн-старт: 4 недели практики»",
    subtitle="Практикум с Виктором", description_md="Четыре недели живых занятий.", status="published",
    price_wusd_minor=10000, price_rub_minor=1000000, price_club_wusd_minor=7500, course_slug="online-start-4w",
    file_media_id=None,
)
OFFER = {"code": "kurs-online-start", "start": "shop_kurs-online-start",
         "url": "https://t.me/WHIEDA_Advisor_bot?start=shop_kurs-online-start", "prices": prices_out(ONLINE)}


# ---- prices -----------------------------------------------------------------------------


def test_club_price_is_used_only_for_a_club_member_and_roubles_follow_it():
    assert price_for_country(ONLINE, "RU") == ("RUB", 1000000)
    assert price_for_country(ONLINE, "BY") == ("RUB", 1000000)  # только рубли (09.10.2026)
    assert price_for_country(ONLINE, "RU", club=True) == ("RUB", 750000)
    assert price_for_country(ONLINE, "BY", club=True) == ("RUB", 750000)
    plain = _item(price_club_wusd_minor=None)
    assert price_for_country(plain, "RU", club=True) == price_for_country(plain, "RU")
    assert priced_for(plain, club=True) is plain


def test_site_sees_the_club_price_next_to_the_regular_one():
    assert prices_out(ONLINE) == {"wusd": 100, "rub": 10000, "byn": 350, "club_wusd": 75, "club_rub": 7500}
    assert "club_wusd" not in prices_out(_item())


def test_owner_can_set_and_clear_the_club_price():
    assert clean_item_fields({"price_club_wusd": 75}, kind="course") == {"price_club_wusd_minor": 7500}
    assert clean_item_fields({"price_club_wusd": None}, kind="course") == {"price_club_wusd_minor": None}
    with pytest.raises(ShopError):
        clean_item_fields({"price_club_wusd": -1}, kind="course")


def test_v22_has_no_dollar_sign_and_seeds_the_club_price_in_the_insert():
    sql = (Path(__file__).resolve().parents[3] / "postgres/sql/platform_shop_v22.sql").read_text(encoding="utf-8")
    assert "$" not in sql  # production applies SQL through n8n, which eats dollar signs
    assert "update shop_items" not in sql  # a rerun must not bring back a club price the owner removed


def test_club_price_above_the_regular_one_is_refused():
    from app.shop.service import _check_club_price

    _check_club_price(ONLINE)
    for over in ({"price_club_wusd_minor": 10001}, {"price_rub_minor": 700000}):
        with pytest.raises(ShopError) as exc:
            _check_club_price({**ONLINE, **over})
        assert exc.value.code == "club_price_above_price"
    _check_club_price({**ONLINE, "price_club_wusd_minor": None})


# ---- the card ---------------------------------------------------------------------------


def test_card_tells_everyone_about_the_club_price_and_a_member_gets_it():
    text = card_text(ONLINE)
    assert "Цена: 10 000 ₽." in text and "Участникам клуба — 7 500 ₽." in text and "WWC$" not in text
    rows = card_keyboard(ONLINE, ref=None, country=None)["inline_keyboard"]
    # Одна кнопка — рубли на карту Т-Банка для всех (владелец, 09.10.2026).
    assert [b["text"] for row in rows for b in row] == ["Купить — 10 000 ₽"]

    member = card_text(ONLINE, club=True)
    assert "Цена для вас как участника клуба: 7 500 ₽ (обычная — 10 000 ₽)." in member
    rows = card_keyboard(ONLINE, ref=None, country="BY", club=True)["inline_keyboard"]
    assert [b["text"] for row in rows for b in row] == ["Купить — 7 500 ₽"]
    assert rows[0][0]["callback_data"] == "shop:buy:kurs-online-start:RU"


@pytest.mark.asyncio
@pytest.mark.parametrize("member", [False, True])
async def test_buy_course_button_opens_the_card_with_the_buyers_price(whieda_tenant, whieda_bot_binding, shop_env, member):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.shop.send_telegram_text", send), patch(
        "app.telegram.shop.get_item", AsyncMock(return_value=ONLINE)
    ), patch("app.telegram.shop.known_country", AsyncMock(return_value=None)), patch(
        "app.telegram.shop.is_club_member", AsyncMock(return_value=member)
    ) as club:
        result = await process_core_telegram_update(whieda_tenant, _callback("shop:card:kurs-online-start"), "c1", binding=whieda_bot_binding)
    assert result["route"] == "shop" and result["status"] == "card"
    club.assert_awaited_once_with("whieda", BUYER)
    sent = send.await_args.kwargs
    first = sent["reply_markup"]["inline_keyboard"][0][0]["text"]
    assert first == ("Купить — 7 500 ₽" if member else "Купить — 10 000 ₽")


@pytest.mark.asyncio
async def test_card_button_for_a_hidden_item_says_it_is_not_available(whieda_tenant, whieda_bot_binding, shop_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 1})
    with patch("app.telegram.shop.send_telegram_text", send), patch(
        "app.telegram.shop.get_item", AsyncMock(return_value={**ONLINE, "status": "draft"})
    ):
        result = await process_core_telegram_update(whieda_tenant, _callback("shop:card:kurs-online-start"), "c2", binding=whieda_bot_binding)
    assert result["status"] == "not_available"


@pytest.mark.asyncio
async def test_a_club_member_orders_at_the_club_price(whieda_tenant, whieda_bot_binding, shop_env):
    send = AsyncMock(return_value={"ok": True, "message_id": 11})
    opened = AsyncMock(return_value=_order(item_code="kurs-online-start", item_title=ONLINE["title"], amount_minor=750000))
    owner = AsyncMock(return_value={"ok": True, "message_id": 12})
    with patch("app.telegram.shop.send_telegram_text", send), patch("app.telegram.support.send_telegram_text", owner), patch(
        "app.telegram.shop.get_item", AsyncMock(return_value=ONLINE)
    ), patch("app.telegram.shop.ensure_telegram_actor", AsyncMock(return_value="telegram:whieda:70007")), patch(
        "app.telegram.shop.resolve_partner_ref", AsyncMock(return_value=(None, None))
    ), patch("app.telegram.shop.open_or_reuse_ticket", AsyncMock(return_value=_ticket(created=False))), patch(
        "app.telegram.shop.open_order", opened
    ), patch("app.telegram.shop.record_relayed_message", AsyncMock(return_value={"duplicate": False})), patch(
        "app.telegram.shop.is_club_member", AsyncMock(return_value=True)
    ):
        result = await process_core_telegram_update(whieda_tenant, _callback("shop:buy:kurs-online-start:RU"), "c3", binding=whieda_bot_binding)
    assert result["status"] == "order_opened"
    assert opened.await_args.kwargs["club"] is True and opened.await_args.kwargs["ticket_id"] == TICKET
    assert "7 500 ₽" in send.await_args.kwargs["text"]
    assert any("клубная цена" in str(call.kwargs.get("text")) for call in owner.await_args_list)


# ---- the Academy lock in the bot ---------------------------------------------------------


def test_offer_lock_text_names_the_price_and_what_happens_after_buy():
    text = offer_lock_text("Онлайн-старт: 4 недели практики", OFFER["prices"], club=False)
    assert text.startswith("«Онлайн-старт: 4 недели практики» — платный курс.")
    assert "Цена: 10 000 ₽." in text and "Участникам клуба — 7 500 ₽." in text and "WWC$" not in text
    assert "пришлёте сюда чек" in text and "ключ" not in text
    member = offer_lock_text("Онлайн-старт: 4 недели практики", OFFER["prices"], club=True)
    assert "Цена для вас как участника клуба: 7 500 ₽ (обычная — 10 000 ₽)." in member


@pytest.mark.asyncio
async def test_locked_course_with_a_shop_card_shows_buy_course(whieda_tenant, whieda_bot_binding, shop_env):
    send = AsyncMock(return_value={"ok": True})
    locked = AsyncMock(side_effect=AcademyError(403, "purchase_required", {"author_contact": {"telegram": "SunRaySword"}}))
    with patch("app.telegram.academy.send_telegram_text", send), patch("app.telegram.academy.answer_callback_query", AsyncMock()), patch(
        "app.telegram.academy.load_viewer", AsyncMock()
    ), patch("app.telegram.academy._show_course", locked), patch(
        "app.telegram.academy._course_brief", AsyncMock(return_value={"slug": "online-start-4w", "title": "Онлайн-старт: 4 недели практики"})
    ), patch("app.telegram.academy._course_offer", AsyncMock(return_value=OFFER)), patch(
        "app.telegram.academy.is_club_member", AsyncMock(return_value=False)
    ):
        result = await process_core_telegram_update(whieda_tenant, _callback("acad:c:online-start-4w"), "a1", binding=whieda_bot_binding)
    assert result["status"] == "purchase_required"
    sent = send.await_args.kwargs
    assert sent["text"].startswith("«Онлайн-старт: 4 недели практики» — платный курс.")
    assert sent["reply_markup"]["inline_keyboard"] == [[{"text": "Купить курс", "callback_data": "shop:card:kurs-online-start"}]]


@pytest.mark.asyncio
async def test_locked_course_without_a_shop_card_keeps_the_authors_key(whieda_tenant, whieda_bot_binding, shop_env):
    send = AsyncMock(return_value={"ok": True})
    contact = {"telegram": "alm_art"}
    locked = AsyncMock(side_effect=AcademyError(403, "purchase_required", {"author_contact": contact}))
    with patch("app.telegram.academy.send_telegram_text", send), patch("app.telegram.academy.answer_callback_query", AsyncMock()), patch(
        "app.telegram.academy.load_viewer", AsyncMock()
    ), patch("app.telegram.academy._show_course", locked), patch(
        "app.telegram.academy._course_brief", AsyncMock(return_value={"slug": "portret", "title": "Портрет"})
    ), patch("app.telegram.academy._course_offer", AsyncMock(return_value=None)):
        await process_core_telegram_update(whieda_tenant, _callback("acad:c:portret"), "a2", binding=whieda_bot_binding)
    assert send.await_args.kwargs["text"] == purchase_lock_text(contact)


@pytest.mark.asyncio
async def test_academy_home_with_one_locked_course_offers_buy(whieda_tenant, whieda_bot_binding, shop_env):
    send = AsyncMock(return_value={"ok": True})
    course = {"slug": "online-start-4w", "title": "Онлайн-старт: 4 недели практики", "locked": True,
              "lock_reason": "purchase_required", "author_contact": {"telegram": "SunRaySword"}, "purchase": OFFER}
    with patch("app.telegram.academy.send_telegram_text", send), patch("app.telegram.academy.answer_callback_query", AsyncMock()), patch(
        "app.telegram.academy.load_viewer", AsyncMock()
    ), patch("app.telegram.academy.academy_visible", lambda viewer: True), patch(
        "app.telegram.academy.list_courses", AsyncMock(return_value=[course])
    ), patch("app.telegram.academy.is_club_member", AsyncMock(return_value=True)):
        await process_core_telegram_update(whieda_tenant, _callback("acad:home"), "a3", binding=whieda_bot_binding)
    sent = send.await_args.kwargs
    assert "Цена для вас как участника клуба" in sent["text"]
    assert sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "shop:card:kurs-online-start"


# ---- другая страна (06.10.2026) ---------------------------------------------------------


def test_old_country_buttons_all_pay_in_roubles():
    from app.telegram.shop import payment_text

    assert price_for_country(ONLINE, "WW") == ("RUB", 1000000)
    assert price_for_country(ONLINE, "WW", club=True) == ("RUB", 750000)
    text = payment_text(ONLINE, _order(country_code="RU", currency="RUB", amount_minor=1000000), _ticket())
    assert "10 000 ₽" in text and "Т-Банк" in text and "SUNRAYSWORD" not in text and "WWC$" not in text