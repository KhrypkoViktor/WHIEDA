"""Academy v2: author markdown → safe HTML; media references → signed links."""

from __future__ import annotations

import json
import re

import pytest

from app.academy.content import FoldError, media_refs, render_markdown, resolve_media, sanitize_html, split_quiz
from app.academy.quiz import QuizError

MEDIA_ID = "0f8fad5b-d9cb-469f-a165-70867728950e"

XSS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    '<a href="javascript:alert(1)">x</a>',
    '<a href="JaVaScRiPt:alert(1)">x</a>',
    '<a href="https://wwc.best" onclick="alert(1)">x</a>',
    "<iframe src=https://evil.example></iframe>",
    "<svg onload=alert(1)><circle/></svg>",
    '<p style="background:url(javascript:alert(1))">x</p>',
    '<img src="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">',
    "<math><mtext><table><mglyph><style><img src=x onerror=alert(1)></style></mglyph></table></mtext></math>",
    '<form action="https://evil.example"><input name=x><button>go</button></form>',
    '<object data="x.swf"></object><embed src="x.swf">',
    '<base href="https://evil.example/">',
    '<meta http-equiv="refresh" content="0;url=https://evil.example">',
    '<div onmouseover="alert(1)">x</div>',
    "<style>body{display:none}</style>",
]


@pytest.mark.parametrize("payload", XSS)
def test_sanitizer_cuts_script_vectors(payload):
    for html in (sanitize_html(payload), render_markdown(payload)):
        lowered = html.lower()
        for marker in ("<script", "onerror", "onclick", "onload", "onmouseover", "javascript:", "<iframe",
                       "<svg", "style=", "data:text", "<form", "<object", "<embed", "<base", "<meta", "<style"):
            assert marker not in lowered, (payload, html)


def test_markdown_link_with_script_scheme_loses_the_href():
    html = render_markdown("[нажми](javascript:alert(1))")
    assert "javascript" not in html.lower()
    assert "нажми" in html


def test_allowed_markup_survives():
    md = (
        "## Неделя 1\n\n"
        "Текст **жирный** и *курсив*, `код`.\n\n"
        "- раз\n- два\n\n"
        "1. первый\n2. второй\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "```\nprint(1)\n```\n\n"
        "[Сайт](https://wwc.best/academy/)\n\n"
        f"![Схема](media:{MEDIA_ID})\n"
    )
    html = render_markdown(md)
    for fragment in ("<h2>Неделя 1</h2>", "<strong>жирный</strong>", "<em>курсив</em>", "<code>код</code>",
                     "<ul>", "<ol>", "<table>", "<td>1</td>", "<pre><code>", f'src="media:{MEDIA_ID}"',
                     'alt="Схема"'):
        assert fragment in html, (fragment, html)
    assert 'href="https://wwc.best/academy/"' in html
    assert 'rel="noopener noreferrer"' in html


def test_legacy_bundle_html_keeps_lazy_images_new_tab_links_and_placeholders():
    legacy = (
        '<p>{{цены}}</p><img loading="lazy" alt="x" src="/academy/img/a.png">'
        '<a href="https://wwc.best" target="_blank" rel="noopener">wwc</a>'
    )
    html = sanitize_html(legacy)
    assert "<p>{{цены}}</p>" in html
    assert 'src="/academy/img/a.png"' in html
    assert 'target="_blank"' in html
    assert 'target="_top"' not in sanitize_html('<a href="https://x.example" target="_top">x</a>')


def test_media_refs_and_resolution():
    other = "11111111-2222-3333-4444-555555555555"
    html = render_markdown(f"![a](media:{MEDIA_ID})\n\n[файл](media:{other})")
    assert media_refs(html) == [MEDIA_ID, other]
    resolved = resolve_media(html, {MEDIA_ID: "/academy-media/whieda/academy/x/original.png?u=1&e=2&s=a"})
    assert 'src="/academy-media/whieda/academy/x/original.png?u=1&amp;e=2&amp;s=a"' in resolved
    # Нет ссылки (файл не готов или удалён) — картинка убирается, у ссылки пропадает адрес.
    assert "<img" not in resolve_media(html, {})
    assert "media:" not in resolve_media(html, {})
    assert "файл" in resolve_media(html, {})


