"""WWC CRM v2 rules (TASK.md 02.10.2026): «Сделано», «Перенести», tags, priority,
logging taps, «Сегодня» by meaning, default templates, cursors, migration V20."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.crm.paging import (
    LEGACY_LIMIT,
    MAX_LIMIT,
    clamp_limit,
    decode_cursor,
    encode_cursor,
    page,
)
from app.crm.rules import (
    ACTIVITY_KINDS,
    DEFAULT_TEMPLATES,
    TAGS_MAX,
    TEMPLATE_BODY_MAX,
    TEMPLATE_TITLE_MAX,
    CrmRuleError,
    clean_log,
    clean_priority,
    clean_tags,
    clean_template_body,
    clean_template_title,
    plan_done,
    snooze_until,
    today_sections,
)

TODAY = date(2026, 9, 25)
MEETING = date(2026, 9, 28)
SQL_V20 = Path(__file__).resolve().parents[3] / "postgres" / "sql" / "platform_crm_v20.sql"


def _done(status: str, step: str | None, **kwargs):
    return plan_done(status=status, next_step=step, today=TODAY, meeting_date=kwargs.pop("meeting_date", None), **kwargs)


def test_done_moves_each_step_to_the_next_one():
    invited = _done("new", "invite", meeting_date=MEETING)
    assert (invited.status, invited.next_step, invited.next_at) == ("invited", "result", MEETING)
    presented = _done("invited", "result")
    assert (presented.status, presented.next_step, presented.next_at) == ("presented", "decide", date(2026, 9, 27))
    for step, status in (("decide", "presented"), ("resume", "paused")):
        deciding = _done(status, step)
        assert (deciding.status, deciding.next_step, deciding.next_at) == ("deciding", "ping", None)
    for status in ("client", "partner"):
        again = _done(status, "ping")
        assert (again.status, again.next_step, again.next_at) == (status, "ping", date(2026, 10, 25))
    waiting = _done("deciding", "ping")
    assert (waiting.status, waiting.next_step, waiting.next_at) == ("deciding", "ping", None)


def test_done_needs_a_meeting_for_invite_and_a_step_at_all():
    with pytest.raises(CrmRuleError) as exc:
        _done("new", "invite")
    assert exc.value.code == "meeting_at_required"
    with pytest.raises(CrmRuleError) as exc:
        _done("deciding", None)
    assert exc.value.code == "no_next_step"
    with pytest.raises(CrmRuleError) as exc:
        _done("paused", "ping")  # still paused, and a pause needs a date
    assert exc.value.code == "next_at_required"
    assert _done("paused", "ping", next_at=date(2026, 11, 1), next_at_given=True).next_at == date(2026, 11, 1)


def test_done_with_an_explicit_date_wins():
    plan = _done("presented", "decide", next_at=date(2026, 9, 30), next_at_given=True)
    assert (plan.status, plan.next_step, plan.next_at) == ("deciding", "ping", date(2026, 9, 30))
    assert _done("client", "ping", next_at=None, next_at_given=True).next_at is None


def test_snooze_by_days_or_date_within_a_year():
    assert snooze_until(TODAY, days=1) == date(2026, 9, 26)
    assert snooze_until(TODAY, days=7) == date(2026, 10, 2)
    assert snooze_until(TODAY, on=TODAY) == TODAY  # overdue → today
    assert snooze_until(TODAY, on=date(2026, 9, 25).replace(year=2027)) == date(2027, 9, 25)
    for kwargs in ({}, {"days": 1, "on": TODAY}):
        with pytest.raises(CrmRuleError) as exc:
            snooze_until(TODAY, **kwargs)
        assert exc.value.code == "snooze_required"
    for kwargs in ({"days": 0}, {"days": -3}, {"days": 367}, {"days": True}, {"days": "3"},
                   {"on": date(2026, 9, 24)}, {"on": date(2027, 9, 27)}):
        with pytest.raises(CrmRuleError) as exc:
            snooze_until(TODAY, **kwargs)
        assert exc.value.code == "invalid_snooze", kwargs


def test_tags_are_cleaned_deduplicated_and_limited():
    assert clean_tags(["  #VIP ", "vip", "минск  центр", "", "#"]) == ["VIP", "минск центр"]
    assert clean_tags(None) == []
    many = [f"метка {i}" for i in range(TAGS_MAX)]
    assert clean_tags(many) == many
    for value, code in (([*many, "ещё"], "too_many_tags"), (["я" * 33], "tag_too_long"),
                        ("vip", "invalid_tags"), ([1], "invalid_tags")):
        with pytest.raises(CrmRuleError) as exc:
            clean_tags(value)
        assert exc.value.code == code


def test_priority_and_log_values():
    assert (clean_priority(True), clean_priority(False), clean_priority(1), clean_priority(0)) == (1, 0, 1, 0)
    for bad in (2, -1, "1", None):
        with pytest.raises(CrmRuleError):
            clean_priority(bad)
    assert clean_log("call", None) == ("call", "phone")
    assert clean_log("message", "whatsapp") == ("message", "whatsapp")
    with pytest.raises(CrmRuleError) as exc:
        clean_log("message", None)
    assert exc.value.code == "invalid_channel"
    with pytest.raises(CrmRuleError) as exc:
        clean_log("note", "phone")
    assert exc.value.code == "invalid_kind"


def test_today_sections_split_by_meaning():
    rows = [
        {"contact_id": "m", "next_step": "result", "next_at": TODAY, "meeting_today": True,
         "meeting_at": datetime(2026, 9, 25, 11, tzinfo=timezone.utc)},
        {"contact_id": "call", "next_step": "invite", "next_at": TODAY},
        {"contact_id": "remind", "next_step": "ping", "next_at": TODAY},
        {"contact_id": "none", "next_step": None, "next_at": TODAY},
        {"contact_id": "late", "next_step": "decide", "next_at": date(2026, 9, 20)},
        {"contact_id": "tomorrow", "next_step": "invite", "next_at": date(2026, 9, 26)},
    ]
    sections = today_sections(rows, TODAY)
    assert [(s["key"], s["title"], [c["contact_id"] for c in s["contacts"]]) for s in sections] == [
        ("meetings", "Встречи сегодня", ["m"]),
        ("call", "Позвонить", ["call"]),
        ("remind", "Напомнить", ["remind", "none"]),
        ("overdue", "Просрочено", ["late"]),
    ]
    merged = today_sections(rows, TODAY, split_overdue=False)
    assert [(s["key"], [c["contact_id"] for c in s["contacts"]]) for s in merged] == [
        ("meetings", ["m"]), ("call", ["call", "late"]), ("remind", ["remind", "none"])]
    assert today_sections([], TODAY) == []


def test_stage_titles_follow_the_approved_v2_mockup():
    from app.crm.rules import STATUS_TITLES, STATUSES

    assert list(STATUS_TITLES) == list(STATUSES)  # codes are the contract with the site, unchanged
    assert [STATUS_TITLES[s] for s in STATUSES] == [
        "Новый контакт", "Приглашён", "Презентация проведена", "Думает", "Клиент", "Партнёр", "Пауза"]


FORBIDDEN_PROMISES = ("доход", "заработ", "деньг", "прибыл", "богат", "миллион", "вылеч", "лечит", "исцел",
                      "здоров", "болезн", "гарант", "похуде")


def test_default_templates_are_short_polite_and_promise_nothing():
    assert [title for title, _ in DEFAULT_TEMPLATES] == [
        "Приглашение", "Напоминание о встрече", "После презентации", "Подумали?", "Возобновление", "Спасибо клиенту"]
    for title, body in DEFAULT_TEMPLATES:
        assert clean_template_title(title) == title and clean_template_body(body) == body
        assert body.startswith("{имя}, ")
        assert len(body) <= 200
        lowered = body.lower()
        assert not any(word in lowered for word in FORBIDDEN_PROMISES), (title, body)
        assert set(re.findall(r"\{[^}]*\}", body)) <= {"{имя}", "{мое_имя}"}


def test_template_fields_are_checked():
    assert clean_template_title("  Привет   всем ") == "Привет всем"
    for value, code in (("", "title_required"), ("я" * (TEMPLATE_TITLE_MAX + 1), "title_too_long")):
        with pytest.raises(CrmRuleError) as exc:
            clean_template_title(value)
        assert exc.value.code == code
    for value, code in (("  ", "body_required"), ("я" * (TEMPLATE_BODY_MAX + 1), "body_too_long")):
        with pytest.raises(CrmRuleError) as exc:
            clean_template_body(value)
        assert exc.value.code == code


# ---- cursors ------------------------------------------------------------------------

STAMP = datetime(2026, 10, 2, 9, 30, 15, 123456, tzinfo=timezone.utc)
CID = "6f1c3f7e-3f0a-4a52-9b1e-2d6a1c5e9f00"


@pytest.mark.parametrize(
    "kind, values",
    [
        ("updated", [STAMP, CID]),
        ("name", [CID]),
        ("next", [date(2026, 10, 5), CID]),
        ("next", [None, CID]),
        ("pipeline", [1, STAMP, CID]),
        ("activities", [STAMP, CID]),
    ],
)
def test_cursor_round_trip_keeps_exact_values(kind, values):
    raw = encode_cursor(kind, values)
    assert re.fullmatch(r"[A-Za-z0-9_-]+", raw)  # safe in a query string as is
    assert decode_cursor(raw, kind) == values


def test_no_cursor_carries_a_name_or_a_phone():
    """Cursors ride in URLs, and URLs reach access logs (review 02.10.2026)."""
    from app.crm.paging import SHAPES

    assert all("text" not in shape for shape in SHAPES.values())
    with pytest.raises(ValueError):
        encode_cursor("name", ["анна петрова", CID])


def test_bad_or_foreign_cursor_is_invalid_cursor():
    other = encode_cursor("name", [CID])
    for raw in (other, "not-base64!", "e30", encode_cursor("updated", [STAMP, CID])[:-3], "eyJrIjoidXBkYXRlZCJ9"):
        with pytest.raises(CrmRuleError) as exc:
            decode_cursor(raw, "updated")
        assert exc.value.code == "invalid_cursor", raw
    import base64
    import json

    def raw_cursor(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")

    for values in (["2026-10-02T09:30:15", CID], [STAMP.isoformat(), "not-a-uuid"], [STAMP.isoformat()],
                   [STAMP.isoformat(), 5], [STAMP.isoformat(), ["x"]], [5, CID], [None, CID]):
        with pytest.raises(CrmRuleError) as exc:
            decode_cursor(raw_cursor({"k": "activities", "v": values}), "activities")
        assert exc.value.code == "invalid_cursor", values
    with pytest.raises(CrmRuleError):
        decode_cursor(raw_cursor({"k": "pipeline", "v": [True, STAMP.isoformat(), CID]}), "pipeline")
    assert decode_cursor(None, "updated") is None and decode_cursor("", "updated") is None


def test_limit_is_clamped_and_page_says_if_there_is_more():
    assert (clamp_limit(None), clamp_limit(0), clamp_limit(5), clamp_limit(10_000)) == (50, 1, 5, MAX_LIMIT)
    assert clamp_limit(None, LEGACY_LIMIT) == 300
    with pytest.raises(CrmRuleError):
        clamp_limit("x")
    rows = [{"id": f"6f1c3f7e-3f0a-4a52-9b1e-2d6a1c5e9f0{i}"} for i in range(4)]
    items, cursor = page(rows, 3, "name", lambda row: (row["id"],))
    assert [row["id"] for row in items] == [row["id"] for row in rows[:3]]
    assert decode_cursor(cursor, "name") == [rows[2]["id"]]
    assert page(rows[:3], 3, "name", lambda row: (row["id"],)) == (rows[:3], None)
    assert page([], 3, "name", lambda row: (row["id"],)) == ([], None)


# ---- migration V20 ------------------------------------------------------------------


def test_v20_matches_the_code_and_has_no_dollar():
    sql = SQL_V20.read_text(encoding="utf-8")
    assert "$" not in sql  # production SQL goes through n8n, which eats the dollar sign
    for kind in ACTIVITY_KINDS:
        assert f"'{kind}'" in sql
    for needle in ("add column if not exists tags", "add column if not exists priority",
                   "add column if not exists last_touch_at", "add column if not exists meeting_reminded_at",
                   "add column if not exists deleted_at", "create table if not exists crm_activities",
                   "create table if not exists crm_templates", "on conflict do nothing",
                   "alter table crm_activities enable row level security",
                   "alter table crm_templates enable row level security",
                   "add column if not exists crm_install_hint_dismissed_at",
                   "add column if not exists crm_templates_seeded_at"):
        assert needle in sql, needle
    lowered = sql.lower()
    assert "create extension" not in lowered and "drop table" not in lowered and "drop column" not in lowered
