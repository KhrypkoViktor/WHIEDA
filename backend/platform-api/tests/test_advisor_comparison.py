from __future__ import annotations

from app.advisor.sql.comparison import build_compare_answer


def test_build_compare_answer_uses_cards():
    left = {"sku": "S1", "canonical_name": "Спирулина", "retail_price_byn": 245, "partner_w": 50}
    right = {"sku": "S2", "canonical_name": "Активатор клеток", "retail_price_byn": 1750, "partner_w": 300}
    left_card = {"what_it_is": "Суперфуд для ежедневной поддержки.", "who_asks_about_it": "Тем, кто хочет простой вход."}
    right_card = {"what_it_is": "Прибор для локальных зон.", "who_asks_about_it": "При боли в шее и спине."}
    text = build_compare_answer(left, left_card, right, right_card)
    assert "Спирулина и Активатор клеток" in text
    assert "Суперфуд" in text
    assert "По цене" in text
    assert "245 BYN" in text


def test_format_partner_price_only():
    from app.advisor.sql.formatters import format_price

    product = {"retail_price_byn": 1750, "partner_price_byn": 1050, "partner_w": 300}
    text = format_price(product, "BY", partner_only=True)
    assert "1050" in text
