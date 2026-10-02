"""Academy v2: author text → safe HTML, and media references inside it.

Authors write Markdown; the server renders it (``markdown``) and cleans the HTML
(``nh3``): headings, lists, links, images, tables and code survive, scripts,
event handlers, styles, frames and forms do not. The site inserts lesson HTML
with innerHTML, so nothing reaches it unclean — old bundle HTML is cleaned on the
way out too.

An uploaded image or file is referenced as ``media:<uuid>`` (``![alt](media:…)``):
the stored HTML never holds a link that expires. ``resolve_media`` swaps the
reference for a signed link of the viewer when the lesson is read.
"""

from __future__ import annotations

import html as html_lib
import re

import markdown
import nh3

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
MARKDOWN_EXTENSIONS = ["tables", "sane_lists", "fenced_code"]
MAX_MARKDOWN_CHARS = 200_000

_MEDIA_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# Любое упоминание, и текстом тоже, в любом регистре: проверка владельца видит всё.
_MEDIA_MENTION_RE = re.compile(rf"media:({_MEDIA_ID})", re.I)
# Подставляются только настоящие ссылки внутри тегов (после nh3 значения атрибутов — в двойных кавычках).
_TAG_RE = re.compile(r"<[^<>]+>")
_ATTR_RE = re.compile(rf'\s(src|href)="media:({_MEDIA_ID})"', re.I)


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
