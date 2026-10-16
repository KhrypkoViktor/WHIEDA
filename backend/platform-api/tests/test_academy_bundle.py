"""Academy bundle from Obsidian: lesson pictures travel inside the bundle (Academy v2)."""

from __future__ import annotations

import base64
import importlib.util
import json
import re
from pathlib import Path

import pytest

BUILDER = Path(__file__).resolve().parents[1] / "scripts" / "academy" / "build_bundle.py"
PNG = b"\x89PNG\r\n\x1a\n" + b"picture"


def _builder():
    pytest.importorskip("markdown")
    spec = importlib.util.spec_from_file_location("academy_build_bundle", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _folder(tmp_path: Path, *, with_image: bool = True) -> Path:
    (tmp_path / "img").mkdir()
    if with_image:
        (tmp_path / "img" / "shema.png").write_bytes(PNG)
    (tmp_path / "01.md").write_text(
        "# Урок 1. Вход\n\n**Результат урока:** понятен вход\n\nТекст.\n\n![Схема](img/shema.png)\n",
        encoding="utf-8",
    )
    (tmp_path / "_academy_course.json").write_text(
        json.dumps(
            {
                "slug": "zapusk",
                "title": "Запуск",
                "modules": [{"title": "Старт", "lessons": [{"file": "01.md", "slug": "vhod"}]}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_pictures_are_embedded_and_referenced_by_their_bundle_path(tmp_path):
    bundle = _builder().build(_folder(tmp_path))
    [lesson] = bundle["lessons"]
    assert 'src="img/shema.png"' in lesson["body_html"]
    assert "/academy/img/" not in lesson["body_html"]
    assert bundle["media"] == {
        "img/shema.png": {"mime": "image/png", "data_b64": base64.b64encode(PNG).decode("ascii")}
    }
    assert lesson["module_title"] == "Старт"


def test_missing_picture_stops_the_build(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        _builder().build(_folder(tmp_path, with_image=False))
    assert "img/shema.png" in str(stopped.value)


# ---- mini-courses (V35): the test of a lesson and code blocks ------------------------------------

QUIZ = {
    "pass": 1,
    "questions": [
        {"q": "Что защищало Пателей?", "ref": "Разбор, семья Пателей",
         "options": [
             {"text": "Высокие цены", "correct": False, "hint": "Не совсем. Перечитайте: Разбор, семья Пателей."},
             {"text": "Низкие постоянные расходы", "correct": True, "hint": "Верно."},
         ]},
    ],
}


def _mini_course(tmp_path: Path, quiz_json: str) -> Path:
    """Фикстура мини-курса из двух уроков: тест после чек-листа (как в 01_Dhando) и промпт в ```."""
    (tmp_path / "00.md").write_text(
        "# Как проходить курс\n\n**Время:** 5 минут.\n\nПрочитайте урок, сделайте упражнение.\n",
        encoding="utf-8",
    )
    (tmp_path / "01.md").write_text(
        "# Орёл или решка\n\n**Результат урока:** потолок потерь\n\n## Разбор\n\nТекст.\n\n"
        "## Чек-лист\n\n- [ ] Записал «орёл»\n- [ ] Записал «решку»\n\n"
        "## Промпт для нейросети\n\n```\nТы — консультант. Сделай по шагам: <идея>\n```\n\n"
        f"## Тест\n\n```quiz\n{quiz_json}\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "_academy_course.json").write_text(
        json.dumps(
            {
                "slug": "mini-dhando", "title": "Алхимия капитала", "access": "purchase",
                "modules": [{"title": "Мини-курс", "lessons": [
                    {"file": "00.md", "slug": "kak-prokhodit", "short": "Как проходить"},
                    {"file": "01.md", "slug": "orel-reshka", "short": "Орёл или решка"},
                ]}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_lesson_test_goes_to_quiz_and_never_into_the_body(tmp_path):
    bundle = _builder().build(_mini_course(tmp_path, json.dumps(QUIZ, ensure_ascii=False)))
    first, second = bundle["lessons"]
    assert first["quiz"] is None
    assert second["quiz"]["pass"] == 1
    assert [q["id"] for q in second["quiz"]["questions"]] == ["q1"]
    assert [o["id"] for o in second["quiz"]["questions"][0]["options"]] == ["a", "b"]
    body = second["body_html"]
    assert "quiz" not in body and "Не совсем" not in body and "correct" not in body
    # Тест после чек-листа не съеден разделом чек-листа; чек-лист — отдельно.
    assert second["checklist"] == ["Записал «орёл»", "Записал «решку»"]
    # Промпт — блок кода, экранированный, и очиститель сайта его пропускает.
    assert "<pre><code>Ты — консультант. Сделай по шагам: &lt;идея&gt;" in body
    from app.academy.content import sanitize_html

    assert "<pre><code>Ты — консультант." in sanitize_html(body)
    assert bundle["course"]["access_rule"] == "purchase"


def test_broken_test_stops_the_build_with_the_file_name(tmp_path):
    broken = json.loads(json.dumps(QUIZ))
    broken["questions"][0]["options"][0]["correct"] = True
    with pytest.raises(SystemExit) as stopped:
        _builder().build(_mini_course(tmp_path, json.dumps(broken, ensure_ascii=False)))
    assert "01.md" in str(stopped.value) and "ровно один" in str(stopped.value)


def test_bare_address_in_a_lesson_becomes_a_link_in_a_new_tab(tmp_path):
    """Владелец 16.10.2026: ссылки в уроке активны — и в уроках из Obsidian, как у автора на сайте."""
    folder = _mini_course(tmp_path, json.dumps(QUIZ, ensure_ascii=False))
    (folder / "00.md").write_text("# Как проходить\n\nПамятка: https://wwc.best/academy/.\n", encoding="utf-8")
    first = _builder().build(folder)["lessons"][0]
    assert '<a href="https://wwc.best/academy/" target="_blank" rel="noopener">https://wwc.best/academy/</a>.' in first["body_html"]
    from app.academy.content import sanitize_html

    assert 'target="_blank"' in sanitize_html(first["body_html"]) and 'rel="noopener noreferrer"' in sanitize_html(first["body_html"])


# ---- раскрывающиеся блоки (16.10.2026) ------------------------------------------------------------

FOLD_LESSON = (
    "# Урок 1. Вход\n\n**Результат урока:** понятен вход\n\n## Разбор\n\nТекст.\n\n"
    ":::раскрыть Подробный расчёт\n"
    "Абзац: https://wwc.best/academy/\n\n"
    "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
    "```\nпромпт\n```\n\n"
    "![Схема](img/shema.png)\n"
    ":::\n\n"
    "После блока.\n"
)


def test_fold_in_a_lesson_is_the_same_html_as_in_the_cabinet(tmp_path):
    folder = _folder(tmp_path)
    (folder / "01.md").write_text(FOLD_LESSON, encoding="utf-8")
    bundle = _builder().build(folder)
    body = bundle["lessons"][0]["body_html"]
    from app.academy.content import render_markdown, sanitize_html

    fold = re.search(r'<details class="ac-fold">.*?</details>', body, re.S).group(0)
    assert fold.startswith('<details class="ac-fold"><summary>Подробный расчёт</summary><div class="ac-fold__body">')
    assert "<table>" in fold and "<pre><code>промпт" in fold and 'src="img/shema.png"' in fold
    # Тот же блок, что в кабинете автора, и чтение урока (вторая очистка) его не меняет.
    assert fold in render_markdown(FOLD_LESSON, folds=True)
    assert fold in sanitize_html(body)
    # Картинка из блока едет в бандле, ссылка в нём — одна новая вкладка.
    assert "img/shema.png" in bundle["media"]
    assert fold.count('target="_blank"') == 1
    assert body.endswith("<p>После блока.</p>")


@pytest.mark.parametrize("inside", ["текст после чек-листа", "### Подробнее\n\nтекст после чек-листа"])
def test_fold_right_after_the_checklist_is_not_swallowed(tmp_path, inside):
    # Чек-лист срезается до следующего заголовка — и до строки блока: блок после него цел.
    folder = _folder(tmp_path)
    (folder / "01.md").write_text(
        "# Урок 1. Вход\n\n## Разбор\n\nТекст.\n\n## Чек-лист\n\n- [ ] Сделал раз\n- [ ] Сделал два\n\n"
        f":::раскрыть Если не получилось\n{inside}\n:::\n\n## Дальше\n\nКонец.\n",
        encoding="utf-8",
    )
    lesson = _builder().build(folder)["lessons"][0]
    assert lesson["checklist"] == ["Сделал раз", "Сделал два"]
    body = lesson["body_html"]
    assert '<details class="ac-fold"><summary>Если не получилось</summary>' in body
    assert "<p>текст после чек-листа</p></div></details>" in body
    assert "Чек-лист" not in body and "Сделал раз" not in body and ":::" not in body
    assert body.endswith("<h2>Дальше</h2>\n<p>Конец.</p>")


def test_checklist_inside_a_fold_keeps_the_fold_closed(tmp_path):
    folder = _folder(tmp_path)
    (folder / "01.md").write_text(
        "# Урок 1. Вход\n\n:::раскрыть Проверьте себя\n### Чек-лист\n\n- [ ] Сделал\n:::\n\nПосле.\n",
        encoding="utf-8",
    )
    lesson = _builder().build(folder)["lessons"][0]
    assert lesson["checklist"] == ["Сделал"]
    assert '<details class="ac-fold"><summary>Проверьте себя</summary>' in lesson["body_html"]
    assert ":::" not in lesson["body_html"] and lesson["body_html"].endswith("<p>После.</p>")


def test_broken_fold_stops_the_build_with_the_file_name(tmp_path):
    folder = _folder(tmp_path)
    (folder / "01.md").write_text("# Урок 1. Вход\n\n:::раскрыть Без конца\nтекст\n", encoding="utf-8")
    with pytest.raises(SystemExit) as stopped:
        _builder().build(folder)
    assert "01.md" in str(stopped.value) and "не закрыт" in str(stopped.value)


def test_test_inside_a_fold_stops_the_build(tmp_path):
    folder = _mini_course(tmp_path, json.dumps(QUIZ, ensure_ascii=False))
    source = (folder / "01.md").read_text(encoding="utf-8")
    (folder / "01.md").write_text(source.replace("## Тест\n\n", ":::раскрыть Тест\n") + ":::\n", encoding="utf-8")
    with pytest.raises(SystemExit) as stopped:
        _builder().build(folder)
    assert "01.md" in str(stopped.value) and "тест не может быть внутри раскрывающегося блока" in str(stopped.value)
