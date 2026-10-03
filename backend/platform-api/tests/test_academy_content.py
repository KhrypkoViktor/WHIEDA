"""Academy v2: author markdown → safe HTML; media references → signed links."""

from __future__ import annotations

import pytest

from app.academy.content import media_refs, render_markdown, resolve_media, sanitize_html

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
