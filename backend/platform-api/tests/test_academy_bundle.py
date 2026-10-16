"""Academy bundle from Obsidian: lesson pictures travel inside the bundle (Academy v2)."""

from __future__ import annotations

import base64
import importlib.util
import json
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