def test_media_mentions_in_text_are_refs_but_only_tags_are_rewritten():
    other = "11111111-2222-3333-4444-555555555555"
    html = f'<p>текст media:{MEDIA_ID.upper()} и src="media:{other}"</p><img src="media:{MEDIA_ID}" alt="a">'
    # Проверка владельца видит и упоминания текстом (в любом регистре).
    assert media_refs(html) == [MEDIA_ID, other]
    # Текст не трогаем и не падаем, если ссылки на упомянутый файл нет.
    resolved = resolve_media(html, {MEDIA_ID: "/academy-media/k?u=1&e=2&s=x"})
    assert f'src="media:{other}"' in resolved and f"media:{MEDIA_ID.upper()}" in resolved
    assert '<img src="/academy-media/k?u=1&amp;e=2&amp;s=x" alt="a">' in resolved
    assert "<img" not in resolve_media(html, {})


def _links(html: str) -> list[tuple[str, str]]:
    """(href, text) of every link, in order."""
    return re.findall(r'<a [^<>]*?href="([^"]*)"[^<>]*>(.*?)</a>', html, flags=re.S)


@pytest.mark.parametrize(
    ("source", "href", "tail"),
    [
        ("Сервис: https://gptunnel.ru/?ref=wwc", "https://gptunnel.ru/?ref=wwc", ""),
        ("Смотрите https://example.com/a.", "https://example.com/a", "."),
        ("Смотрите (https://example.com/a), потом урок", "https://example.com/a", "), потом урок"),
        ("«https://example.com/a»", "https://example.com/a", "»"),
        ("Вики: https://ru.wikipedia.org/wiki/Тон_(живопись)", "https://ru.wikipedia.org/wiki/Тон_(живопись)", ""),
        ("Файл https://example.com/__init__.py и *всё*", "https://example.com/__init__.py", " и <em>всё</em>"),
        ("http://example.com/a_b_c?x=1&y=2, дальше", "http://example.com/a_b_c?x=1&amp;y=2", ", дальше"),
        ("Сайт https://пример.рф/путь!", "https://пример.рф/путь", "!"),
        # Редактор экранировал подчёркивание — адрес тот же, а не обрезанный.
        (r"Канал https://t.me/wwc\_best\_club", "https://t.me/wwc_best_club", ""),
    ],
)
def test_bare_address_becomes_a_link(source, href, tail):
    html = render_markdown(source)
    assert _links(html) == [(href, href)], html
    assert html.endswith(f"</a>{tail}</p>"), html


def test_bare_addresses_work_in_lists_tables_headings_and_emphasis():
    md = (
        "## Инструменты: https://one.example\n\n"
        "- VPN — https://two.example/vpn\n\n"
        "| Сервис | Адрес |\n|---|---|\n| GPT | https://three.example |\n\n"
        "**https://four.example** и https://five.example\n"
    )
    html = render_markdown(md)
    assert [href for href, _ in _links(html)] == [
        "https://one.example", "https://two.example/vpn", "https://three.example",
        "https://four.example", "https://five.example",
    ]
    assert re.search(r'<strong><a [^<>]*href="https://four\.example"', html), html


