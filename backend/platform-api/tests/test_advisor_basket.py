from __future__ import annotations

from app.advisor.sql.basket import build_starter_basket, parse_budget_request
from app.advisor.sql.text import product_query_text


def test_product_query_text_strips_price_prefix():
    assert product_query_text("цена спирулина") == "спирулина"
    assert product_query_text("сколько стоит активатор клеток") == "активатор клеток"
    assert product_query_text("что такое активатор клеток pro") == "активатор клеток pro"


def test_followup_matches_day_foto():
    from app.advisor.sql.text import is_context_followup, is_materials_request

    assert is_context_followup("дай фото")
    assert is_context_followup("дай видео")
    assert is_context_followup("материалы")
    assert is_materials_request("материалы")


def test_business_definition_not_price_intent():
    from app.advisor.sql.text import has_price_intent

    assert not has_price_intent("что такое повторка")
    assert has_price_intent("повторка активатора")


def test_parse_budget_request_from_followup():
    stored = {"starter_basket": True}
    assert parse_budget_request("на 500", stored) == {
        "budget_byn": 500.0,
        "target_pv": None,
        "goal": "balanced",
    }


def test_has_price_intent_ignores_pv_definition():
    from app.advisor.sql.text import has_price_intent, is_pv_definition_question

    assert is_pv_definition_question("что такое pv")
    assert not has_price_intent("что такое pv")
    assert has_price_intent("цена спирулина")


def test_build_starter_basket_respects_budget():
    catalog = [
        {
            "sku": "a",
            "canonical_name": "Спирулина",
            "retail_price_byn": 120,
            "partner_points": 40,
            "business_priority": 90,
            "universality_score": 80,
            "demo_score": 70,
            "gift_score": 10,
            "popularity_score": 60,
        },
        {
            "sku": "b",
            "canonical_name": "Активатор",
            "retail_price_byn": 280,
            "partner_points": 90,
            "business_priority": 85,
            "universality_score": 75,
            "demo_score": 65,
            "gift_score": 5,
            "popularity_score": 55,
        },
    ]
    text, skus = build_starter_basket(catalog, budget_byn=500.0, target_pv=None, goal="balanced")
    assert "Спирулина" in text
    assert "Активатор" in text
    assert len(skus) >= 1
    assert "400" in text or "500" in text
