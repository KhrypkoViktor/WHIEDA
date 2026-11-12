"""Цель в трекере привычек: счёт и проверка входа без базы (ТЗ §3.5, §5.5).

Примеры 5.5 (сегодня 11.11.2026): начало 11.11, срок 31.12 → осталось 50, прошло 0 из 50 (0 %);
начало 05.10, срок 31.12 → осталось 50, прошло 37 из 87 (43 %); срок 11.11 → «последний день»;
срок 10.11 → срок вышел; начало равно сроку → всего 0, 100 %.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.habits import rules
from app.habits.rules import HabitsError, clean_goal, goal_counts

D = date
TODAY = D(2026, 11, 11)


def counts(start, due, today=TODAY):
    c = goal_counts(start, due, today)
    return c["left"], c["passed"], c["total"], c["percent"], c["expired"]


def test_examples_of_the_brief():
    assert counts(D(2026, 11, 11), D(2026, 12, 31)) == (50, 0, 50, 0, False)
    assert counts(D(2026, 10, 5), D(2026, 12, 31)) == (50, 37, 87, 43, False)
    assert counts(D(2026, 10, 5), D(2026, 11, 11)) == (0, 37, 37, 100, False)  # последний день: ещё идёт
    assert counts(D(2026, 10, 5), D(2026, 11, 10)) == (0, 36, 36, 100, True)  # срок вышел
    assert counts(D(2026, 11, 11), D(2026, 11, 11)) == (0, 0, 0, 100, False)  # всего 0 → 100 %


def test_percent_rounds_half_up_on_whole_numbers():
    # 1 из 2 = 50 %; 1 из 8 = 12,5 → 13; 1 из 3 = 33,3 → 33; 2 из 3 = 66,7 → 67.
    assert goal_counts(D(2026, 11, 10), D(2026, 11, 12), D(2026, 11, 11))["percent"] == 50
    assert goal_counts(D(2026, 11, 10), D(2026, 11, 18), D(2026, 11, 11))["percent"] == 13
    assert goal_counts(D(2026, 11, 10), D(2026, 11, 13), D(2026, 11, 11))["percent"] == 33
    assert goal_counts(D(2026, 11, 10), D(2026, 11, 13), D(2026, 11, 12))["percent"] == 67


def test_last_day_and_the_day_after():
    due = D(2026, 12, 31)
    assert goal_counts(D(2026, 12, 1), due, due)["expired"] is False
    assert goal_counts(D(2026, 12, 1), due, due)["left"] == 0
    after = goal_counts(D(2026, 12, 1), due, D(2027, 1, 1))
    assert after["expired"] is True
    assert (after["left"], after["passed"], after["percent"]) == (0, 30, 100)  # не выходят за «всего» и 100 %


def test_start_in_the_future_after_a_timezone_shift_does_not_go_negative():
    c = goal_counts(D(2026, 11, 12), D(2026, 12, 31), D(2026, 11, 11))
    assert (c["passed"], c["percent"], c["left"]) == (0, 0, 50)


def test_counts_follow_the_persons_local_day_with_a_substituted_now():
    # Москва против Владивостока: один и тот же момент — разные «сегодня», а значит разные числа.
    now = datetime(2026, 12, 31, 20, 0, tzinfo=timezone.utc)
    moscow, _ = rules.person_days(now, "Europe/Moscow")  # 23:00 31.12
    vladivostok, _ = rules.person_days(now, "Asia/Vladivostok")  # 06:00 01.01
    start, due = D(2026, 12, 1), D(2026, 12, 31)
    assert goal_counts(start, due, moscow)["expired"] is False
    assert goal_counts(start, due, moscow)["left"] == 0
    assert goal_counts(start, due, vladivostok)["expired"] is True


# ---- вход ---------------------------------------------------------------------------------


def goal(**body):
    return clean_goal(body, today=TODAY, current_start=body.pop("_current", None))


def test_create_defaults_start_to_today_and_cleans_the_title():
    got = clean_goal({"title": "  Квалификация   директора ", "due": "2026-12-31"}, today=TODAY, current_start=None)
    assert got == {"title": "Квалификация директора", "due": D(2026, 12, 31), "start": TODAY}


def test_create_may_start_earlier_but_not_later_than_today():
    got = clean_goal({"title": "Цель", "due": "2026-12-31", "start": "2026-10-05"}, today=TODAY, current_start=None)
    assert got["start"] == D(2026, 10, 5)
    for bad in ("2026-11-12", "завтра", "2026-13-01", 20261105, "2026-1-5"):
        with pytest.raises(HabitsError) as caught:
            clean_goal({"title": "Цель", "due": "2026-12-31", "start": bad}, today=TODAY, current_start=None)
        assert (caught.value.status, caught.value.code) == (400, "bad_goal_start"), bad


def test_blank_start_is_the_default():
    for blank in (None, "", "  "):
        got = clean_goal({"title": "Цель", "due": "2026-12-31", "start": blank}, today=TODAY, current_start=None)
        assert got["start"] == TODAY


def test_edit_keeps_the_start_and_rejects_another_one():
    current = D(2026, 10, 5)
    got = clean_goal({"title": "Новая", "due": "2027-01-31"}, today=TODAY, current_start=current)
    assert got["start"] == current
    same = clean_goal({"title": "Новая", "due": "2027-01-31", "start": "2026-10-05"}, today=TODAY, current_start=current)
    assert same["start"] == current  # то же самое начало — не ошибка
    with pytest.raises(HabitsError) as caught:
        clean_goal({"title": "Новая", "due": "2027-01-31", "start": "2026-10-06"}, today=TODAY, current_start=current)
    assert caught.value.code == "bad_goal_start"


def test_title_rules_80_characters_not_empty_no_control_characters():
    ok = "Я" * 80
    assert clean_goal({"title": ok, "due": "2026-12-31"}, today=TODAY, current_start=None)["title"] == ok
    for bad in ("", "   ", None, "Я" * 81, "цель\x00", "цель‮", 5 * [1]):
        with pytest.raises(HabitsError) as caught:
            clean_goal({"title": bad, "due": "2026-12-31"}, today=TODAY, current_start=None)
        assert (caught.value.status, caught.value.code) == (400, "bad_goal_title"), repr(bad)


def test_due_is_required_not_before_today_and_not_before_start():
    assert clean_goal({"title": "Цель", "due": "2026-11-11"}, today=TODAY, current_start=None)["due"] == TODAY
    for bad in (None, "", "2026-11-10", "скоро", "2026-02-30", 20261231, "31.12.2026"):
        with pytest.raises(HabitsError) as caught:
            clean_goal({"title": "Цель", "due": bad}, today=TODAY, current_start=None)
        assert (caught.value.status, caught.value.code) == (400, "bad_goal_due"), repr(bad)
    # срок раньше начала (начало поставили раньше, срок — сегодня, начало не позже сегодня: срок ≥ начала всегда)
    with pytest.raises(HabitsError) as caught:
        clean_goal({"title": "Цель", "due": "2026-11-11", "start": "2026-11-12"}, today=TODAY, current_start=None)
    assert caught.value.code == "bad_goal_start"
