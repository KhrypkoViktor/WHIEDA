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
        parse_payment_command("оплата ref:a\nтренинг 10 WWC$")
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
    assert describe_lines(lines, 400000, bonus_minor=1000)[-1] == "Бонусами: 10 WWC$"


def test_bonus_line_covers_the_gap_between_lines_and_received():
    # Olesya, 18.09.2026: bundle 105 WWC$, 10 000 ₽ received, the missing 5 WWC$ from her bonus balance.
    parsed = parse_payment_command("оплата ref:olesya\nпакет 10500 RUB\nполучено 10000 RUB\nбонусами 5 WWC$")
    assert parsed.bonus_minor == 500 and parsed.received_minor == 1000000 and parsed.currency == "RUB"
    prices = {"platform_subscription": 300000, "club_subscription": 1200000}
    assert validate_lines(parsed.lines, prices, parsed.received_minor, parsed.bonus_minor) == []
    # Same in WWC$: 100 received + 5 bonus = 105.
    w = parse_payment_command("оплата ref:olesya\nпакет 105 WWC$\nполучено 100 WWC$\nбонусами 5 W$")
    assert validate_lines(w.lines, {"platform_subscription": 3000, "club_subscription": 12000}, w.received_minor, w.bonus_minor) == []
    # Bonus that does not close the gap is still a mismatch, and the message names it.
    problems = validate_lines(w.lines, {"platform_subscription": 3000, "club_subscription": 12000}, w.received_minor, 300)
    assert len(problems) == 1 and "бонусами 3 WWC$" in problems[0]
    # Bonus is WWC$ only; zero is not a bonus.
    with pytest.raises(SubscriptionError):
        parse_payment_command("оплата ref:olesya\nпакет 105 WWC$\nбонусами 500 RUB")
    with pytest.raises(SubscriptionError):
        parse_payment_command("оплата ref:olesya\nпакет 105 WWC$\nбонусами 0 WWC$")


def test_bonus_offset_rides_in_the_intent_as_a_marker_not_a_line():
    from app.subscriptions.pricing import BONUS_OFFSET, bonus_from_json, lines_from_json, with_list_prices

    lines = [PaymentLine("platform_subscription", 3000, "WUSD", 3, False, "")]
    stored = with_list_prices(lines, {"platform_subscription": 3000}, bonus_minor=500)
    assert [i["product_code"] for i in stored] == ["platform_subscription", BONUS_OFFSET]
    assert lines_from_json(stored) == lines and bonus_from_json(stored) == 500
    assert with_list_prices(lines, {"platform_subscription": 3000}) == stored[:1] and bonus_from_json(stored[:1]) == 0


def test_course_is_a_one_off_product_line():
    # Курс Академии (100 WWC$): разовая покупка, без срока, в одном платеже с чем угодно.
    parsed = parse_payment_command("оплата ref:rufa\nкурс 100 WWC$\nполучено 100 WWC$")
    assert parsed.lines == [PaymentLine("course_academy", 10000, "WUSD", 0, False, "")]
    mixed = parse_payment_command("оплата ref:rufa\nPRO 3000 RUB 3\nакадемия 10000 RUB\nполучено 13000 RUB")
    assert [(l.product_code, l.access_months) for l in mixed.lines][-1] == ("course_academy", 0)
    assert validate_lines(parsed.lines, {"course_academy": 10000}, parsed.received_minor) == []
    assert describe_lines(parsed.lines, 10000)[0] == "курс Академии: 100 WWC$"