@pytest.mark.parametrize(
    ("source", "links"),
    [
        # Готовая ссылка остаётся одной ссылкой со своим текстом.
        ("[Сайт](https://example.com/a)", [("https://example.com/a", "Сайт")]),
        ("[https://example.com/a](https://example.com/b)", [("https://example.com/b", "https://example.com/a")]),
        ("<https://example.com/a>", [("https://example.com/a", "https://example.com/a")]),
        ("[Сайт][1]\n\n[1]: https://example.com/a", [("https://example.com/a", "Сайт")]),
        # Сырой HTML автора: адрес внутри его ссылки второй ссылкой не становится.
        (
            '<a href="https://example.com/a" target="_blank">https://example.com/a</a> и https://example.com/b',
            [("https://example.com/a", "https://example.com/a"), ("https://example.com/b", "https://example.com/b")],
        ),
        ('<a href="https://example.com/a">см. https://example.com/a</a>', [("https://example.com/a", "см. https://example.com/a")]),
        # Код и картинки — не ссылки.
        ("Команда `curl https://example.com/a`", []),
        ("```\ncurl https://example.com/a\n```", []),
        ("    curl https://example.com/a", []),
        ("![Схема https://example.com/a](https://example.com/a.png)", []),
        # Не адрес: другая схема, обрывок, середина слова.
        ("ftp://example.com/a и www.example.com", []),
        ("https:// и http://.", []),
        ("xhttps://example.com/a", []),
    ],
)
def test_ready_links_code_and_non_addresses_are_left_alone(source, links):
    html = render_markdown(source)
    assert _links(html) == links, html
    assert html.count("<a ") == len(links), html


def test_bare_address_next_to_media_reference_leaves_media_alone():
    html = render_markdown(f"![a](media:{MEDIA_ID})\n\nmedia:{MEDIA_ID} и https://example.com/a\n\n[файл](media:{MEDIA_ID})")
    assert f'<img alt="a" src="media:{MEDIA_ID}"' in html
    assert f"<p>media:{MEDIA_ID} и <a " in html
    assert _links(html) == [("https://example.com/a", "https://example.com/a"), (f"media:{MEDIA_ID}", "файл")]
    assert media_refs(html) == [MEDIA_ID]


def test_bare_address_cannot_smuggle_markup():
    for source in (
        'https://example.com/"onmouseover="alert(1)',
        "https://example.com/<script>alert(1)</script>",
        "https://example.com/'onclick='alert(1)",
        "javascript://example.com/%0aalert(1)",
    ):
        html = render_markdown(source)
        for tag in re.findall(r"<[^<>]+>", html):
            assert not re.search(r"\son\w+=|<script|javascript:", tag, flags=re.I), (source, html)


def test_link_out_opens_in_a_new_tab():
    # Ученик не теряет страницу урока: ссылка наружу — в новой вкладке, с rel от nh3.
    for html in (
        render_markdown("[Сайт](https://example.com/a)"),
        render_markdown("https://example.com/a"),
        sanitize_html('<a href="http://example.com/a">x</a>'),
        sanitize_html('<a href="//example.com/a">x</a>'),
        sanitize_html('<a href="https://example.com/a" target="_top" title="t">x</a>'),
        sanitize_html('<a target="_blank" href="https://example.com/a">x</a>'),
    ):
        assert html.count('target="_blank"') == 1, html
        assert 'rel="noopener noreferrer"' in html
    stored = sanitize_html('<p><a href="https://example.com/a">x</a></p>')
    assert sanitize_html(stored) == stored  # очистка на выходе не меняет уже очищенное


@pytest.mark.parametrize(
    "link",
    [
        '<a href="/academy/?course=kurs">x</a>',
        '<a href="#shag-2">x</a>',
        '<a href="mailto:a@example.com">x</a>',
        '<a href="tel:+375291234567">x</a>',
        f'<a href="media:{MEDIA_ID}">x</a>',
        '<a title="href=&quot;https://example.com/a&quot;" href="/academy/">x</a>',
    ],
)
def test_links_inside_the_site_keep_the_tab(link):
    assert "target=" not in sanitize_html(link)
    assert "target=" not in sanitize_html('<img alt=\'<a href="https://example.com/a">\' src="/a.png">')


