"""Трекер привычек: чистый подсчёт и проверки без базы (ТЗ §5.4, примеры 1–10).

«Сегодня» подставляется: среда 11.11.2026, неделя с понедельника 09.11.
Кортеж counts — (прошло, выполнено, пропущено, серия).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.habits import rules
from app.habits.rules import HabitSpec, HabitsError, tally

D = date
MON, TUE, WED, THU, FRI, SAT, SUN = (D(2026, 11, n) for n in (9, 10, 11, 12, 13, 14, 15))
NEXT_MON = D(2026, 11, 16)


def spec(rhythm="daily", goal=1, start=WED, changed=None, pauses=()):
    return HabitSpec(rhythm, goal, start, changed, tuple(pauses))


def counts(t):
    return (t.passed, t.done, t.missed, t.streak)


def days(*numbers, month=11):
    return {D(2026, month, n) for n in numbers}


# ---- 1. ежедневная, добавлена в среду ------------------------------------------------------


def test_daily_added_on_wednesday_counts_from_wednesday():
    s = spec(start=WED)
    assert counts(tally(s, {WED}, WED)) == (1, 1, 0, 1)
    assert counts(tally(s, {WED}, THU)) == (1, 1, 0, 1)  # четверг идёт в счёт, только когда отмечен
    assert counts(tally(s, {WED}, FRI)) == (2, 1, 1, 0)  # пятница: четверг пропущен, серия 0


def test_daily_today_not_marked_is_not_a_miss_and_keeps_the_streak():
    s = spec(start=D(2026, 11, 1))
    # 1–8 пропущены, 9 и 10 сделаны, сегодняшнее 11-е пока вне счёта.
    assert counts(tally(s, days(9, 10), WED)) == (10, 2, 8, 2)


def test_daily_cannot_mark_before_start():
    with pytest.raises(HabitsError) as err:
        rules.check_mark_day(TUE, today=WED, start_day=WED)
    assert err.value.code == "before_start"
    rules.check_mark_day(WED, today=WED, start_day=WED)


# ---- 2. недельная, добавлена в среду -------------------------------------------------------


def test_weekly_partial_week_counts_only_as_a_win():
    s = spec("weekly", 2, start=WED)
    assert counts(tally(s, {WED, THU}, THU)) == (1, 1, 0, 1)  # два раза — победа
    assert counts(tally(s, {WED}, SUN)) == (0, 0, 0, 0)  # неделя ещё идёт
    assert counts(tally(s, {WED}, NEXT_MON)) == (0, 0, 0, 0)  # неполная без победы: не считается, не рвёт
    assert counts(tally(s, set(), NEXT_MON)) == (0, 0, 0, 0)


def test_weekly_full_weeks_count_from_the_next_monday():
    s = spec("weekly", 2, start=WED)
    assert counts(tally(s, set(), D(2026, 11, 23))) == (1, 0, 1, 0)  # 16–22 прожита целиком
    assert counts(tally(s, {WED, THU, NEXT_MON, D(2026, 11, 20)}, D(2026, 11, 23))) == (2, 2, 0, 2)


def test_weekly_marks_over_the_goal_stay_one_win_and_show_in_period():
    s = spec("weekly", 2, start=D(2026, 11, 2))
    t = tally(s, {MON, TUE, WED}, WED)
    assert counts(t) == (2, 1, 1, 1)  # неделя 2–8 пропущена, текущая выиграна
    assert (t.period, t.period_done, t.period_goal) == ("week", 3, 2)


# ---- 3. воскресенье отмечено в понедельник -------------------------------------------------


def test_sunday_marked_on_monday_wins_the_closed_week():
    s = spec("weekly", 2, start=D(2026, 11, 2))
    base = {D(2026, 11, 3), D(2026, 11, 5), TUE}  # неделя 2–8: два раза; 9–15: один (вторник)
    assert counts(tally(s, base, NEXT_MON)) == (2, 1, 1, 0)  # неделя 9–15 закончилась с 1 из 2
    assert counts(tally(s, base | {SUN}, NEXT_MON)) == (2, 2, 0, 2)  # в понедельник отметили воскресенье
    with pytest.raises(HabitsError) as err:  # во вторник — уже поздно
        rules.check_mark_day(SUN, today=D(2026, 11, 17), start_day=D(2026, 11, 2))
    assert err.value.code == "day_closed"


# ---- 4. месячная ---------------------------------------------------------------------------


def test_monthly_current_month_is_never_a_miss():
    s = spec("monthly", 1, start=D(2026, 10, 1))
    assert counts(tally(s, days(15, month=10), D(2026, 11, 20))) == (1, 1, 0, 1)
    assert counts(tally(s, set(), D(2026, 11, 20))) == (1, 0, 1, 0)  # октябрь закончился без отметки


def test_monthly_november_becomes_a_miss_on_december_first_unless_marked_on_the_30th():
    s = spec("monthly", 1, start=D(2026, 10, 1))
    october = days(15, month=10)
    dec1 = D(2026, 12, 1)
    assert counts(tally(s, october, D(2026, 11, 30))) == (1, 1, 0, 1)  # весь ноябрь не пропуск
    assert counts(tally(s, october, dec1)) == (2, 1, 1, 0)  # 1 декабря ноябрь — пропуск
    assert counts(tally(s, october | {D(2026, 11, 30)}, dec1)) == (2, 2, 0, 2)  # «вчера» 30.11 ещё можно отметить
    rules.check_mark_day(D(2026, 11, 30), today=dec1, start_day=s.start_day)
    with pytest.raises(HabitsError) as err:  # 2 декабря — нельзя
        rules.check_mark_day(D(2026, 11, 30), today=D(2026, 12, 2), start_day=s.start_day)
    assert err.value.code == "day_closed"


def test_monthly_started_mid_month_counts_only_as_a_win():
    s = spec("monthly", 1, start=D(2026, 10, 20))
    assert counts(tally(s, set(), D(2026, 11, 5))) == (0, 0, 0, 0)  # неполный октябрь без отметки не считается
    assert counts(tally(s, days(25, month=10), D(2026, 11, 5))) == (1, 1, 0, 1)


def test_month_and_week_bounds_at_the_edges():
    assert rules.period_bounds("monthly", D(2026, 12, 31)) == (D(2026, 12, 1), D(2026, 12, 31))
    assert rules.period_bounds("monthly", D(2028, 2, 10)) == (D(2028, 2, 1), D(2028, 2, 29))  # високосный
    assert rules.period_bounds("weekly", SUN) == (MON, SUN)
    assert rules.period_bounds("weekly", NEXT_MON) == (NEXT_MON, D(2026, 11, 22))
    # Год меняется посреди недели: 31.12.2026 — четверг.
    assert rules.period_bounds("weekly", D(2026, 12, 31)) == (D(2026, 12, 28), D(2027, 1, 3))


def test_weekly_streak_crosses_the_new_year():
    s = spec("weekly", 1, start=D(2026, 12, 14))
    marks = {D(2026, 12, 15), D(2026, 12, 22), D(2026, 12, 30), D(2027, 1, 5)}
    assert counts(tally(s, marks, D(2027, 1, 6))) == (4, 4, 0, 4)


# ---- 5. полночь ----------------------------------------------------------------------------


def test_midnight_boundary_in_the_person_zone():
    assert rules.person_days(datetime(2026, 11, 11, 20, 59, tzinfo=timezone.utc), "Europe/Moscow") == (WED, TUE)  # 23:59
    assert rules.person_days(datetime(2026, 11, 11, 21, 0, tzinfo=timezone.utc), "Europe/Moscow") == (THU, WED)  # 00:00
    assert rules.person_days(datetime(2026, 11, 11, 21, 1, tzinfo=timezone.utc), "Europe/Moscow") == (THU, WED)  # 00:01


def test_press_at_2359_arrives_at_0001_is_still_accepted_as_yesterday():
    today, _ = rules.person_days(datetime(2026, 11, 11, 21, 1, tzinfo=timezone.utc), "Europe/Moscow")
    rules.check_mark_day(WED, today=today, start_day=TUE)  # клиент прислал day 11.11
    rules.check_mark_day(WED, today=rules.person_days(datetime(2026, 11, 11, 21, 30, tzinfo=timezone.utc), "Europe/Moscow")[0],
                         start_day=TUE)  # страница, открытая с вечера, в 00:30


def test_a_page_open_since_the_day_before_yesterday_is_refused():
    today, _ = rules.person_days(datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc), "Europe/Moscow")
    with pytest.raises(HabitsError) as err:
        rules.check_mark_day(MON, today=today, start_day=D(2026, 11, 1))
    assert err.value.code == "day_closed"


def test_future_day_is_closed():
    with pytest.raises(HabitsError) as err:
        rules.check_mark_day(THU, today=WED, start_day=D(2026, 11, 1))
    assert err.value.code == "day_closed"


def test_daily_miss_appears_exactly_at_local_midnight():
    s = spec(start=D(2026, 11, 1))
    marks = days(*range(1, 11))
    before = rules.person_days(datetime(2026, 11, 11, 20, 59, tzinfo=timezone.utc), "Europe/Moscow")[0]
    after = rules.person_days(datetime(2026, 11, 11, 21, 0, tzinfo=timezone.utc), "Europe/Moscow")[0]
    assert counts(tally(s, marks, before)) == (10, 10, 0, 10)  # сегодня 11-е: ещё не пропуск
    assert counts(tally(s, marks, after)) == (11, 10, 1, 0)  # в 00:00 11-е закончилось без отметки


# ---- 6. смена часового пояса ---------------------------------------------------------------


def test_zone_moves_forward_today_changes_and_old_marks_stay():
    moment = datetime(2026, 11, 11, 20, 0, tzinfo=timezone.utc)
    assert rules.person_days(moment, "Europe/Moscow") == (WED, TUE)
    assert rules.person_days(moment, "Asia/Vladivostok") == (THU, WED)  # Москва → Дальний Восток, +7 ч
    s = spec(start=D(2026, 11, 1))
    marks = days(*range(1, 12))
    assert counts(tally(s, marks, WED)) == (11, 11, 0, 11)
    assert counts(tally(s, marks, THU)) == (11, 11, 0, 11)  # 12-е ещё идёт, старые отметки целы


def test_zone_moves_back_future_marks_are_kept_but_not_counted_until_their_day():
    s = spec(start=D(2026, 11, 1))
    marks = days(*range(1, 13))  # 12-е отмечено по «дальневосточному» поясу
    assert counts(tally(s, marks, WED)) == (11, 11, 0, 11)  # по новому поясу 12-го ещё нет
    assert counts(tally(s, marks, THU)) == (12, 12, 0, 12)  # день наступил — отметка в счёте


def test_zone_must_be_valid_iana_name():
    assert rules.parse_tz("Europe/Moscow") == "Europe/Moscow"
    assert rules.parse_tz(None) is None and rules.parse_tz("  ") is None
    for bad in ("Mars/Olympus", "../../etc/passwd", "x" * 70, "Europe Moscow", 12):
        with pytest.raises(HabitsError) as err:
            rules.parse_tz(bad)
        assert err.value.code == "bad_tz"


# ---- 7. пауза ------------------------------------------------------------------------------


def test_pause_days_do_not_count_and_do_not_break_the_streak():
    s = spec(start=D(2026, 11, 1), pauses=[(THU, D(2026, 11, 20))])  # пауза 12.11, «Продолжить» 20.11
    marks = days(*range(1, 12), 20, 21, 22, 23, 24)
    assert counts(tally(s, marks, D(2026, 11, 25))) == (16, 16, 0, 16)
    # Дни паузы без отметки — не пропуск; после «Продолжить» ежедневная идёт с 20.11.
    assert counts(tally(s, days(*range(1, 12)), D(2026, 11, 21))) == (12, 11, 1, 0)  # 20.11 пропущен


def test_marks_made_during_a_pause_count():
    s = spec(start=D(2026, 11, 1), pauses=[(THU, None)])
    marks = days(*range(1, 12), 14)
    assert counts(tally(s, marks, D(2026, 11, 16))) == (12, 12, 0, 12)


def test_open_pause_today_is_not_a_miss_for_a_daily_habit():
    s = spec(start=D(2026, 11, 1), pauses=[(THU, None)])
    assert counts(tally(s, days(*range(1, 12)), D(2026, 11, 30))) == (11, 11, 0, 11)


def test_weekly_period_with_a_pause_counts_only_as_a_win():
    s = spec("weekly", 2, start=D(2026, 11, 2), pauses=[(THU, D(2026, 11, 20))])
    only_one = tally(s, {MON}, D(2026, 11, 23))
    assert counts(only_one) == (1, 0, 1, 0)  # 2–8 прожита целиком и пропущена; 9–15 с паузой не считается; 16–22 тоже (пауза до 20-го)
    won = tally(s, {MON, TUE}, D(2026, 11, 23))
    assert counts(won) == (2, 1, 1, 1)  # две отметки в неделе с паузой — победа, и серия тянется через пустые периоды


def test_monthly_period_with_a_pause_counts_only_as_a_win():
    s = spec("monthly", 1, start=D(2026, 10, 1), pauses=[(D(2026, 10, 20), D(2026, 10, 25))])
    assert counts(tally(s, set(), D(2026, 11, 3))) == (0, 0, 0, 0)  # октябрь с паузой без победы не считается
    assert counts(tally(s, days(5, month=10), D(2026, 11, 3))) == (1, 1, 0, 1)


def test_empty_pause_interval_is_ignored():
    s = spec(start=D(2026, 11, 1), pauses=[(THU, THU)])
    assert counts(tally(s, set(), D(2026, 11, 13))) == (12, 0, 12, 0)


# ---- 10. смена ритма -----------------------------------------------------------------------


def test_rhythm_change_keeps_old_days_as_marks_and_the_change_week_is_a_win_only():
    s = spec("weekly", 3, start=D(2026, 11, 2), changed=WED)  # «Кардио»: каждый день → 3 раза в неделю
    old = days(2, 3, 4, 5, 6, 7, 8, 9, 10)  # ежедневные отметки до смены
    t = tally(s, old | {WED}, WED)
    assert counts(t) == (1, 1, 0, 1)  # пн, вт + среда = 3 отметки в неделе смены: победа; неделя 2–8 не считается
    assert (t.period_done, t.period_goal) == (3, 3)
    assert counts(tally(s, old, WED)) == (0, 0, 0, 0)  # без отметки в среду — 2 из 3: не считается и не рвёт
    assert counts(tally(s, days(9), D(2026, 11, 17))) == (0, 0, 0, 0)  # неделя смены не выиграна — не считается и не рвёт
    assert counts(tally(s, days(9), D(2026, 11, 23))) == (1, 0, 1, 0)  # 16–22 — полная неделя по новому ритму


def test_rhythm_change_on_a_monday_makes_that_week_full():
    s = spec("weekly", 2, start=D(2026, 11, 2), changed=MON)
    assert counts(tally(s, days(10), NEXT_MON)) == (1, 0, 1, 0)


# ---- 8–9. сервис (удаление, лимит) в postgres-тесте; здесь — проверка входа ---------------


def test_template_defaults_match_the_table():
    expected = {
        "water": ("daily", 1), "warmup": ("daily", 1), "cardio": ("daily", 1), "strength": ("weekly", 2),
        "reading": ("daily", 1), "book": ("monthly", 1), "calls": ("daily", 1), "meetings": ("weekly", 2),
    }
    for template, pair in expected.items():
        params = {"liters": 2.5} if template == "water" else {}
        new = rules.clean_new({"template": template, "params": params})
        assert (new["rhythm"], new["goal"]) == pair, template
    assert rules.clean_new({"template": "reading"})["params"] == {"pages": 10}


def test_templates_with_a_count_can_switch_between_daily_and_n_per_week():
    assert rules.clean_new({"template": "cardio", "rhythm": "weekly", "goal": 3})["goal"] == 3
    assert rules.clean_new({"template": "strength", "rhythm": "daily"})["rhythm"] == "daily"
    assert rules.clean_new({"template": "calls", "rhythm": "weekly", "goal": 7})["goal"] == 7
    for bad in (
        {"template": "water", "rhythm": "weekly", "goal": 2, "params": {"liters": 2.5}},
        {"template": "book", "rhythm": "daily"},
        {"template": "cardio", "rhythm": "weekly", "goal": 0},
        {"template": "cardio", "rhythm": "weekly", "goal": 8},
        {"template": "cardio", "rhythm": "weekly"},
        {"template": "cardio", "rhythm": "daily", "goal": 3},
        {"template": "cardio", "rhythm": "hourly"},
    ):
        with pytest.raises(HabitsError) as err:
            rules.clean_new(bad)
        assert err.value.code == "bad_rhythm", bad


def test_bad_template_title_and_params():
    def code(body):
        with pytest.raises(HabitsError) as err:
            rules.clean_new(body)
        return err.value.code

    assert code({"template": "swim"}) == "bad_template"
    assert code({}) == "bad_template"
    assert code({"template": "custom"}) == "bad_title"
    assert code({"template": "custom", "title": "   "}) == "bad_title"
    assert code({"template": "custom", "title": "я" * 41}) == "bad_title"
    assert code({"template": "custom", "title": "а\u0000б"}) == "bad_title"
    assert rules.clean_new({"template": "custom", "title": "я" * 40})["title"] == "я" * 40
    assert rules.clean_new({"template": "custom", "title": "  Йога   утром "})["title"] == "Йога утром"
    assert rules.clean_new({"template": "warmup", "title": "ignored"})["title"] is None
    assert code({"template": "water"}) == "bad_params"
    assert code({"template": "water", "params": {"liters": 0.5}}) == "bad_params"
    assert code({"template": "water", "params": {"liters": 9.1}}) == "bad_params"
    assert code({"template": "water", "params": {"liters": 2.55}}) == "bad_params"
    assert code({"template": "water", "params": {"liters": "2.5"}}) == "bad_params"
    assert code({"template": "reading", "params": {"pages": 0}}) == "bad_params"
    assert code({"template": "reading", "params": {"pages": 501}}) == "bad_params"
    assert code({"template": "reading", "params": {"pages": True}}) == "bad_params"
    assert code({"template": "warmup", "params": {"x": 1}}) == "bad_params"
    assert rules.clean_new({"template": "water", "params": {"liters": 3}})["params"] == {"liters": 3.0}
    assert rules.clean_new({"template": "water", "params": {"liters": 2.6}})["params"] == {"liters": 2.6}
    assert rules.clean_new({"template": "reading", "params": {"pages": 500}})["params"] == {"pages": 500}


def test_patch_marks_rhythm_change_only_when_rhythm_or_goal_differ():
    cardio = {"template": "cardio", "rhythm": "daily", "goal": 1}
    assert rules.clean_patch(cardio, {"rhythm": "weekly", "goal": 3}) == {"rhythm": "weekly", "goal": 3, "rhythm_changed": True}
    assert rules.clean_patch(cardio, {"rhythm": "daily"})["rhythm_changed"] is False
    assert rules.clean_patch(cardio, {})["rhythm_changed"] is False
    weekly = {"template": "strength", "rhythm": "weekly", "goal": 2}
    assert rules.clean_patch(weekly, {"goal": 4})["goal"] == 4
    assert rules.clean_patch(weekly, {"rhythm": "daily"}) == {"rhythm": "daily", "goal": 1, "rhythm_changed": True}
    with pytest.raises(HabitsError) as err:
        rules.clean_patch(cardio, {"rhythm": "weekly"})  # число раз обязательно
    assert err.value.code == "bad_rhythm"
    with pytest.raises(HabitsError) as err:
        rules.clean_patch(cardio, {"title": "x"})  # название — только у «своей»
    assert err.value.code == "bad_title"
    water = {"template": "water", "rhythm": "daily", "goal": 1}
    assert rules.clean_patch(water, {"params": {"liters": 2.6}})["params"] == {"liters": 2.6}


def test_parse_day():
    assert rules.parse_day("2026-11-11") == WED
    for bad in ("11.11.2026", "", None, "2026-13-01"):
        with pytest.raises(HabitsError) as err:
            rules.parse_day(bad)
        assert err.value.code == "bad_day"
