"""Варианты «Заказать сайт» по прайсу владельца 11.11.2026: суммы, тексты, старые коды, строки ledger."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.site_requests import service as sr
from app.telegram import site_requests as tg

RUB = "RUB"

TOTALS = {
    "site_3": 6_000, "site_6": 8_400, "site_12": 12_600,
    "club_3": 15_000, "club_6": 29_400, "club_12": 57_600,
    "course_3": 13_000, "course_6": 15_400, "course_12": 19_600,
    "bundle": 11_000,
}


@pytest.mark.parametrize("code,rub", TOTALS.items())
def test_totals_match_price_list(code, rub):
    assert sr.plan_total_minor(code, RUB) == rub * 100


def test_all_totals_distinct_so_db_code_can_be_resolved():
    assert len(set(TOTALS.values())) == len(TOTALS)


def test_ledger_lines_of_each_kind():
    def rows(code):
        return [(l.product_code, l.amount_minor // 100, l.access_months, l.promo) for l in sr.plan_lines(code, RUB)]

    assert rows("site_6") == [("platform_subscription", 5_400, 6, False), ("site_setup", 3_000, 0, False)]
    assert rows("club_12") == [
        ("platform_subscription", 9_600, 12, False), ("club_subscription", 48_000, 12, False),
    ]
    assert rows("course_3") == [
        ("platform_subscription", 3_000, 3, False), ("course_academy", 10_000, 0, False),
    ]
    assert rows("bundle") == [
        ("platform_subscription", 3_000, 3, False), ("club_subscription", 8_000, 2, True),
    ]
    # promo-строка обязана нести пометку (иначе в ledger «акция» без причины)
    assert all(l.note for code in TOTALS for l in sr.plan_lines(code, RUB) if l.promo)


def test_old_codes_work_as_before():
    assert sr.plan_lines("site", RUB) == sr.plan_lines("site_3", RUB)
    assert sr.resolve_plan_code("site", 600_000) == "site_3"
    assert sr.resolve_plan_code("bundle", 1_100_000) == "bundle"
    assert sr.resolve_plan_code("site", None) == "site"


def test_db_code_fits_current_check_constraint_and_resolves_back():
    for code, rub in TOTALS.items():
        assert sr.db_plan_code(code) in {"site", "bundle"}
        assert sr.resolve_plan_code(sr.db_plan_code(code), rub * 100) == code


def test_mismatched_total_is_refused():
    with pytest.raises(sr.SiteRequestError):
        sr.resolve_plan_code("site", 123_456)


def test_bundle_available_until_end_of_november_msk():
    last = datetime(2026, 11, 30, 23, 59, tzinfo=sr.MSK)
    assert sr.bundle_available(last.astimezone(timezone.utc))
    assert not sr.bundle_available(datetime(2026, 12, 1, 0, 0, tzinfo=sr.MSK).astimezone(timezone.utc))
    assert sr.bundle_available(datetime(2026, 11, 11, tzinfo=timezone.utc))


def test_breakdown_texts():
    text = sr.plan_summary("club_6", RUB)
    assert "сайт на 6 месяцев" in text and "клуб на 6 месяцев — 24" in text and "подключение сайта — бесплатно" in text
    assert "подключение сайта — бесплатно" in sr.plan_summary("course_12", RUB)
    assert "бесплатно" not in sr.plan_summary("site_12", RUB) and "подключение сайта — бесплатно" in sr.plan_summary("bundle", RUB)
    assert "курс онлайн-запуска" in sr.plan_summary("course_3", RUB)
    assert sr.plan_summary("bundle", RUB).startswith("Акция ноября: ") and "клуб на 2 месяца" in sr.plan_summary("bundle", RUB)


def _request(plan_code, rub):
    return {"plan_code": plan_code, "total_amount_minor": rub * 100, "currency": RUB}


def test_payment_text_lists_composition_and_total():
    text = tg._payment_text(_request("site", 15_000))
    assert "сайт на 3 месяца" in text and "клуб на 3 месяца" in text and "15" in text.split("Итого:")[1].splitlines()[0]
    assert "скриншот чека" in text
    legacy = tg._payment_text(_request("site", 6_000))
    assert "подключение сайта — 3" in legacy and "клуб" not in legacy


def test_owner_text_has_composition():
    assert "курс онлайн-запуска" in tg._owner_plan_text(_request("site", 13_000))


def test_first_keyboard_has_four_choices_and_bundle_only_in_november(monkeypatch):
    monkeypatch.setattr(tg, "bundle_available", lambda *a, **k: True)
    data = [b["callback_data"] for row in tg._kind_keyboard()["inline_keyboard"] for b in row]
    assert data == ["site:kind:site", "site:kind:club", "site:kind:course", "site:plan:bundle"]
    label = tg._kind_keyboard()["inline_keyboard"][3][0]["text"]
    assert "11 000" in label and "30.11" in label
    monkeypatch.setattr(tg, "bundle_available", lambda *a, **k: False)
    assert [b["callback_data"] for row in tg._kind_keyboard()["inline_keyboard"] for b in row] == data[:3]


@pytest.mark.parametrize("kind", ["site", "club", "course"])
def test_terms_keyboard_shows_totals(kind):
    rows = tg._terms_keyboard(kind)["inline_keyboard"]
    assert [r[0]["callback_data"] for r in rows] == [f"site:plan:{kind}_3", f"site:plan:{kind}_6", f"site:plan:{kind}_12", "site:plans"]
    for n, row in zip((3, 6, 12), rows):
        assert f"{TOTALS[f'{kind}_{n}']:,}".replace(",", " ") in row[0]["text"].replace(" ", " ")


def test_callback_regex_accepts_new_and_old_codes():
    for data in ["site:plan:site", "site:plan:bundle", "site:plan:club_12", "site:kind:course", "site:plans"]:
        assert tg._CALLBACK_RE.fullmatch(data), data
    for data in ["site:plan:club_5", "site:kind:bundle", "site:plan:gold"]:
        assert not tg._CALLBACK_RE.fullmatch(data), data


def test_club_invite_only_for_plans_with_club():
    assert sr.plan_has_product("club_3", "club_subscription") and sr.plan_has_product("bundle", "club_subscription")
    assert not sr.plan_has_product("course_3", "club_subscription") and not sr.plan_has_product("site", "club_subscription")
    assert sr.plan_has_product("course_12", "course_academy")
