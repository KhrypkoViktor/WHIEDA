"""Checkout comment: missing price stays 'цена не указана', never 0."""

from app.cart.pricing import currency_for_market, format_checkout_comment, money_value


def test_checkout_comment_uses_null_price_text():
    text = format_checkout_comment(
        {
            "price_mode": "primary",
            "currency_code": "BYN",
            "items": [
                {
                    "canonical_name": "Пояс",
                    "qty": 1,
                    "line_amount": None,
                    "line_pv": 0,
                }
            ],
            "totals": {"amount": 0, "pv": 0},
        }
    )
    assert "цена не указана" in text
    assert "0 BYN" not in text.split("Пояс")[1].split("\n")[0]


def test_checkout_comment_lists_priced_lines():
    text = format_checkout_comment(
        {
            "price_mode": "repeat",
            "currency_code": "BYN",
            "items": [
                {
                    "canonical_name": "Активатор клеток",
                    "qty": 2,
                    "line_amount": 2100,
                    "line_pv": 600,
                }
            ],
            "totals": {"amount": 2100, "pv": 600},
        }
    )
    assert "повторная" in text
    assert "Активатор клеток × 2 — 2100 BYN" in text
    assert "Итого: 2100 BYN, 600 PV" in text


def test_money_value_never_coerces_missing_or_zero():
    assert money_value(None) is None
    assert money_value("") is None
    assert money_value(0) is None
    assert money_value(-10) is None
    assert money_value("abc") is None
    assert money_value(1750) == 1750.0


def test_currency_for_market():
    assert currency_for_market("by") == "BYN"
    assert currency_for_market("ru") == "RUB"
    assert currency_for_market("kz") == "RUB"


def test_checkout_comment_rub_repeat():
    text = format_checkout_comment(
        {
            "price_mode": "primary",
            "currency_code": "RUB",
            "items": [
                {
                    "canonical_name": "Активатор клеток",
                    "qty": 1,
                    "line_amount": 50000,
                    "line_pv": 300,
                }
            ],
            "totals": {"amount": 50000, "pv": 300},
        }
    )
    assert "первичная" in text
    assert "50000 RUB" in text
    assert "Итого: 50000 RUB, 300 PV" in text
