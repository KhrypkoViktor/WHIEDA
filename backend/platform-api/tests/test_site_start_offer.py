"""«Хочу такой же сайт»: /start site_<код> ведёт на один экран заказа, а не в кабинет."""
from app.referral_bonus.service import parse_referral_start_token, parse_site_start_token
from app.telegram.referral_bonus import site_offer_keyboard, site_offer_text


def test_site_token_is_separate_from_referral_token():
    assert parse_site_start_token("site_igR7fQ2mWx9pLv4K") == "igR7fQ2mWx9pLv4K"
    assert parse_site_start_token("site_bad code") == ""
    assert parse_site_start_token("ref_igR7fQ2mWx9pLv4K") is None
    assert parse_referral_start_token("site_igR7fQ2mWx9pLv4K") is None


def test_site_offer_text_is_the_owner_wording():
    text = site_offer_text("Игорь Ефименко")
    assert text == (
        "Вас пригласил партнёр WWC: Игорь Ефименко. Такой же сайт — за 1 день, 10 W$ в месяц (1000 ₽ / 35 BYN). "
        "20 W$ — разовое подключение."
    )
    assert "Баланс" not in text and "реферальн" not in text
    assert site_offer_text("").startswith("Такой же сайт")


def test_site_offer_keyboard_has_order_first_and_example_host():
    kb = site_offer_keyboard(telegram_user_id=42, example_url="https://igoref.wwc.best/")
    rows = kb["inline_keyboard"]
    assert rows[0][0]["text"] == "Заказать сайт" and "docs.google.com/forms" in rows[0][0]["url"]
    assert rows[1][0]["text"] == "Посмотреть пример: igoref.wwc.best"
    assert len(site_offer_keyboard(telegram_user_id=None, example_url="")["inline_keyboard"]) == 1
