"""Academy: a lesson's test with hints (V35, 16.10.2026). No I/O.

The author writes the test at the end of the lesson Markdown as a fenced block
with the language ``quiz`` (``app.academy.content.split_quiz`` cuts it out
before rendering)::

    {"pass": 3, "questions": [
      {"id": "q1", "q": "Вопрос?", "ref": "Разбор, кейс Пателей",
       "options": [
         {"id": "a", "text": "…", "correct": false, "hint": "Не совсем. … Перечитайте: …"},
         {"id": "b", "text": "…", "correct": true,  "hint": "Верно. …"}]}]}

Rules: 1–20 questions, 2–6 options, exactly one ``correct`` per question, every
option has a ``hint`` (it may be empty only on the correct one), ``pass`` from 1
to the number of questions. Missing ids become ``q1..qN`` and ``a..f``. Unknown
keys are refused — a typo («corect», «hnt») must not silently drop a field.
Texts are plain text (the site inserts them as text, not HTML).

A pause between attempts (V39, 19.10.2026): the optional top-level key
``retry_after_min`` — a whole number of minutes, 0–1440 (default 0 = no pause, as in
V35; it is kept in the stored form only when above 0). While the test is not passed
and the last attempt was less than that long ago, ``POST …/quiz`` answers 429
``quiz_cooldown`` with ``retry_at`` and writes nothing (``retry_at`` here: the pure rule).
After a passed attempt there are no pauses. ``public_quiz`` shows the number to the site.

The student sees the test without ``correct`` and ``hint`` (``public_quiz``); an
answer (``grade_quiz``) returns, per question, right or wrong and the hint of the
chosen option. An unanswered question is wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

MAX_QUESTIONS = 20
MIN_OPTIONS, MAX_OPTIONS = 2, 6
MAX_QUESTION_CHARS = 1000
MAX_OPTION_CHARS = 1000
MAX_HINT_CHARS = 2000
MAX_REF_CHARS = 300
OPTION_IDS = "abcdef"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}\Z")
MAX_RETRY_AFTER_MIN = 1440  # сутки
_QUIZ_KEYS = {"pass", "questions", "retry_after_min"}
_QUESTION_KEYS = {"id", "q", "ref", "options"}
_OPTION_KEYS = {"id", "text", "correct", "hint"}


class QuizError(ValueError):
    """A test the author wrote does not fit the rules; the message says what to fix (Russian)."""


def _text(value: Any, *, where: str, field: str, limit: int, required: bool) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise QuizError(f"{where}: «{field}» должно быть строкой")
    text = value.strip()
    if required and not text:
        raise QuizError(f"{where}: пустое поле «{field}»")
    if len(text) > limit:
        raise QuizError(f"{where}: «{field}» длиннее {limit} символов")
    return text


def _id(value: Any, *, where: str, default: str, taken: set[str]) -> str:
    if value is None:
        ident = default
    elif not isinstance(value, str) or not _ID_RE.match(value):
        raise QuizError(f"{where}: id — латиница, цифры, «-» или «_», до 32 символов")
    else:
        ident = value
    if ident in taken:
        raise QuizError(f"{where}: id «{ident}» повторяется")
    taken.add(ident)
    return ident


def _unknown(item: dict[str, Any], allowed: set[str], where: str) -> None:
    extra = sorted(set(item) - allowed)
    if extra:
        raise QuizError(f"{where}: неизвестные поля {', '.join(extra)}")


def validate_quiz(raw: Any) -> dict[str, Any]:
    """The author's test → the stored form (ids filled in, texts trimmed); QuizError otherwise."""
    if not isinstance(raw, dict):
        raise QuizError("тест — JSON-объект вида {\"pass\": …, \"questions\": […]}")
    _unknown(raw, _QUIZ_KEYS, "тест")
    questions = raw.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= MAX_QUESTIONS:
        raise QuizError(f"тест: «questions» — список из 1–{MAX_QUESTIONS} вопросов")
    out_questions = []
    question_ids: set[str] = set()
    for number, question in enumerate(questions, start=1):
        where = f"вопрос {number}"
        if not isinstance(question, dict):
            raise QuizError(f"{where}: должен быть объектом")
        _unknown(question, _QUESTION_KEYS, where)
        ident = _id(question.get("id"), where=where, default=f"q{number}", taken=question_ids)
        text = _text(question.get("q"), where=where, field="q", limit=MAX_QUESTION_CHARS, required=True)
        ref = _text(question.get("ref"), where=where, field="ref", limit=MAX_REF_CHARS, required=False)
        options = question.get("options")
        if not isinstance(options, list) or not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
            raise QuizError(f"{where}: «options» — список из {MIN_OPTIONS}–{MAX_OPTIONS} вариантов")
        out_options = []
        option_ids: set[str] = set()
        for index, option in enumerate(options):
            spot = f"{where}, вариант {index + 1}"
            if not isinstance(option, dict):
                raise QuizError(f"{spot}: должен быть объектом")
            _unknown(option, _OPTION_KEYS, spot)
            correct = option.get("correct", False)
            if not isinstance(correct, bool):
                raise QuizError(f"{spot}: «correct» — true или false")
            if "hint" not in option:
                raise QuizError(f"{spot}: нет подсказки «hint»")
            hint = _text(option["hint"], where=spot, field="hint", limit=MAX_HINT_CHARS, required=False)
            if not hint and not correct:
                raise QuizError(f"{spot}: у неверного варианта нужна подсказка «hint»")
            out_options.append(
                {
                    "id": _id(option.get("id"), where=spot, default=OPTION_IDS[index], taken=option_ids),
                    "text": _text(option.get("text"), where=spot, field="text", limit=MAX_OPTION_CHARS, required=True),
                    "correct": correct,
                    "hint": hint,
                }
            )
        right = sum(1 for option in out_options if option["correct"])
        if right != 1:
            raise QuizError(f"{where}: верный вариант должен быть ровно один, сейчас {right}")
        out_questions.append({"id": ident, "q": text, "ref": ref, "options": out_options})
    threshold = raw.get("pass")
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 1 <= threshold <= len(out_questions):
        raise QuizError(f"тест: «pass» — целое число от 1 до {len(out_questions)} (число вопросов)")
    stored: dict[str, Any] = {"pass": threshold, "questions": out_questions}
    pause = raw.get("retry_after_min")
    if pause is not None:
        if isinstance(pause, bool) or not isinstance(pause, int) or not 0 <= pause <= MAX_RETRY_AFTER_MIN:
            raise QuizError(f"тест: «retry_after_min» — целое число минут от 0 до {MAX_RETRY_AFTER_MIN}")
        if pause:
            stored["retry_after_min"] = pause
    return stored


