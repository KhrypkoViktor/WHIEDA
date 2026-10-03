"""Academy v2: unlock rules, lesson completeness, the next step (pure functions)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.academy.rules import (
    LessonIn,
    ModuleIn,
    evaluate_locks,
    is_complete,
    next_lesson_key,
    parse_unlock,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
OPEN = {"type": "open"}
AFTER_PREV = {"type": "after_prev"}


def lesson(key, module="m1", *, done=False, unlock=None, required=None, status=None):
    return LessonIn(
        key=key, module_key=module, unlock=unlock, done=done,
        assignment_required=required, submission_status=status,
    )


def locks(modules, lessons, **kw):
    kw.setdefault("now", NOW)
    kw.setdefault("started_at", NOW - timedelta(days=1))
    return {key: (lock.locked, lock.reason) for key, lock in evaluate_locks(modules, lessons, **kw).items()}


# ---- completeness -------------------------------------------------------------


def test_lesson_without_homework_is_complete_when_done():
    assert is_complete(lesson("a", done=True))
    assert not is_complete(lesson("a", done=False))


def test_required_homework_must_be_accepted():
    assert not is_complete(lesson("a", done=True, required=True))
    assert not is_complete(lesson("a", done=True, required=True, status="submitted"))
    assert not is_complete(lesson("a", done=True, required=True, status="returned"))
    assert is_complete(lesson("a", done=True, required=True, status="accepted"))
    # Принятая домашка без «Сделал» — урок ещё не завершён.
    assert not is_complete(lesson("a", done=False, required=True, status="accepted"))


def test_optional_homework_does_not_block_completeness():
    assert is_complete(lesson("a", done=True, required=False))
    assert is_complete(lesson("a", done=True, required=False, status="returned"))


# ---- open / after_prev ------------------------------------------------------------


def test_open_module_opens_every_lesson():
    assert locks([ModuleIn("m1", OPEN)], [lesson("a"), lesson("b")]) == {"a": (False, None), "b": (False, None)}


def test_after_prev_module_waits_for_every_lesson_of_the_previous_module():
    modules = [ModuleIn("m1", OPEN), ModuleIn("m2", AFTER_PREV)]
    half = [lesson("a", done=True), lesson("b"), lesson("c", "m2")]
    assert locks(modules, half)["c"] == (True, "after_prev")
    full = [lesson("a", done=True), lesson("b", done=True), lesson("c", "m2")]
    assert locks(modules, full)["c"] == (False, None)


def test_after_prev_module_counts_homework_acceptance():
    modules = [ModuleIn("m1", OPEN), ModuleIn("m2", AFTER_PREV)]
    waiting = [lesson("a", done=True, required=True, status="submitted"), lesson("b", "m2")]
    assert locks(modules, waiting)["b"] == (True, "after_prev")
    accepted = [lesson("a", done=True, required=True, status="accepted"), lesson("b", "m2")]
    assert locks(modules, accepted)["b"] == (False, None)


def test_first_module_after_prev_and_empty_previous_module_are_open():
    assert locks([ModuleIn("m1", AFTER_PREV)], [lesson("a")]) == {"a": (False, None)}
    modules = [ModuleIn("m1", OPEN), ModuleIn("empty", OPEN), ModuleIn("m3", AFTER_PREV)]
    assert locks(modules, [lesson("a"), lesson("c", "m3")])["c"] == (False, None)


def test_lesson_after_prev_looks_at_the_previous_lesson_across_modules():
    modules = [ModuleIn("m1", OPEN), ModuleIn("m2", OPEN)]
    rows = [lesson("a"), lesson("b", "m2", unlock=AFTER_PREV)]
    assert locks(modules, rows)["b"] == (True, "after_prev")
    rows = [lesson("a", done=True), lesson("b", "m2", unlock=AFTER_PREV), lesson("c", "m2", unlock=AFTER_PREV)]
    assert locks(modules, rows) == {"a": (False, None), "b": (False, None), "c": (True, "after_prev")}


def test_lesson_rule_overrides_the_module_rule():
    modules = [ModuleIn("m1", OPEN), ModuleIn("m2", AFTER_PREV)]
    rows = [lesson("a"), lesson("b", "m2", unlock=OPEN), lesson("c", "m2")]
    assert locks(modules, rows) == {"a": (False, None), "b": (False, None), "c": (True, "after_prev")}


# ---- date -------------------------------------------------------------------------


def test_date_rule_opens_exactly_at_the_moment():
    at = NOW + timedelta(seconds=1)
    rule = {"type": "date", "at": at.isoformat()}
    assert locks([ModuleIn("m1", rule)], [lesson("a")]) == {"a": (True, f"date:{at.isoformat()}")}
    assert locks([ModuleIn("m1", rule)], [lesson("a")], now=at) == {"a": (False, None)}
    assert locks([ModuleIn("m1", rule)], [lesson("a")], now=at + timedelta(days=30)) == {"a": (False, None)}


def test_date_rule_reports_when_it_opens():
    at = datetime(2026, 10, 10, 7, 0, tzinfo=timezone.utc)
    result = evaluate_locks([ModuleIn("m1", {"type": "date", "at": at.isoformat()})], [lesson("a")],
                            now=NOW, started_at=None)
    assert result["a"].opens_at == at
    assert result["a"].reason == "date:2026-10-10T07:00:00+00:00"


# ---- days_after_start -------------------------------------------------------------


def test_days_after_start_counts_from_the_access_start():
    start = NOW - timedelta(days=7)
    rule = {"type": "days_after_start", "days": 7}
    assert locks([ModuleIn("m1", rule)], [lesson("a")], started_at=start) == {"a": (False, None)}
    later_start = start + timedelta(seconds=1)
    assert locks([ModuleIn("m1", rule)], [lesson("a")], started_at=later_start) == {"a": (True, "days:7")}
    lock = evaluate_locks([ModuleIn("m1", rule)], [lesson("a")], now=NOW, started_at=later_start)["a"]
    assert lock.opens_at == later_start + timedelta(days=7)


def test_days_after_start_without_a_start_stays_locked_and_zero_days_is_open():
    rule = {"type": "days_after_start", "days": 3}
    result = evaluate_locks([ModuleIn("m1", rule)], [lesson("a")], now=NOW, started_at=None)
    assert (result["a"].locked, result["a"].reason, result["a"].opens_at) == (True, "days:3", None)
    zero = {"type": "days_after_start", "days": 0}
    assert locks([ModuleIn("m1", zero)], [lesson("a")], started_at=None) == {"a": (False, None)}


# ---- course access and staff --------------------------------------------------------


def test_lock_reasons_are_only_the_ones_the_site_knows():
    """ТЗ §5: after_prev | date:<iso> | days:<n> | purchase (PRO-курс без доступа — тоже purchase)."""
    from app.academy.service import COURSE_LOCK_LESSON_REASON

    assert set(COURSE_LOCK_LESSON_REASON.values()) == {"purchase"}


def test_course_without_access_locks_every_lesson_with_its_reason():
    rows = [lesson("a"), lesson("b")]
    assert locks([ModuleIn("m1", OPEN)], rows, course_lock="purchase") == {
        "a": (True, "purchase"), "b": (True, "purchase"),
    }


def test_staff_sees_everything_open():
    modules = [ModuleIn("m1", {"type": "date", "at": (NOW + timedelta(days=9)).isoformat()})]
    assert locks(modules, [lesson("a")], bypass=True) == {"a": (False, None)}


def test_lessons_without_a_module_are_open():
    assert locks([], [lesson("a", module="")]) == {"a": (False, None)}


# ---- next step --------------------------------------------------------------------------


def test_next_lesson_skips_finished_and_locked_lessons():
    modules = [ModuleIn("m1", OPEN), ModuleIn("m2", {"type": "date", "at": (NOW + timedelta(days=1)).isoformat()})]
    rows = [
        lesson("a", done=True),
        lesson("b", done=True, required=True, status="submitted"),  # ждёт проверки — ученику делать нечего
        lesson("c", "m2"),
    ]
    assert next_lesson_key(rows, evaluate_locks(modules, rows, now=NOW, started_at=NOW)) is None
    rows[1] = lesson("b", done=True, required=True, status="returned")  # вернули — переделать
    assert next_lesson_key(rows, evaluate_locks(modules, rows, now=NOW, started_at=NOW)) == "b"
    rows[1] = lesson("b", done=True, required=True)  # «Сделал», но домашку не сдал
    assert next_lesson_key(rows, evaluate_locks(modules, rows, now=NOW, started_at=NOW)) == "b"


def test_next_lesson_is_the_first_open_unfinished_one():
    rows = [lesson("a", done=True), lesson("b"), lesson("c")]
    assert next_lesson_key(rows, evaluate_locks([ModuleIn("m1", OPEN)], rows, now=NOW, started_at=NOW)) == "b"


# ---- parsing what an author sends ------------------------------------------------------------


def test_parse_unlock_normalizes_and_rejects():
    assert parse_unlock(None) == {"type": "open"}
    assert parse_unlock({"type": "after_prev"}) == {"type": "after_prev"}
    assert parse_unlock({"type": "days_after_start", "days": "7"}) == {"type": "days_after_start", "days": 7}
    assert parse_unlock({"type": "date", "at": "2026-10-10T10:00:00+03:00"}) == {
        "type": "date", "at": "2026-10-10T07:00:00+00:00",
    }
    # Без часового пояса — время Москвы/Минска (UTC+3).
    assert parse_unlock({"type": "date", "at": "2026-10-10T10:00"}) == {"type": "date", "at": "2026-10-10T07:00:00+00:00"}
    assert parse_unlock(None, allow_none=True) is None
    for bad in (
        {"type": "someday"},
        {"type": "date"},
        {"type": "date", "at": "завтра"},
        {"type": "days_after_start"},
        {"type": "days_after_start", "days": -1},
        {"type": "days_after_start", "days": 400},
        "open",
    ):
        with pytest.raises(ValueError):
            parse_unlock(bad)


def test_after_prev_lock_names_the_lesson_to_finish():
    modules = [ModuleIn("m1", OPEN), ModuleIn("m2", AFTER_PREV), ModuleIn("m3", {"type": "date", "at": "2030-01-01T00:00:00+00:00"})]
    rows = [
        lesson("a", done=True), lesson("b"),
        lesson("c", "m2"), lesson("d", "m2", unlock=AFTER_PREV),
        lesson("e", "m3"),
    ]
    result = evaluate_locks(modules, rows, now=NOW, started_at=NOW)
    assert result["c"].after == "b"  # модуль «после предыдущего» — последний урок прошлого модуля
    assert result["d"].after == "c"  # урок «после предыдущего» — предыдущий урок
    assert result["a"].after is None and result["e"].after is None  # открыт / по дате
