"""Academy V39 (pure parts): pause between test attempts, ``free_for.unlock``, its progress object.

ТЗ: ``PROCESS/wwc-mini-courses-20261016/TZ_UNLOCK.md`` пп. 2–5. Работа с базой и роутами — в
``test_academy_unlock_postgres.py``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.academy.author import parse_free_for
from app.academy.quiz import MAX_RETRY_AFTER_MIN, QuizError, public_quiz, retry_after_min, retry_at, validate_quiz
from app.academy.service import (
    FreeFacts,
    Members,
    UnlockSpec,
    course_unlock,
    free_reason_for,
    quiz_path,
    unlock_member,
    unlock_met,
    unlock_spec,
)
from app.academy.unlock import clean_platform, unlock_progress, unlock_text, unlock_view

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
RULE = {"unlock": {"quiz": {"course": "zapusk-wwc", "lesson": "itogi-i-plan"}, "app": True}}
TEST = ("zapusk-wwc", "itogi-i-plan")
TEST_DICT = {"course": "zapusk-wwc", "lesson": "itogi-i-plan"}


def question(text="Вопрос?"):
    return {
        "q": text,
        "ref": "Разбор",
        "options": [
            {"text": "Неверно", "correct": False, "hint": "Не совсем. Перечитайте: Разбор."},
            {"text": "Верно", "correct": True, "hint": ""},
        ],
    }


def quiz(**extra):
    return {"pass": 1, "questions": [question()], **extra}


def course(rule=RULE, access_rule="purchase"):
    return {"slug": "mini-dhando", "title": "Алхимия капитала", "access_rule": access_rule, "free_for": rule}


def facts(*, club=False, courses=(), tests=(), app=False):
    return FreeFacts(club=club, course_slugs=frozenset(courses), passed_quizzes=frozenset(tests), app_installed=app)


# ---- retry_after_min in the test ------------------------------------------------------------


@pytest.mark.parametrize("value", [30, 1, 1440])
def test_retry_after_min_is_kept_in_the_stored_form(value):
    stored = validate_quiz(quiz(retry_after_min=value))
    assert stored["retry_after_min"] == value
    assert validate_quiz(stored) == stored  # сохранённый вид снова проходит проверку
    assert retry_after_min(stored) == value


@pytest.mark.parametrize("raw", [{}, {"retry_after_min": 0}, {"retry_after_min": None}])
def test_no_pause_leaves_the_stored_form_as_it_was(raw):
    stored = validate_quiz(quiz(**raw))
    assert "retry_after_min" not in stored  # мини-курс без паузы хранится, как до V39
    assert retry_after_min(stored) == 0


@pytest.mark.parametrize("value", [-1, MAX_RETRY_AFTER_MIN + 1, "30", 30.5, True, False, [30], {"min": 30}])
def test_retry_after_min_outside_the_rules_is_refused(value):
    with pytest.raises(QuizError, match="retry_after_min"):
        validate_quiz(quiz(retry_after_min=value))


def test_retry_after_min_is_only_a_top_level_key():
    raw = quiz()
    raw["questions"][0]["retry_after_min"] = 5
    with pytest.raises(QuizError, match="неизвестные поля retry_after_min"):
        validate_quiz(raw)


def test_public_quiz_shows_the_pause_and_still_no_answers():
    paused = public_quiz(validate_quiz(quiz(retry_after_min=30)))
    assert paused["retry_after_min"] == 30
    assert public_quiz(validate_quiz(quiz()))["retry_after_min"] == 0
    dumped = json.dumps(paused, ensure_ascii=False)
    assert "correct" not in dumped and "hint" not in dumped


@pytest.mark.parametrize("stored", [None, {}, {"retry_after_min": -5}, {"retry_after_min": "x"},
                                    {"retry_after_min": True}, {"retry_after_min": 99999}])
def test_a_stored_pause_written_around_the_validator_counts_as_none(stored):
    assert retry_after_min(stored) == 0


# ---- retry_at: the pure rule ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pause", "ago_min", "passed", "waits_min"),
    [
        (30, 0, False, 30),  # только что провалил
        (30, 10, False, 20),
        (30, 29, False, 1),
        (30, 30, False, None),  # ровно N минут — можно
        (30, 31, False, None),
        (30, 5, True, None),  # сдан хоть раз — пауз нет
        (0, 0, False, None),  # без паузы — как до V39
        (-3, 0, False, None),
    ],
)
def test_retry_at(pause, ago_min, passed, waits_min):
    last = NOW - timedelta(minutes=ago_min)
    moment = retry_at(pause, last, passed=passed, now=NOW)
    assert moment == (None if waits_min is None else NOW + timedelta(minutes=waits_min))


def test_no_attempts_means_no_wait():
    assert retry_at(30, None, passed=False, now=NOW) is None


# ---- free_for.unlock -> free_reason ------------------------------------------------------------


def test_every_condition_of_the_rule_must_hold():
    assert free_reason_for(course(), facts(tests=[TEST], app=True)) == "unlock"
    assert free_reason_for(course(), facts(tests=[TEST])) is None  # приложения нет
    assert free_reason_for(course(), facts(app=True)) is None  # теста нет
    assert free_reason_for(course(), facts(tests=[("zapusk-wwc", "drugoy-urok")], app=True)) is None
    assert free_reason_for(course(), facts(tests=[("other", "itogi-i-plan")], app=True)) is None
    assert free_reason_for(course(), None) is None


def test_a_rule_with_one_condition_needs_only_that_one():
    only_app = course({"unlock": {"app": True}})
    only_quiz = course({"unlock": {"quiz": {"course": "zapusk-wwc", "lesson": "itogi-i-plan"}}})
    assert free_reason_for(only_app, facts(app=True)) == "unlock"
    assert free_reason_for(only_app, facts(tests=[TEST])) is None
    assert free_reason_for(only_quiz, facts(tests=[TEST])) == "unlock"
    assert free_reason_for(only_quiz, facts(app=True)) is None
    assert free_reason_for(course({"unlock": {"app": False, "quiz": TEST_DICT}}), facts(tests=[TEST])) == "unlock"


def test_club_and_course_go_first_the_other_reasons_are_unchanged():
    rule = {"club": True, "courses": ["online-start-4w"], **RULE}
    everything = facts(club=True, courses=["online-start-4w"], tests=[TEST], app=True)
    assert free_reason_for(course(rule), everything) == "club"
    assert free_reason_for(course(rule), facts(courses=["online-start-4w"], tests=[TEST], app=True)) == "course"
    assert free_reason_for(course(rule), facts(tests=[TEST], app=True)) == "unlock"
    assert free_reason_for(course({"club": True}), facts(club=True)) == "club"  # V35 как было
    assert free_reason_for(course({"courses": ["a"]}), facts(courses=["a"], tests=[TEST], app=True)) == "course"
    assert free_reason_for(course({"club": True}), facts(tests=[TEST], app=True)) is None


def test_unlock_belongs_to_purchase_courses_only():
    assert free_reason_for(course(access_rule="pro"), facts(tests=[TEST], app=True)) is None
    assert free_reason_for(course(access_rule="free"), facts(tests=[TEST], app=True)) is None
    assert free_reason_for({"slug": "x", "access_rule": "purchase", "free_for": None}, facts(app=True)) is None


@pytest.mark.parametrize(
    "unlock",
    [
        {},  # нечего проверять — не «открыто всем»
        {"app": False},
        {"app": "yes"},
        {"ap": True, "app": True},  # опечатка в ключе: правило целиком не действует
        {"quiz": {"course": "zapusk-wwc"}},
        {"quiz": {"course": "zapusk-wwc", "lesson": "itogi-i-plan", "pass": 8}},
        {"quiz": {"course": "", "lesson": "itogi-i-plan"}},
        {"quiz": {"course": "zapusk-wwc", "lesson": 5}},
        {"quiz": "zapusk-wwc/itogi-i-plan"},
        {"quiz": None, "app": True},
        "app",
        ["app"],
        True,
        None,
    ],
)
def test_a_broken_rule_never_opens_the_course(unlock):
    assert unlock_spec({"unlock": unlock}) is None
    assert free_reason_for(course({"unlock": unlock}), facts(tests=[TEST], app=True)) is None


def test_the_spec_reads_the_documented_shape():
    assert unlock_spec(RULE) == UnlockSpec(quiz=TEST, app=True)
    assert unlock_spec({"unlock": {"app": True}}) == UnlockSpec(quiz=None, app=True)
    assert unlock_spec({}) is None and unlock_spec(None) is None
    assert course_unlock(course()) == UnlockSpec(quiz=TEST, app=True)
    assert course_unlock(course(access_rule="pro")) is None
    assert unlock_met(UnlockSpec(quiz=TEST, app=True), facts(tests=[TEST], app=True)) is True
    assert unlock_met(UnlockSpec(quiz=TEST, app=True), None) is False


# ---- the progress object ------------------------------------------------------------------------


def test_unlock_object_has_exactly_the_agreed_fields():
    spec = UnlockSpec(quiz=TEST, app=True)
    waiting = unlock_view(spec, facts(), quiz_course_title="Запуск WWC", eligible=True)
    assert waiting == {
        "quiz_passed": False,
        "app_installed": False,
        "quiz_url": "/academy/?course=zapusk-wwc&lesson=itogi-i-plan",
        "quiz_course_title": "Запуск WWC",
        "eligible": True,
    }
    half = unlock_view(spec, facts(tests=[TEST]), quiz_course_title="Запуск WWC", eligible=False)
    assert (half["quiz_passed"], half["app_installed"], half["eligible"]) == (True, False, False)
    assert unlock_view(spec, facts(app=True), quiz_course_title=None, eligible=True)["quiz_course_title"] is None


def test_a_condition_the_rule_does_not_have_counts_as_met():
    only_app = unlock_view(UnlockSpec(quiz=None, app=True), facts(), quiz_course_title=None, eligible=True)
    assert (only_app["quiz_passed"], only_app["quiz_url"], only_app["app_installed"]) == (True, None, False)
    only_quiz = unlock_view(UnlockSpec(quiz=TEST, app=False), facts(), quiz_course_title="Запуск", eligible=True)
    assert (only_quiz["quiz_passed"], only_quiz["app_installed"]) == (False, True)


def test_quiz_path_is_the_site_path_of_the_lesson():
    assert quiz_path("zapusk-wwc", "itogi-i-plan") == "/academy/?course=zapusk-wwc&lesson=itogi-i-plan"


# ---- app mark and the message --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "clean"),
    [("android", "android"), ("ios", "ios"), ("desktop", "desktop"), ("Android", None), ("windows", None),
     ("", None), (None, None), (5, None), (["ios"], None), ({"a": 1}, None)],
)
def test_platform_is_one_of_three_or_null(value, clean):
    assert clean_platform(value) == clean


def test_the_message_names_what_the_person_did():
    both = unlock_text("Алхимия капитала", UnlockSpec(quiz=TEST, app=True), "Запуск WWC")
    assert both == "🎁 Мини-курс «Алхимия капитала» открыт бесплатно: вы сдали тест «Запуск WWC» и поставили приложение WWC."
    assert unlock_text("М", UnlockSpec(quiz=None, app=True), None).endswith("вы поставили приложение WWC.")
    assert unlock_text("М", UnlockSpec(quiz=TEST, app=False), "Запуск WWC").endswith("вы сдали тест «Запуск WWC».")
    assert "сдали итоговый тест и поставили" in unlock_text("М", UnlockSpec(quiz=TEST, app=True), None)
    assert unlock_text("Курс", UnlockSpec(quiz=None, app=True), None, kind="course").startswith("🎁 Курс «Курс»")


# ---- the owner writes the rule ---------------------------------------------------------------------


def test_owner_can_write_the_unlock_rule():
    stored = parse_free_for({"club": True, "courses": ["online-start-4w"], **RULE})
    assert stored == {"club": True, "courses": ["online-start-4w"], "unlock": {"quiz": TEST_DICT, "app": True}}
    assert parse_free_for(RULE) == {"unlock": {"quiz": TEST_DICT, "app": True}}
    assert parse_free_for({"unlock": {"app": True}}) == {"unlock": {"app": True}}
    assert unlock_spec(parse_free_for(RULE)) == UnlockSpec(quiz=TEST, app=True)  # записанное читается тем же кодом
    # Без условий правило ничего не открывает — как club: false.
    assert parse_free_for({"unlock": {}}) is None
    assert parse_free_for({"unlock": {"app": False}}) is None
    assert parse_free_for({"unlock": None}) is None
    assert parse_free_for({"club": False, "unlock": {"app": False}}) is None


@pytest.mark.parametrize(
    "raw",
    [
        {"unlock": "app"},
        {"unlock": {"apps": True}},
        {"unlock": {"app": "true"}},
        {"unlock": {"quiz": "zapusk-wwc"}},
        {"unlock": {"quiz": {"course": "zapusk-wwc"}}},
        {"unlock": {"quiz": {"course": "zapusk-wwc", "lesson": "itogi-i-plan", "pass": 8}}},
        {"unlock": {"quiz": {"course": "Не slug", "lesson": "itogi-i-plan"}}},
        {"unlock": {"quiz": {"course": "zapusk-wwc", "lesson": None}}},
        {"unlock": {"quiz": {"course": "a" * 61, "lesson": "itogi-i-plan"}}},
        {"lock": True},
    ],
)
def test_owner_mistakes_in_the_rule_are_refused(raw):
    with pytest.raises(ValueError, match="free_for"):
        parse_free_for(raw)


# ---- unlock.members: only the club and the course students may unlock (19.10.2026) --------------------

MEMBERS_RULE = {
    "unlock": {
        "quiz": TEST_DICT,
        "app": True,
        "members": {"club": True, "courses": ["online-start-4w"]},
    }
}
STUDENT = ("online-start-4w",)


def test_only_members_unlock_and_only_with_both_conditions():
    mini = course(MEMBERS_RULE)
    # Участник клуба / ученик «Онлайн-старта»: тест + приложение — открыто.
    assert free_reason_for(mini, facts(club=True, tests=[TEST], app=True)) == "unlock"
    assert free_reason_for(mini, facts(courses=STUDENT, tests=[TEST], app=True)) == "unlock"
    assert free_reason_for(mini, facts(club=True, courses=STUDENT, tests=[TEST], app=True)) == "unlock"
    # Без одного из условий участник не открывает.
    assert free_reason_for(mini, facts(club=True, tests=[TEST])) is None
    assert free_reason_for(mini, facts(club=True, app=True)) is None
    assert free_reason_for(mini, facts(courses=STUDENT)) is None
    # Не участник — платит на общих основаниях, хоть тест и приложение есть (PRO сюда не входит).
    assert free_reason_for(mini, facts(tests=[TEST], app=True)) is None
    assert free_reason_for(mini, facts(courses=("other-course",), tests=[TEST], app=True)) is None
    assert free_reason_for(mini, None) is None


def test_members_with_one_key_means_that_key_only():
    club_only = course({"unlock": {"app": True, "members": {"club": True}}})
    courses_only = course({"unlock": {"app": True, "members": {"courses": ["online-start-4w"]}}})
    assert free_reason_for(club_only, facts(club=True, app=True)) == "unlock"
    assert free_reason_for(club_only, facts(courses=STUDENT, app=True)) is None
    assert free_reason_for(courses_only, facts(courses=STUDENT, app=True)) == "unlock"
    assert free_reason_for(courses_only, facts(club=True, app=True)) is None
    off = course({"unlock": {"app": True, "members": {"club": False, "courses": ["online-start-4w"]}}})
    assert free_reason_for(off, facts(club=True, app=True)) is None  # club: false — клуб не в счёт


def test_top_level_club_and_courses_of_other_courses_work_as_before():
    both = course({"club": True, **MEMBERS_RULE})
    assert free_reason_for(both, facts(club=True)) == "club"  # верхний club — без теста и приложения
    assert free_reason_for(both, facts(tests=[TEST], app=True)) is None  # не участник
    assert free_reason_for(course({"club": True}), facts(club=True)) == "club"
    assert free_reason_for(course({"courses": ["online-start-4w"]}), facts(courses=STUDENT)) == "course"
    # unlock без members — для всех, как в первой версии V39.
    assert free_reason_for(course(), facts(tests=[TEST], app=True)) == "unlock"


@pytest.mark.parametrize(
    "members",
    [
        {},  # ни одного ключа
        {"club": False},
        {"courses": []},
        {"club": "yes", "courses": ["a"]},
        {"club": True, "courses": "online-start-4w"},
        {"courses": [5]},
        {"courses": [""]},
        {"club": True, "extra": 1},
        {"clubs": True},
        None,
        True,
        ["online-start-4w"],
        "club",
    ],
)
def test_broken_members_open_nothing(members):
    rule = {"unlock": {"quiz": TEST_DICT, "app": True, "members": members}}
    assert unlock_spec(rule) is None
    everyone = facts(club=True, courses=STUDENT, tests=[TEST], app=True)
    assert free_reason_for(course(rule), everyone) is None


def test_members_are_read_into_the_spec():
    spec = unlock_spec(MEMBERS_RULE)
    assert spec == UnlockSpec(quiz=TEST, app=True, members=Members(club=True, courses=frozenset(STUDENT)))
    assert unlock_spec(RULE) == UnlockSpec(quiz=TEST, app=True, members=None)
    assert unlock_member(spec, facts()) is False
    assert unlock_member(spec, facts(club=True)) is True
    assert unlock_member(spec, facts(courses=STUDENT)) is True
    assert unlock_member(unlock_spec(RULE), facts()) is True  # без members — все
    assert unlock_member(spec, None) is False


@pytest.mark.asyncio
async def test_a_person_outside_members_gets_no_unlock_object():
    from app.academy.service import AcademyViewer

    viewer = AcademyViewer(telegram_user_id=1, is_preview_admin=False, partner_paid=True)  # PRO без клуба и курса
    mini = course(MEMBERS_RULE)
    # Решение принимается до запросов в базу: соединение не нужно.
    assert await unlock_progress(None, "whieda", viewer, mini, facts(tests=[TEST], app=True)) is None
    assert await unlock_progress(None, "whieda", viewer, mini, None) is None


def test_owner_can_write_members():
    stored = parse_free_for(MEMBERS_RULE)
    assert stored == {
        "unlock": {"quiz": TEST_DICT, "app": True, "members": {"club": True, "courses": ["online-start-4w"]}}
    }
    assert unlock_spec(stored) == unlock_spec(MEMBERS_RULE)  # записанное читается тем же кодом
    only_club = parse_free_for({"unlock": {"app": True, "members": {"club": True, "courses": []}}})
    assert only_club == {"unlock": {"app": True, "members": {"club": True}}}
    twice = parse_free_for({"unlock": {"app": True, "members": {"courses": ["a", "a", "b"]}}})
    assert twice["unlock"]["members"] == {"courses": ["a", "b"]}
    assert parse_free_for({"unlock": {"app": True, "members": None}}) == {"unlock": {"app": True}}
    assert parse_free_for({"unlock": {"app": False}}) is None  # как и было


@pytest.mark.parametrize(
    "unlock",
    [
        {"app": True, "members": {}},
        {"app": True, "members": {"club": False}},
        {"app": True, "members": {"club": "true"}},
        {"app": True, "members": {"courses": "online-start-4w"}},
        {"app": True, "members": {"courses": ["Не slug"]}},
        {"app": True, "members": {"courses": [None]}},
        {"app": True, "members": {"club": True, "extra": 1}},
        {"app": True, "members": "club"},
        {"app": True, "members": ["club"]},
        {"members": {"club": True}},  # членство само ничего не проверяет
        {"app": False, "members": {"club": True}},
    ],
)
def test_owner_mistakes_in_members_are_refused(unlock):
    with pytest.raises(ValueError, match="free_for.unlock"):
        parse_free_for({"unlock": unlock})