def retry_after_min(quiz: dict[str, Any] | None) -> int:
    """The pause (minutes) between attempts of a stored test; 0 — none. A stored value that does not
    fit the rules (written by hand around the validator) counts as no pause."""
    pause = (quiz or {}).get("retry_after_min")
    if isinstance(pause, bool) or not isinstance(pause, int) or not 0 < pause <= MAX_RETRY_AFTER_MIN:
        return 0
    return pause


def retry_at(
    pause_min: int, last_attempt_at: datetime | None, *, passed: bool, now: datetime
) -> datetime | None:
    """When the next attempt is allowed; None — now. A pause only follows a failed attempt of a test
    that was never passed: ``passed`` (an attempt of this student passed) lifts it for good."""
    if passed or pause_min <= 0 or last_attempt_at is None:
        return None
    moment = last_attempt_at + timedelta(minutes=pause_min)
    return moment if moment > now else None


def public_quiz(quiz: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the student sees before answering: no ``correct``, no ``hint``."""
    if not quiz:
        return None
    return {
        "pass": int(quiz["pass"]),
        "retry_after_min": retry_after_min(quiz),
        "questions": [
            {
                "id": question["id"],
                "q": question["q"],
                "ref": question.get("ref") or "",
                "options": [{"id": option["id"], "text": option["text"]} for option in question["options"]],
            }
            for question in quiz["questions"]
        ],
    }


@dataclass(frozen=True)
class QuizResult:
    score: int
    passed: bool
    answers: dict[str, str]  # only questions of this test, as given
    results: list[dict[str, Any]]


def grade_quiz(quiz: dict[str, Any], answers: dict[str, Any]) -> QuizResult:
    """Score one attempt. Answers to unknown questions are dropped; an unknown option
    or no answer is wrong (no hint then — the site points to ``ref``)."""
    given = {
        str(key): value for key, value in (answers or {}).items() if isinstance(value, str) and len(value) <= 32
    }
    kept: dict[str, str] = {}
    results = []
    score = 0
    for question in quiz["questions"]:
        choice = given.get(question["id"])
        option = next((item for item in question["options"] if item["id"] == choice), None)
        correct = bool(option and option["correct"])
        if choice is not None:
            kept[question["id"]] = choice
        score += int(correct)
        results.append(
            {
                "id": question["id"],
                "answer": option["id"] if option else None,
                "correct": correct,
                "hint": (option.get("hint") or "") if option else "",
            }
        )
    return QuizResult(score=score, passed=score >= int(quiz["pass"]), answers=kept, results=results)
