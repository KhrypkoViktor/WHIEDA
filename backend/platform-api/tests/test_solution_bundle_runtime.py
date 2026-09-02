from app.advisor.sql.solution_bundles import (
    bundle_sku_groups,
    has_specific_bundle_context,
    match_solution_bundle,
    may_match_solution_bundle,
)


BUNDLE = {
    "bundle_id": "bundle_raw_0031",
    "bundle_name": "Титановые импланты и Активатор",
    "aliases": "совместимость Активатора с металлическими имплантами;Титановые импланты и Активатор",
    "sku_groups": "M015-00",
    "active": True,
}


def test_matches_owner_alias_not_in_old_topic_gate():
    assert may_match_solution_bundle("титановые импланты и активатор")
    assert match_solution_bundle("титановые импланты и активатор", [BUNDLE]) == BUNDLE


def test_keeps_alternatives_in_bundle_order():
    assert bundle_sku_groups({"sku_groups": "F001|F002;F028"}) == [["F001", "F002"], ["F028"]]


def test_does_not_match_unrelated_question():
    assert match_solution_bundle("покажи фото товара", [BUNDLE]) is None


INSOLES = {
    "bundle_id": "bundle_insoles_protocol",
    "bundle_name": "Стельки при плоскостопии, вальгусе и шпоре",
    "aliases": (
        "стельки можно;стельки при импланте;плоскостопие;вальгус;шпора;"
        "пяточная шпора;как носить стельки;стельки при плоскостопии"
    ),
    "sku_groups": "D013",
    "active": True,
}

VISION_WITH_INSOLES = {
    "bundle_id": "bundle_raw_0021",
    "bundle_name": "Очки + Активатор + Стельки для поддержки зрения",
    "aliases": (
        "вопрос про конкретный диагноз глаз (глаукома/катаракта/атрофия зрительного нерва);"
        "Очки + Активатор + Стельки для поддержки зрения;Графеновые очки"
    ),
    "sku_groups": "D014;M015-00;D013",
    "active": True,
}


def test_insoles_match_foot_questions_not_bare_product_name():
    assert match_solution_bundle("стельки при плоскостопии", [INSOLES, VISION_WITH_INSOLES]) == INSOLES
    assert match_solution_bundle("как носить стельки", [INSOLES, VISION_WITH_INSOLES]) == INSOLES
    assert match_solution_bundle("стельки с анионами", [INSOLES, VISION_WITH_INSOLES]) is None
    assert match_solution_bundle("стельки", [INSOLES]) is None


def test_insoles_phrases_count_as_bundle_context_not_bare_product():
    assert has_specific_bundle_context("стельки при плоскостопии")
    assert has_specific_bundle_context("как носить стельки")
    assert has_specific_bundle_context("пяточная шпора")
    assert not has_specific_bundle_context("стельки с анионами")
    assert not has_specific_bundle_context("стельки")


def test_vision_phrase_still_beats_insoles_aliases():
    assert (
        match_solution_bundle(
            "Очки + Активатор + Стельки для поддержки зрения",
            [INSOLES, VISION_WITH_INSOLES],
        )
        == VISION_WITH_INSOLES
    )
