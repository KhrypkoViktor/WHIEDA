"""Academy v2: author text → safe HTML, and media references inside it.

Authors write Markdown; the server renders it (``markdown``) and cleans the HTML
(``nh3``): headings, lists, links, images, tables and code survive, scripts,
event handlers, styles, frames and forms do not. The site inserts lesson HTML
with innerHTML, so nothing reaches it unclean — old bundle HTML is cleaned on the
way out too.

An uploaded image or file is referenced as ``media:<uuid>`` (``![alt](media:…)``):
the stored HTML never holds a link that expires. ``resolve_media`` swaps the
reference for a signed link of the viewer when the lesson is read.

A bare ``https://…`` in the author's text becomes a link when the Markdown is rendered
(``BareLinks``, 05.10.2026); a ready link, code and ``media:<uuid>`` are not touched.

A lesson's test (V35) is a fenced block with the language ``quiz`` holding JSON:
``split_quiz`` cuts it out of the raw Markdown before rendering — answers and
hints never reach the lesson HTML (``app.academy.quiz`` checks the JSON).
"""

from __future__ import annotations

import html as html_lib
import json
import re
import xml.etree.ElementTree as etree
from typing import Any

import markdown
import nh3
from markdown.extensions import Extension
from markdown.inlinepatterns import InlineProcessor
from markdown.util import HTML_PLACEHOLDER_RE, INLINE_PLACEHOLDER_RE, AtomicString

from app.academy.quiz import QuizError, validate_quiz

ALLOWED_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "br", "hr",
    "strong", "b", "em", "i", "u", "s", "del", "sup", "sub", "blockquote",
    "ul", "ol", "li",
    "a", "img",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "code", "pre",
}
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title"},  # target — только _blank (tag_attribute_values)
    "img": {"src", "alt", "title", "width", "height"},
    "th": {"align", "colspan", "rowspan"},
    "td": {"align", "colspan", "rowspan"},
    "ol": {"start"},
}
URL_SCHEMES = {"http", "https", "mailto", "tel", "media"}
MAX_MARKDOWN_CHARS = 200_000

# Голый адрес: http(s)://, не в середине слова, до пробела, «<», «>» или кавычки.
# \x02…\x03 — метки markdown на месте уже разобранного куска (код, сырой тег, экранированный знак).
_BARE_LINK_RE = r'(?<![^\W_])https?://(?=[^\W_])(?:[^\s<>"\x02\x03]|\x02klzzwxh:\d+\x03)+'
# Знаки в конце — пунктуация фразы, а не адреса; «)» — только если она без пары.
_BARE_LINK_TAIL = ".,;:!?*_~'»”“’…]}"
_ESCAPED_CHAR_RE = re.compile("\x02(\\d+)\x03")
_RAW_LINK_OPEN_RE = re.compile(r"<a[\s>]", re.I)
_RAW_LINK_CLOSE_RE = re.compile(r"</a[\s>]", re.I)


class _BareLinkProcessor(InlineProcessor):
    """``https://…`` in plain text → a link. Runs after code, ``[text](url)``, ``<url>`` and
    raw HTML tags (priority 85), so their addresses never reach it, and before emphasis,
    so ``_`` and ``*`` inside an address stay as they are."""

    ANCESTOR_EXCLUDES = ("a",)  # текст готовой markdown-ссылки

    def handleMatch(self, m: re.Match, data: str):  # noqa: N802 — имя задаёт markdown
        if self._inside_raw_link(data[: m.start()]):
            return None, None, None
        raw = m.group(0)
        # Метка экранированного знака (\_) — часть адреса, любая другая его заканчивает.
        for mark in INLINE_PLACEHOLDER_RE.finditer(raw):
            if self._escaped_char(mark) is None:
                raw = raw[: mark.start()]
                break
        while raw[-1] in _BARE_LINK_TAIL or (raw[-1] == ")" and raw.count(")") > raw.count("(")):
            raw = raw[:-1]
        url = INLINE_PLACEHOLDER_RE.sub(self._escaped_char, raw)
        link = etree.Element("a")
        link.set("href", url)
        link.text = AtomicString(url)
        return link, m.start(), m.start() + len(raw)

    def _escaped_char(self, mark: re.Match) -> str | None:
        node = self.md.treeprocessors["inline"].stashed_nodes.get(mark.group(1))
        code = _ESCAPED_CHAR_RE.fullmatch(node) if isinstance(node, str) else None
        return chr(int(code.group(1))) if code else None

    def _inside_raw_link(self, before: str) -> bool:
        """The author's own ``<a …>`` is open before this place (raw HTML is kept as marks)."""
        depth = 0
        for mark in HTML_PLACEHOLDER_RE.finditer(self.unescape(before)):
            raw = str(self.md.htmlStash.rawHtmlBlocks[int(mark.group(1))])
            if _RAW_LINK_OPEN_RE.match(raw):
                depth += 1
            elif _RAW_LINK_CLOSE_RE.match(raw):
                depth = max(0, depth - 1)
        return depth > 0


