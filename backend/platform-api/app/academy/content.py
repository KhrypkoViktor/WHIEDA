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
_MEDIA_REF_RE = re.compile(rf'"media:({_MEDIA_ID})"')
_IMG_RE = re.compile(rf'<img\b[^>]*\bsrc="media:({_MEDIA_ID})"[^>]*>')
_HREF_RE = re.compile(rf'\shref="media:({_MEDIA_ID})"')
_SRC_RE = re.compile(rf'\ssrc="media:({_MEDIA_ID})"')


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
    """``media:<uuid>`` references in order, without repeats."""
    seen: list[str] = []
    for match in _MEDIA_REF_RE.finditer(str(html or "")):
        if match.group(1) not in seen:
            seen.append(match.group(1))
    return seen


def resolve_media(html: str | None, urls: dict[str, str]) -> str:
    """References → links of this viewer; an image without a link is dropped, a
    link without one keeps its text only."""
    text = str(html or "")
    text = _IMG_RE.sub(lambda m: m.group(0) if m.group(1) in urls else "", text)
    text = _SRC_RE.sub(lambda m: f' src="{html_lib.escape(urls[m.group(1)], quote=True)}"', text)
    return _HREF_RE.sub(
        lambda m: f' href="{html_lib.escape(urls[m.group(1)], quote=True)}"' if m.group(1) in urls else "",
        text,
    )
