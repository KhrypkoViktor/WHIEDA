"""Format promotions, events and community links like legacy Telegram advisor."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

RUSSIAN_WEEKDAYS = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)
RUSSIAN_MONTHS = (
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def _tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Europe/Minsk")
    except Exception:
        return ZoneInfo("Europe/Minsk")


def format_promotion_end(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_tz("Europe/Minsk")).strftime("%d.%m.%Y")
    except ValueError:
        return ""


def format_promotions(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return (
            "Сейчас в структурированной базе нет активной акции для Беларуси. "
            "Проверьте раздел «Акции» в личном кабинете: условия и остатки подарков могут меняться."
        )
    blocks: list[str] = []
    for row in rows[:5]:
        parts = [f"🔥 {str(row.get('title') or '').strip()}"]
        brief = str(row.get("short_text") or row.get("benefit_text") or "").strip()
        if brief:
            parts.append(brief)
        until = format_promotion_end(row.get("ends_at"))
        if until:
            parts.append(f"Действует до {until}.")
        source = str(row.get("source_url") or "").strip()
        if source:
            parts.append(source)
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def next_event_timestamp(row: dict[str, Any], *, now_ms: int | None = None) -> int | None:
    raw = str(row.get("starts_at") or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        base = datetime.fromisoformat(raw)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        base_ms = int(base.timestamp() * 1000)
    except ValueError:
        return None
    now = now_ms if now_ms is not None else int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    recurrence = str(row.get("recurrence_rule") or "")
    if "FREQ=WEEKLY" not in recurrence.upper():
        return base_ms if base_ms >= now else None
    week_ms = 7 * 24 * 60 * 60 * 1000
    if base_ms >= now:
        return base_ms
    steps = math.ceil((now - base_ms) / week_ms)
    return base_ms + int(steps) * week_ms


def format_event_when(row: dict[str, Any], timestamp_ms: int) -> str:
    tz = _tz(str(row.get("timezone") or "Europe/Minsk"))
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).astimezone(tz)
    return f"{RUSSIAN_WEEKDAYS[dt.weekday()].capitalize()}, {dt.day} {RUSSIAN_MONTHS[dt.month]}, {dt:%H:%M}"


def format_events(rows: list[dict[str, Any]]) -> str:
    upcoming: list[tuple[dict[str, Any], int]] = []
    for row in rows:
        ts = next_event_timestamp(row)
        if ts is not None:
            upcoming.append((row, ts))
    upcoming.sort(key=lambda item: item[1])
    upcoming = upcoming[:3]
    if not upcoming:
        return "Ближайших подтверждённых мероприятий пока нет."
    blocks: list[str] = []
    for row, ts in upcoming:
        parts = [
            f"📅 {str(row.get('title') or 'Мероприятие WHIEDA').strip()}",
            format_event_when(row, ts),
        ]
        address = str(row.get("address") or "").strip()
        if address:
            parts.append(address)
        contact = str(row.get("contact") or "").strip()
        if contact:
            parts.append(f"Спикер: {contact}")
        description = str(row.get("description") or "").strip()
        if description:
            parts.append(description)
        online = str(row.get("online_url") or "").strip()
        if online:
            parts.append(online)
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def format_community(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "Проверенных ссылок на сообщества пока нет."
    blocks: list[str] = []
    for row in rows[:5]:
        parts = [f"📢 {str(row.get('title') or 'Канал WHIEDA').strip()}"]
        description = str(row.get("description") or "").strip()
        if description:
            parts.append(description)
        url = str(row.get("url") or "").strip()
        if url:
            parts.append(url)
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)
