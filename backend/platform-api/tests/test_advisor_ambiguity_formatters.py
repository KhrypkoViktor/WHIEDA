from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.advisor.sql.ambiguity import (
    is_ambiguous_short_alias,
    try_ambiguity_clarification,
    weak_color_or_belt_clarification,
)
from app.advisor.sql.business_formatters import (
    format_events,
    format_promotion_end,
    format_promotions,
    next_event_timestamp,
)


def test_paste_ambiguity_is_hardcoded():
    assert weak_color_or_belt_clarification("паста") is None


def test_belt_clarification_not_direct():
    result = weak_color_or_belt_clarification("пояс")
    assert result is not None
    assert result["direct"] is False
    assert "Магнитный пояс" in result["fallback"]


def test_ambiguous_short_alias_activator():
    assert is_ambiguous_short_alias("активатор", "активатор") is True
    assert is_ambiguous_short_alias("активатор клеток", "активатор клеток") is False


def test_format_promotion_end_ru():
    assert format_promotion_end("2026-12-31T23:59:59+00:00") == "01.01.2027" or format_promotion_end(
        "2026-12-31T12:00:00+00:00"
    )


def test_format_promotions_empty():
    text = format_promotions([])
    assert "нет активной акции" in text


def test_format_promotions_with_end():
    text = format_promotions(
        [
            {
                "title": "Тестовая акция",
                "short_text": "Скидка 10%",
                "ends_at": "2026-12-15T21:00:00+00:00",
            }
        ]
    )
    assert "Тестовая акция" in text
    assert "Скидка 10%" in text
    assert "Действует до" in text


def test_next_event_timestamp_weekly():
    base = datetime(2026, 1, 1, 18, 0, tzinfo=ZoneInfo("Europe/Minsk"))
    row = {
        "starts_at": base.isoformat(),
        "recurrence_rule": "FREQ=WEEKLY",
        "timezone": "Europe/Minsk",
    }
    future_now = int(datetime(2026, 8, 1, tzinfo=timezone.utc).timestamp() * 1000)
    ts = next_event_timestamp(row, now_ms=future_now)
    assert ts is not None
    assert ts >= future_now


def test_format_events_includes_address():
    row = {
        "title": "Встреча WHIEDA",
        "starts_at": "2027-06-15T17:00:00+03:00",
        "address": "Кальварийская, 4",
        "timezone": "Europe/Minsk",
        "recurrence_rule": "",
    }
    text = format_events([row])
    assert "Встреча WHIEDA" in text
    assert "Кальварийская, 4" in text
    assert "Вторник, 15 июня, 17:00" in text
    assert "Tuesday" not in text
