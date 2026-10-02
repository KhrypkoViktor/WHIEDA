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
