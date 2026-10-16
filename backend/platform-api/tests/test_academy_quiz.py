"""Academy V35: the lesson's test — the ```quiz block in Markdown, its rules, what the
student sees and how an attempt is scored (pure functions)."""

from __future__ import annotations

import json

import pytest

from app.academy.content import render_markdown, split_quiz
from app.academy.quiz import QuizError, grade_quiz, public_quiz, validate_quiz


def question(text="Вопрос?", *, right=1, count=3, ref="Разбор", ids=False, hints=True):
    options = []
    for index in range(count):
        option = {"text": f"Вариант {index + 1}", "correct": index == right}
        if hints:
            option["hint"] = "Верно." if index == right else f"Не совсем. Перечитайте: {ref}."
        if ids:
            option["id"] = f"o{index}"
        options.append(option)
    return {"q": text, "ref": ref, "options": options}


def quiz(*questions, threshold=None):
    items = list(questions) or [question()]
    return {"pass": threshold if threshold is not None else len(items), "questions": items}


def lesson_md(block: str) -> str:
    return f"# Урок\n\nТекст урока.\n\n## Тест\n\n```quiz\n{block}\n```\n\nПосле теста.\n"


# ---- split_quiz -------------------------------------------------------------------


def test_block_is_cut_out_and_ids_are_filled_in():
    raw = quiz(question("Первый?"), question("Второй?", right=0), threshold=1)
    body, parsed = split_quiz(lesson_md(json.dumps(raw, ensure_ascii=False)))
    assert "```" not in body and "quiz" not in body and '"correct"' not in body
    assert "Текст урока." in body and "После теста." in body
    assert parsed["pass"] == 1
    assert [q["id"] for q in parsed["questions"]] == ["q1", "q2"]
    assert [o["id"] for o in parsed["questions"][0]["options"]] == ["a", "b", "c"]
    assert [o["correct"] for o in parsed["questions"][1]["options"]] == [True, False, False]
    # Рендер урока после вырезания: ни ответов, ни подсказок в HTML.
    html = render_markdown(body)
    assert "Не совсем" not in html and "Верно." not in html


def test_lesson_without_a_block_is_left_as_it_is():
    text = "# Урок\n\n```\nПромпт для нейросети\n```\n"
    assert split_quiz(text) == (text, None)
    assert split_quiz(None) == ("", None)


def test_own_ids_are_kept_and_must_be_unique():
    raw = quiz({**question(ids=True), "id": "patel"})
    _, parsed = split_quiz(lesson_md(json.dumps(raw)))
    assert parsed["questions"][0]["id"] == "patel"
    assert [o["id"] for o in parsed["questions"][0]["options"]] == ["o0", "o1", "o2"]
    twice = quiz({**question(), "id": "x"}, {**question(), "id": "x"})
    with pytest.raises(QuizError, match="повторяется"):
        validate_quiz(twice)


def test_broken_json_says_where():
    with pytest.raises(QuizError, match=r"не JSON .*строка 9"):
        split_quiz(lesson_md('{"pass": 1, "questions": [\n  {"q": "x",}\n]}'))


def test_two_correct_options_are_refused():
    raw = quiz(question())
    raw["questions"][0]["options"][0]["correct"] = True
    with pytest.raises(QuizError, match="ровно один, сейчас 2"):
        split_quiz(lesson_md(json.dumps(raw)))
    raw["questions"][0]["options"][0]["correct"] = False
    raw["questions"][0]["options"][1]["correct"] = False
    with pytest.raises(QuizError, match="ровно один, сейчас 0"):
        validate_quiz(raw)


def test_every_option_needs_a_hint_empty_only_on_the_right_one():
    with pytest.raises(QuizError, match="нет подсказки"):
        split_quiz(lesson_md(json.dumps(quiz(question(hints=False)))))
    raw = quiz(question())
    raw["questions"][0]["options"][1]["hint"] = ""  # верный вариант: можно пусто
    assert validate_quiz(raw)["questions"][0]["options"][1]["hint"] == ""
    raw["questions"][0]["options"][0]["hint"] = "  "
    with pytest.raises(QuizError, match="у неверного варианта нужна подсказка"):
        validate_quiz(raw)


