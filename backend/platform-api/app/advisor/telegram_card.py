"""Telegram-oriented product card renderer (plain Unicode, no Markdown stars)."""

from __future__ import annotations

import re
from typing import Any

MARKDOWN_STAR_RE = re.compile(r"\*\*")
HTML_TAG_RE = re.compile(r"<[^>]+>")

CARD_FOOTER = (
    "Могу подсказать цену/PV, фото, видео, сертификат или сравнение с другим товаром "
    "— если эти материалы есть в базе."
)

_SECTION_SPECS: tuple[tuple[str, str, bool], ...] = (
    ("what_it_is", "🔥 Коротко:", False),
    ("who_asks_about_it", "👥 Для кого:", False),
    ("common_use_cases", "✅ Когда обычно рассматривают:", True),
    ("how_to_use_short", "🧭 Как используют:", False),
    ("what_to_expect_soft", "🧠 Почему интересен:", False),
    ("contraindications_short", "⚠️ Ограничения:", True),
)


def _sanitize_field(value: Any, *, collapse_spaces: bool = True) -> str:
    text = MARKDOWN_STAR_RE.sub("", str(value or ""))
    text = HTML_TAG_RE.sub("", text)
    if collapse_spaces:
        return re.sub(r"\s+", " ", text).strip()
    lines: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"[ \t]+", " ", line.strip())
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def _clean_field(value: Any) -> str:
    return _sanitize_field(value, collapse_spaces=True)


def _product_title(product: dict[str, Any], card: dict[str, Any] | None) -> str:
    for source in (product, card or {}):
        for key in ("canonical_name", "short_name"):
            name = _clean_field(source.get(key))
            if name:
                return name
    return _clean_field(product.get("sku")) or "Товар"


def _strip_leading_product_name(text: str, title: str) -> str:
    if not text or not title:
        return text
    normalized_title = title.casefold()
    cleaned = text
    for sep in (" — ", " - ", ": "):
        prefix = f"{title}{sep}"
        if cleaned.casefold().startswith(prefix.casefold()):
            return cleaned[len(prefix) :].strip()
    if cleaned.casefold().startswith(normalized_title):
        return cleaned[len(title) :].lstrip(" —-:").strip()
    return cleaned


def _split_bullets(text: str) -> list[str]:
    raw = _sanitize_field(text, collapse_spaces=False)
    if not raw:
        return []
    if "\n" in raw:
        parts = [_clean_field(part) for part in raw.splitlines()]
        parts = [part for part in parts if part]
        if len(parts) >= 2:
            return parts
    compact = _clean_field(raw)
    if ";" in compact:
        parts = [_clean_field(part) for part in compact.split(";")]
        parts = [part for part in parts if part]
        if len(parts) >= 2 and all(len(part) <= 160 for part in parts):
            return parts
    return [compact] if compact else []


def _first_sentence(text: str) -> str:
    value = _clean_field(text)
    if not value:
        return ""
    match = re.search(r"(.+?[.!?…])\s", value + " ")
    if match:
        return match.group(1).strip()
    return value


def render_telegram_product_card(
    card: dict[str, Any] | None,
    product: dict[str, Any],
    *,
    compact: bool = False,
) -> str:
    title = _product_title(product, card)
    if not card:
        return title or _clean_field(product.get("sku"))

    if compact:
        summary = _strip_leading_product_name(_clean_field(card.get("what_it_is")), title)
        lines = [title]
        sentence = _first_sentence(summary)
        if sentence:
            lines.extend(["", f"🔥 Коротко: {sentence}"])
        lines.extend(["", CARD_FOOTER])
        return "\n".join(lines).strip()

    lines: list[str] = [title]
    rendered_sections = 0

    for field, heading, as_bullets in _SECTION_SPECS:
        raw_source = str(card.get(field) or "").strip()
        if not raw_source:
            continue
        if as_bullets:
            bullets = _split_bullets(raw_source)
            if not bullets:
                continue
            lines.append("")
            lines.append(heading)
            for bullet in bullets:
                lines.append(f"• {bullet}")
            rendered_sections += 1
            continue

        raw = _strip_leading_product_name(_clean_field(raw_source), title) if field == "what_it_is" else _clean_field(raw_source)
        if not raw:
            continue
        lines.append("")
        lines.append(f"{heading} {raw}")
        rendered_sections += 1

    if rendered_sections == 0:
        fallback = _clean_field(product.get("canonical_name") or product.get("sku"))
        return fallback or title

    lines.extend(["", CARD_FOOTER])
    text = "\n".join(lines).strip()
    if "**" in text:
        raise ValueError("telegram card renderer produced literal markdown stars")
    return text
