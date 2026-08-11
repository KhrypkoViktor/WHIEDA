"""Telegram-oriented product card renderer (plain Unicode, no Markdown stars)."""

from __future__ import annotations

import re
from typing import Any

MARKDOWN_STAR_RE = re.compile(r"\*\*")
HTML_TAG_RE = re.compile(r"<[^>]+>")

CARD_FOOTER = (
    "Могу подсказать цену/PV, фото, видео, сертификат или сравнение с другим товаром."
)

_SECTION_SPECS: tuple[tuple[str, str, bool], ...] = (
    ("what_it_is", "🔥 Коротко:", False),
    ("who_asks_about_it", "👥 Для кого:", False),
    ("common_use_cases", "✅ Когда обычно рассматривают:", True),
    ("how_to_use_short", "🧭 Как используют:", False),
    ("what_to_expect_soft", "🧠 Почему интересен:", False),
    ("contraindications_short", "⚠️ Ограничения:", True),
)


def _clean_field(value: Any) -> str:
    text = MARKDOWN_STAR_RE.sub("", str(value or "")).strip()
    text = HTML_TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


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
    value = _clean_field(text)
    if not value:
        return []
    if ";" in value:
        parts = [_clean_field(part) for part in value.split(";")]
        parts = [part for part in parts if part]
        if len(parts) >= 2 and all(len(part) <= 160 for part in parts):
            return parts
    if "\n" in value:
        parts = [_clean_field(part) for part in value.splitlines()]
        parts = [part for part in parts if part]
        if len(parts) >= 2:
            return parts
    return [value]


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
        raw = _clean_field(card.get(field))
        if not raw:
            continue
        if field == "what_it_is":
            raw = _strip_leading_product_name(raw, title)
        if not raw:
            continue
        lines.append("")
        if as_bullets:
            bullets = _split_bullets(raw)
            lines.append(heading)
            for bullet in bullets:
                lines.append(f"• {bullet}")
        else:
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