def test_cleaned_tags_never_hold_raw_angle_brackets_or_quotes():
    # На этом стоят правки тегов после nh3 (resolve_media, новая вкладка): «<», «>» и «"» внутри
    # значений атрибутов экранированы, значит «<…>» в очищенном HTML — всегда настоящий тег.
    hostile = (
        f'<p><img alt="<img" src="media:{MEDIA_ID}"> &quot; src=x onerror=alert(1) x=&quot;</p>'
        '<a title="<a onmouseover=alert(1) &quot; x" href="https://example.com/a">t</a>'
        '<img alt="a > b < c" src="/a.png">'
    )
    clean = sanitize_html(hostile)
    for tag in re.findall(r'<(?:[^<>"]|"[^"]*")+>', clean):
        for value in re.findall(r'="([^"]*)"', tag):
            assert "<" not in value and ">" not in value, tag
    for html in (clean, resolve_media(clean, {}), resolve_media(clean, {MEDIA_ID: "/academy-media/k?u=1&e=2&s=x"})):
        for tag in re.findall(r"<[^<>]+>", html):
            assert not re.search(r"\son\w+=", re.sub(r'"[^"]*"', '""', tag)), html


def test_author_views_clean_stored_html_too():
    from app.academy.author import _course_out, _lesson_out

    course = {
        "slug": "k", "title": "K", "subtitle": None, "kind": "course", "status": "draft", "access_rule": "purchase",
        "description_md": "", "description_html": "<p>x</p><script>alert(1)</script>", "cover_media_id": None,
        "price_wusd_minor": None, "price_currency": "WUSD", "created_at": None, "updated_at": None,
    }
    assert _course_out(course, {}, 1)["description_html"] == "<p>x</p>"
    lesson = {
        "lesson_id": MEDIA_ID, "slug": "a", "module_id": None, "position": 1, "title": "A", "short_title": None,
        "kind": "lesson", "status": "published", "body_md": "", "body_html": "<p>b</p>", "video": None, "files": [],
        "live_at": None, "live_url": None, "unlock": None,
        "prompt_md": "", "prompt_html": '<p onclick="x()">Фото</p><iframe src="https://x"></iframe>', "required": True,
    }
    assert _lesson_out(lesson, {}, 1)["assignment"]["prompt_html"] == "<p>Фото</p>"


# ---- раскрывающиеся блоки «:::раскрыть …» (владелец, 16.10.2026) ---------------------------------

QUIZ_OK = {
    "pass": 1,
    "questions": [{"q": "Вопрос?", "ref": "Разбор", "options": [
        {"text": "Да", "correct": True, "hint": "Верно."},
        {"text": "Нет", "correct": False, "hint": "Перечитайте: Разбор."},
    ]}],
}


FOLD_RE = re.compile(r'<details class="ac-fold"><summary>(.*?)</summary><div class="ac-fold__body">(.*?)</div></details>', re.S)


def _folds(html):
    return FOLD_RE.findall(html)


def test_fold_becomes_details_with_summary_and_body():
    html = render_markdown("## Разбор\n\nВступление.\n\n:::раскрыть Подробный расчёт\nАбзац внутри.\n:::\n\nПосле.", folds=True)
    assert _folds(html) == [("Подробный расчёт", "<p>Абзац внутри.</p>")]
    assert html.startswith("<h2>Разбор</h2>\n<p>Вступление.</p>\n<details")
    assert html.endswith("</details>\n<p>После.</p>")
    assert "<p><details" not in html and ":::" not in html


