from app.advisor.sql.text import is_high_risk_medical_boundary


def test_existing_acute_cases_still_stop():
    assert is_high_risk_medical_boundary("Кто-нибудь лечил гнойную ангину активатором?")
    assert is_high_risk_medical_boundary("Девочка 7 месяцев, врождённая гемангиома. Можно лазером?")


def test_do_not_route_dialysis_onco_pregnancy_reanimation():
    assert is_high_risk_medical_boundary("У пожилой женщины диализ, можно ли носить стельки?")
    assert is_high_risk_medical_boundary("на гемодиализе, можно Активатором?")
    assert is_high_risk_medical_boundary("год назад была пересадка почки. Можно ли стельки?")
    assert is_high_risk_medical_boundary("при онкологии можно стельки с анионами носить")
    assert is_high_risk_medical_boundary("рак 4 стадии фохоу")
    assert is_high_risk_medical_boundary("остеосаркома 4 стадия, можно ли при шпорах носить стельки?")
    assert is_high_risk_medical_boundary("идёт химиотерапия, что попить")
    assert is_high_risk_medical_boundary("беременным можно носить стельки?")
    assert is_high_risk_medical_boundary("кормящим можно эликсир 3 драгоценности")
    assert is_high_risk_medical_boundary("схема реанимации клеток")
    assert is_high_risk_medical_boundary("Схема РЕАНИМАЦИЯ при тяжёлом диагнозе")


def test_insoles_and_energy_questions_are_not_high_risk():
    assert not is_high_risk_medical_boundary("стельки при плоскостопии")
    assert not is_high_risk_medical_boundary("как носить стельки")
    assert not is_high_risk_medical_boundary("стельки с анионами")
    assert not is_high_risk_medical_boundary("энергия")
    assert not is_high_risk_medical_boundary("иммунитет")
    assert not is_high_risk_medical_boundary("чистые сосуды")


def test_cancer_word_does_not_eat_unrelated_stems():
    assert not is_high_risk_medical_boundary("ракетка для зала")
    assert not is_high_risk_medical_boundary("врач сказал носить стельки")