class BareLinks(Extension):
    def extendMarkdown(self, md: markdown.Markdown) -> None:  # noqa: N802
        md.inlinePatterns.register(_BareLinkProcessor(_BARE_LINK_RE, md), "academy_bare_link", 85)


MARKDOWN_EXTENSIONS = ["tables", "sane_lists", "fenced_code", BareLinks()]

_MEDIA_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# Любое упоминание, и текстом тоже, в любом регистре: проверка владельца видит всё.
_MEDIA_MENTION_RE = re.compile(rf"media:({_MEDIA_ID})", re.I)
# Подставляются только настоящие ссылки внутри тегов (после nh3 значения атрибутов — в двойных кавычках).
_TAG_RE = re.compile(r"<[^<>]+>")
_ATTR_RE = re.compile(rf'\s(src|href)="media:({_MEDIA_ID})"', re.I)
# Тест урока: ```quiz … ``` (или ~~~quiz), закрывающий забор — тот же, что открывающий.
_QUIZ_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*quiz[ \t]*\r?$", re.M)
_QUIZ_BLOCK_RE = re.compile(
    r"^ {0,3}(?P<fence>`{3,}|~{3,})[ \t]*quiz[ \t]*\r?\n(?P<body>.*?)^ {0,3}(?P=fence)[ \t]*\r?$\n?",
    re.M | re.S,
)


def split_quiz(body_md: str | None) -> tuple[str, dict[str, Any] | None]:
    """Markdown → (Markdown without the ``quiz`` block, the checked test or None).

    The first ```quiz block is the test; a second one, an unclosed one, broken JSON
    or a test that breaks the rules — QuizError with the reason (the author fixes it).
    A second block is refused rather than left in the text: it would show the answers."""
    text = str(body_md or "")
    match = _QUIZ_BLOCK_RE.search(text)
    if not match:
        if _QUIZ_OPEN_RE.search(text):
            raise QuizError("тест: блок ```quiz не закрыт")
        return text, None
    try:
        raw = json.loads(match.group("body"))
    except json.JSONDecodeError as exc:
        line = text[: match.start("body")].count("\n") + exc.lineno
        raise QuizError(f"тест: не JSON — {exc.msg} (строка {line}, позиция {exc.colno})") from exc
    quiz = validate_quiz(raw)
    rest = text[: match.start()] + text[match.end():]
    if _QUIZ_OPEN_RE.search(rest):
        raise QuizError("тест: в уроке второй блок ```quiz — оставьте один")
    return rest, quiz


def sanitize_html(raw: str | None) -> str:
    return nh3.clean(
        str(raw or ""),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=URL_SCHEMES,
        link_rel="noopener noreferrer",
        tag_attribute_values={"a": {"target": {"_blank"}}},
        set_tag_attribute_values={"img": {"loading": "lazy"}},
        strip_comments=True,
    )


def render_markdown(source: str | None) -> str:
    text = str(source or "")
    if len(text) > MAX_MARKDOWN_CHARS:
        raise ValueError("markdown_too_long")
    return sanitize_html(markdown.markdown(text, extensions=MARKDOWN_EXTENSIONS, output_format="html"))


def media_refs(html: str | None) -> list[str]:
    """Every ``media:<uuid>`` mentioned (links and plain text), lower-case, in order, no repeats."""
    seen: list[str] = []
    for match in _MEDIA_MENTION_RE.finditer(str(html or "")):
        media_id = match.group(1).lower()
        if media_id not in seen:
            seen.append(media_id)
    return seen


def linked_media(html: str | None) -> set[str]:
    """Ids used as real links: ``src``/``href="media:<id>"`` inside tags, not mentions in text.
    Only these open a file to the readers of a course (``media_service.media_access``)."""
    found: set[str] = set()
    for tag in _TAG_RE.finditer(str(html or "")):
        for match in _ATTR_RE.finditer(tag.group(0)):
            found.add(match.group(2).lower())
    return found


def resolve_media(html: str | None, urls: dict[str, str]) -> str:
    """``src``/``href="media:<id>"`` inside tags → links of this viewer. An image without
    a link is dropped, a link without one keeps its text only; text is never touched."""

    def attribute(match: re.Match) -> str:
        url = urls.get(match.group(2).lower())
        return f' {match.group(1)}="{html_lib.escape(url, quote=True)}"' if url else ""

    def tag(match: re.Match) -> str:
        value = match.group(0)
        if "media:" not in value.lower():
            return value
        if value[:4].lower() == "<img":
            source = _ATTR_RE.search(value)
            if source and source.group(1).lower() == "src" and source.group(2).lower() not in urls:
                return ""
        return _ATTR_RE.sub(attribute, value)

    return _TAG_RE.sub(tag, str(html or ""))
