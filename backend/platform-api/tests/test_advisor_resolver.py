from __future__ import annotations

from app.advisor.sql.resolver import pick_best_product


def test_pick_best_product_prefers_base_over_pro_without_marker():
    rows = [
        {
            "sku": "M015-00",
            "canonical_name": "Активатор клеток",
            "alias": "активатор клеток",
            "priority": 100,
            "match_type": "alias",
        },
        {
            "sku": "EU-N000031-25",
            "canonical_name": "Активатор клеток PRO (комплект)",
            "alias": "активатор pro",
            "priority": 120,
            "match_type": "alias",
        },
    ]
    best = pick_best_product("активатор клеток", rows, allow_pro=False)
    assert best is not None
    assert best["sku"] == "M015-00"


def test_typo_map_resolves_spirulina():
    from app.advisor.sql.resolver import QUERY_TYPO_MAP

    assert QUERY_TYPO_MAP.get("фузялина") == "спирулина"
    assert QUERY_TYPO_MAP.get("активаор") == "активатор клеток"


def test_pick_best_product_allows_pro_when_asked():
    rows = [
        {
            "sku": "M015-00",
            "canonical_name": "Активатор клеток",
            "alias": "активатор",
            "priority": 100,
            "match_type": "alias",
        },
        {
            "sku": "EU-N000031-25",
            "canonical_name": "Активатор клеток PRO (комплект)",
            "alias": "активатор pro",
            "priority": 120,
            "match_type": "alias",
        },
    ]
    best = pick_best_product("активатор pro", rows, allow_pro=True)
    assert best is not None
    assert best["sku"] == "EU-N000031-25"


def test_pick_best_product_prefers_pro_for_full_question():
    rows = [
        {
            "sku": "M015-00",
            "canonical_name": "Активатор клеток",
            "alias": "активатор клеток",
            "priority": 100,
            "match_type": "alias",
        },
        {
            "sku": "EU-N000031-25",
            "canonical_name": "Активатор клеток PRO (комплект)",
            "alias": "активатор клеток pro",
            "priority": 90,
            "match_type": "alias",
        },
    ]
    best = pick_best_product("что такое активатор клеток pro", rows, allow_pro=True)
    assert best is not None
    assert best["sku"] == "EU-N000031-25"


def test_stems_reach_inflected_product_names():
    from app.advisor.sql.resolver import stem, stem_phrase_match

    assert stem("красного") == stem("красный") == "красн"
    assert stem("сауны") == stem("сауна") == "саун"
    assert stem("зелёный") == stem("зеленый")
    assert stem_phrase_match("цена красного эликсира", "красный эликсир")
    assert stem_phrase_match("зеленый эликсир", "зелёный эликсир")
    assert stem_phrase_match("цена сауны", "сауна")
    assert not stem_phrase_match("активатор клеток", "паста")