@pytest.mark.parametrize(
    "raw, message",
    [
        ([], "JSON-объект"),
        ({"pass": 1, "questions": []}, "1–20"),
        ({"pass": 1, "questions": [question()] * 21}, "1–20"),
        (quiz(question(count=1)), "2–6"),
        (quiz(question(count=7)), "2–6"),
        (quiz(question(), threshold=0), "«pass»"),
        (quiz(question(), threshold=2), "«pass»"),
        (quiz(question(), threshold=True), "«pass»"),
        ({"questions": [question()]}, "«pass»"),
        (quiz(question(text="  ")), "пустое поле «q»"),
        ({**quiz(), "passs": 1}, "неизвестные поля passs"),
        (quiz({**question(), "refs": "x"}), "неизвестные поля refs"),
    ],
)
def test_rules_of_the_test(raw, message):
    with pytest.raises(QuizError, match=message):
        validate_quiz(raw)


def test_unknown_option_key_and_non_bool_correct_are_refused():
    raw = quiz(question())
    raw["questions"][0]["options"][0]["corect"] = True
    with pytest.raises(QuizError, match="неизвестные поля corect"):
        validate_quiz(raw)
    raw = quiz(question())
    raw["questions"][0]["options"][1]["correct"] = "true"
    with pytest.raises(QuizError, match="true или false"):
        validate_quiz(raw)


def test_unclosed_and_second_block_are_refused():
    with pytest.raises(QuizError, match="не закрыт"):
        split_quiz("Текст\n\n```quiz\n{}\n")
    block = json.dumps(quiz())
    with pytest.raises(QuizError, match="второй блок"):
        split_quiz(lesson_md(block) + f"\n~~~quiz\n{block}\n~~~\n")


def test_tilde_fence_and_crlf_work():
    block = json.dumps(quiz())
    body, parsed = split_quiz(f"Текст\r\n\r\n~~~quiz\r\n{block}\r\n~~~\r\nДальше\r\n")
    assert parsed is not None and "~~~" not in body and "Дальше" in body


def test_stored_form_passes_the_check_again():
    stored = validate_quiz(quiz(question(), question(right=2)))
    assert validate_quiz(stored) == stored


# ---- what the student sees, how an attempt is scored ----------------------------------------


def test_public_quiz_has_no_answers_and_no_hints():
    stored = validate_quiz(quiz(question(), question(right=0), threshold=1))
    shown = public_quiz(stored)
    assert shown["pass"] == 1
    assert shown["questions"][0] == {
        "id": "q1", "q": "Вопрос?", "ref": "Разбор",
        "options": [{"id": "a", "text": "Вариант 1"}, {"id": "b", "text": "Вариант 2"}, {"id": "c", "text": "Вариант 3"}],
    }
    dumped = json.dumps(shown, ensure_ascii=False)
    assert "correct" not in dumped and "hint" not in dumped and "Не совсем" not in dumped
    assert public_quiz(None) is None


def test_attempt_scores_hints_and_unanswered_is_wrong():
    stored = validate_quiz(quiz(question(), question(right=0), question(right=2), threshold=2))
    result = grade_quiz(stored, {"q1": "b", "q2": "b", "zzz": "a"})
    assert (result.score, result.passed) == (1, False)
    assert result.results == [
        {"id": "q1", "answer": "b", "correct": True, "hint": "Верно."},
        {"id": "q2", "answer": "b", "correct": False, "hint": "Не совсем. Перечитайте: Разбор."},
        {"id": "q3", "answer": None, "correct": False, "hint": ""},
    ]
    assert result.answers == {"q1": "b", "q2": "b"}  # чужие вопросы не пишутся
    passed = grade_quiz(stored, {"q1": "b", "q2": "a", "q3": "x"})
    assert (passed.score, passed.passed) == (2, True)
    assert passed.results[2] == {"id": "q3", "answer": None, "correct": False, "hint": ""}