def test_fold_holds_any_markdown_list_table_code_image_link():
    md = (
        ":::раскрыть Всё внутри\n"
        "Абзац со ссылкой https://wwc.best/academy/ и **жирным**.\n\n"
        "- раз\n- два\n\n"
        "1. первый\n2. второй\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "```\nпромпт: <идея>\n:::\n## не заголовок\n```\n\n"
        f"![Схема](media:{MEDIA_ID})\n"
        ":::\n"
    )
    [(title, body)] = _folds(render_markdown(md, folds=True))
    assert title == "Всё внутри"
    for fragment in ("<strong>жирным</strong>", "<ul>\n<li>раз</li>", "<ol>\n<li>первый</li>", "<table>", "<td>2</td>",
                     "<pre><code>промпт: &lt;идея&gt;\n:::\n## не заголовок\n</code></pre>",
                     f'<img alt="Схема" src="media:{MEDIA_ID}" loading="lazy">',
                     '<a target="_blank" href="https://wwc.best/academy/" rel="noopener noreferrer">'):
        assert fragment in body, fragment
    assert "<h2" not in body
    assert media_refs(render_markdown(md, folds=True)) == [MEDIA_ID]


def test_two_folds_space_after_colons_and_a_fold_right_after_a_heading():
    html = render_markdown(
        "## Раздел\n:::раскрыть Первый\nодин\n:::\nмежду\n::: раскрыть   Второй  \nдва\n:::", folds=True
    )
    assert _folds(html) == [("Первый", "<p>один</p>"), ("Второй", "<p>два</p>")]
    assert html.startswith("<h2>Раздел</h2>\n<details")
    assert "<p>между</p>" in html


def test_fold_title_is_text_escaped_and_limited():
    [(title, _)] = _folds(render_markdown(':::раскрыть Код <b>x</b> & "кавычки"\nтекст\n:::', folds=True))
    assert title == 'Код &lt;b&gt;x&lt;/b&gt; &amp; "кавычки"'
    assert _folds(render_markdown(":::раскрыть " + "я" * 120 + "\nтекст\n:::", folds=True))
    with pytest.raises(FoldError, match="длиннее 120"):
        render_markdown(":::раскрыть " + "я" * 121 + "\nтекст\n:::", folds=True)
    with pytest.raises(FoldError, match="нужен заголовок"):
        render_markdown(":::раскрыть\nтекст\n:::", folds=True)
    with pytest.raises(FoldError, match="нужен заголовок"):
        render_markdown("::: раскрыть   \nтекст\n:::", folds=True)


@pytest.mark.parametrize(
    "source, reason",
    [
        (":::раскрыть Без конца\nтекст\n", "(строка 1) не закрыт"),
        ("текст\n\n:::раскрыть Код без конца\n```\nкод\n:::\n", "не закрыт"),
        (":::раскрыть Первый\n:::раскрыть Второй\nтекст\n:::\n:::\n", "не вкладываются"),
        ("текст\n:::\n", "строка 2: «:::» без начала"),
        (":::раскрытьСлитно\nтекст\n:::\n", "без начала"),
        (":::раскрыть Раздел\nтекст\n## Новый раздел\n:::\n", "заголовок раздела «## …» (строка 3)"),
        (":::раскрыть Раздел\nЗаголовок\n---\n:::\n", "заголовок раздела"),
        (":::раскрыть Раздел\n<h2>сырой</h2>\n:::\n", "заголовок раздела"),
    ],
)
def test_broken_fold_is_an_error_for_the_author(source, reason):
    with pytest.raises(FoldError) as broken:
        render_markdown(source, folds=True)
    assert reason in str(broken.value)
    assert isinstance(broken.value, ValueError)


def test_h3_inside_a_fold_is_fine():
    [(_, body)] = _folds(render_markdown(":::раскрыть Шаги\n### Шаг 1\nтекст\n:::", folds=True))
    assert body.startswith("<h3>Шаг 1</h3>")


def test_folds_are_off_outside_the_academy_cabinet():
    # Магазин рендерит тем же render_markdown без блоков: «:::» остаётся текстом, ошибок нет.
    assert render_markdown(":::раскрыть Заголовок\nтекст\n:::") == "<p>:::раскрыть Заголовок\nтекст\n:::</p>"
    assert render_markdown("текст\n:::") == "<p>текст\n:::</p>"


