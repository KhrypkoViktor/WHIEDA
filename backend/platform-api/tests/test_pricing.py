"""Owner's multi-line payment command: products, bundle, promo lines, totals."""

from __future__ import annotations

import pytest

from app.subscriptions.pricing import PaymentLine, describe_lines, parse_payment_command, validate_lines
from app.subscriptions.service import SubscriptionError


def test_legacy_one_liner_is_a_single_pro_line():
    parsed = parse_payment_command("оплата ref:olga-samtsova 30 WWC$ 3")
    assert parsed.identifier == "ref:olga-samtsova"
    assert parsed.lines == [PaymentLine("platform_subscription", 3000, "WUSD", 3, False, "")]
    assert parsed.received_minor is None and parsed.currency == "WUSD"


def test_multiline_with_personal_price_promo_and_received():
    parsed = parse_payment_command(
        "Оплата @olga_samtsova\nPRO 15 WWC$ 3\nклуб 75 WWC$ 3 акция\nнастройка сайта 0 WWC$ акция\nполучено 90 WWC$"
    )
    assert parsed.identifier == "@olga_samtsova"
    assert [l.product_code for l in parsed.lines] == ["platform_subscription", "club_subscription", "site_setup"]
    assert parsed.lines[1].promo is True and parsed.lines[1].amount_minor == 7500
    assert parsed.lines[2].access_months == 0
    assert parsed.received_minor == 9000


def test_bundle_line_expands_into_pro_and_promo_club():
    parsed = parse_payment_command("оплата ref:zinaida\nпакет 105 WWC$\nполучено 105 WWC$")
    assert [(l.product_code, l.amount_minor, l.promo) for l in parsed.lines] == [
        ("platform_subscription", 3000, False),
        ("club_subscription", 7500, True),
    ]
    rub = parse_payment_command("оплата ref:zinaida\nпакет 10500 RUB")
    assert [(l.product_code, l.amount_minor) for l in rub.lines] == [("platform_subscription", 300000), ("club_subscription", 750000)]


def test_currencies_and_aliases():
    assert parse_payment_command("оплата ref:a 30 W$ 3").lines[0].currency == "WUSD"
    assert parse_payment_command("оплата ref:a 3000 RUB 3").lines[0].amount_minor == 300000
    assert parse_payment_command("оплата ref:a\nсайт 3000 ₽ 6").lines[0].access_months == 6


def test_mixed_currencies_unknown_product_or_garbage_are_rejected():
    with pytest.raises(SubscriptionError):
        parse_payment_command("оплата ref:a\nPRO 30 WWC$ 3\nклуб 12000 RUB 3")
    with pytest.raises(SubscriptionError):
        parse_payment_command("оплата ref:a\nкурс 10 WWC$")
    with pytest.raises(SubscriptionError):
        parse_payment_command("привет")
    with pytest.raises(SubscriptionError):
        parse_payment_command("оплата ref:a")


def test_validate_flags_off_price_lines_without_promo_and_wrong_total():
    prices = {"platform_subscription": 3000, "club_subscription": 12000, "site_setup": 2000}
    lines = [PaymentLine("platform_subscription", 3000, "WUSD", 3, False, ""), PaymentLine("club_subscription", 7500, "WUSD", 3, False, "")]
    problems = validate_lines(lines, prices, received_minor=10500)
    assert len(problems) == 1 and "CLUB" in problems[0] and "120 WWC$" in problems[0]
    promo = [lines[0], PaymentLine("club_subscription", 7500, "WUSD", 3, True, "акция")]
    assert validate_lines(promo, prices, received_minor=10500) == []
    assert any("не сходится" in p for p in validate_lines(promo, prices, received_minor=9999))
    assert any("не найден" in p for p in validate_lines([PaymentLine("club_subscription", 1, "WUSD", 3, True, "")], {}, None))


def test_describe_lines_reads_like_a_receipt():
    lines = [PaymentLine("platform_subscription", 300000, "RUB", 3, False, ""), PaymentLine("site_setup", 200000, "RUB", 0, False, "")]
    assert describe_lines(lines, 500000) == ["PRO (сайт): 3 000 ₽, 3 мес.", "настройка сайта: 2 000 ₽", "Получено: 5 000 ₽"]