def test_sanitizer_keeps_the_fold_and_only_its_own_classes():
    html = render_markdown(":::раскрыть Заголовок\nтекст\n:::", folds=True)
    assert sanitize_html(html) == html  # вторая очистка при чтении урока (service.lesson_detail) ничего не снимает
    hostile = sanitize_html(
        '<details class="ac-fold evil" open ontoggle="alert(1)"><summary class="x" onclick="y()">T</summary>'
        '<div class="ac-fold__body" style="color:red" id="z">b</div><div class="evil">c</div></details>'
        '<p class="ac-fold">p</p>'
    )
    assert hostile == "<details><summary>T</summary><div class=\"ac-fold__body\">b</div><div>c</div></details><p>p</p>"
    assert sanitize_html('<details class="ac-fold"><summary>T</summary></details>') == (
        '<details class="ac-fold"><summary>T</summary></details>'
    )


def test_author_tags_inside_a_fold_cannot_close_it():
    html = render_markdown(":::раскрыть Блок\nТекст </div></details> <b>жирный</b>\n:::\n\nПосле.", folds=True)
    assert _folds(html) == [("Блок", "<p>Текст  <b>жирный</b></p>")]
    assert html.endswith("</details>\n<p>После.</p>")


def test_quiz_outside_a_fold_still_splits_and_inside_is_refused():
    quiz = json.dumps(QUIZ_OK, ensure_ascii=False)
    text, found = split_quiz(f":::раскрыть Блок\nтекст\n:::\n\n```quiz\n{quiz}\n```\n")
    assert found["pass"] == 1 and "quiz" not in text
    assert _folds(render_markdown(text, folds=True)) == [("Блок", "<p>текст</p>")]
    with pytest.raises(QuizError, match="тест не может быть внутри раскрывающегося блока «Блок»"):
        split_quiz(f"Вступление.\n\n:::раскрыть Блок\n```quiz\n{quiz}\n```\n:::\n")
    with pytest.raises(FoldError, match="не закрыт"):
        split_quiz(f":::раскрыть Блок\nтекст\n\n```quiz\n{quiz}\n```\n")


def test_author_path_answers_bad_fold_with_the_reason():
    from app.academy.author import _markdown, _split_quiz
    from app.academy.service import AcademyError

    assert _folds(_markdown(":::раскрыть Блок\nтекст\n:::"))
    with pytest.raises(AcademyError) as broken:
        _markdown(":::раскрыть Блок\nтекст")
    assert (broken.value.status, broken.value.code) == (400, "bad_fold")
    assert "не закрыт" in broken.value.extra["detail"]
    with pytest.raises(AcademyError) as in_split:
        _split_quiz("текст\n:::")
    assert in_split.value.code == "bad_fold" and "без начала" in in_split.value.extra["detail"]
    quiz = json.dumps(QUIZ_OK, ensure_ascii=False)
    with pytest.raises(AcademyError) as quiz_inside:
        _split_quiz(f":::раскрыть Блок\n```quiz\n{quiz}\n```\n:::")
    assert quiz_inside.value.code == "bad_quiz" and "внутри раскрывающегося блока" in quiz_inside.value.extra["detail"]
    with pytest.raises(AcademyError) as too_long:
        _markdown("x" * 200_001)
    assert too_long.value.code == "text_too_long"


def test_markdown_without_folds_renders_as_before():
    # Уроки без блоков (весь Basic_course) — тот же HTML, что до блоков: сборщик байт в байт.
    import markdown as markdown_lib

    from app.academy.content import MARKDOWN_EXTENSIONS, markdown_html

    md = "## A\n\nтекст https://wwc.best\n\n```\nкод\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    assert markdown_html(md) == markdown_lib.markdown(md, extensions=MARKDOWN_EXTENSIONS, output_format="html")
