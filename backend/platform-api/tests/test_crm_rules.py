"""CRM v1 rules: statuses → next step, PATCH plan, phone, «Сегодня», access, export."""

from __future__ import annotations

from datetime import date

import pytest

from app.crm.rules import (
    NEXT_STEPS,
    STATUSES,
    CrmRuleError,
    CrmViewer,
    access_lock_reason,
    clean_name,
    clean_note,
    csv_cell,
    default_plan,
    export_row,
    group_today,
    lead_note_text,
    looks_like_timezone,
    phone_from_lead_contact,
    plan_for_patch,
    split_phone,
)

TODAY = date(2026, 9, 25)
MEETING = date(2026, 9, 28)


def test_status_table_defaults():
    assert (default_plan("new", today=TODAY).next_step, default_plan("new", today=TODAY).next_at) == ("invite", TODAY)
    invited = default_plan("invited", today=TODAY, meeting_date=MEETING)
    assert (invited.next_step, invited.next_at) == ("result", MEETING)
    presented = default_plan("presented", today=TODAY)
    assert (presented.next_step, presented.next_at) == ("decide", date(2026, 9, 27))
    deciding = default_plan("deciding", today=TODAY)
    assert (deciding.next_step, deciding.next_at) == ("ping", None)
    for status in ("client", "partner"):
        plan = default_plan(status, today=TODAY)
        assert (plan.next_step, plan.next_at) == ("ping", date(2026, 10, 25))


def test_invited_needs_meeting_and_paused_needs_date():
    with pytest.raises(CrmRuleError) as exc:
        default_plan("invited", today=TODAY)
    assert exc.value.code == "meeting_at_required"
    with pytest.raises(CrmRuleError) as exc:
        default_plan("paused", today=TODAY)
    assert exc.value.code == "next_at_required"
    with pytest.raises(CrmRuleError):
        default_plan("unknown", today=TODAY)


def _patch(**kwargs):
    base = dict(
        current_status="new",
        new_status=None,
        today=TODAY,
        meeting_date=None,
        meeting_given=False,
        next_step=None,
        next_step_given=False,
        next_at=None,
        next_at_given=False,
        current_next_step="invite",
        current_next_at=TODAY,
    )
    base.update(kwargs)
    return plan_for_patch(**base)


def test_status_change_applies_rule_when_step_not_given():
    plan = _patch(new_status="presented")
    assert (plan.next_step, plan.next_at) == ("decide", date(2026, 9, 27))


def test_explicit_step_and_date_win_over_rule():
    plan = _patch(new_status="presented", next_step="ping", next_step_given=True,
                  next_at=date(2026, 10, 1), next_at_given=True)
    assert (plan.next_step, plan.next_at) == ("ping", date(2026, 10, 1))
    only_date = _patch(new_status="client", next_at=date(2026, 12, 1), next_at_given=True)
    assert (only_date.next_step, only_date.next_at) == ("ping", date(2026, 12, 1))


def test_invited_takes_meeting_date_and_follows_a_new_meeting():
    plan = _patch(new_status="invited", meeting_date=MEETING, meeting_given=True)
    assert (plan.next_step, plan.next_at) == ("result", MEETING)
    moved = _patch(current_status="invited", current_next_step="result", current_next_at=MEETING,
                   meeting_date=date(2026, 9, 30), meeting_given=True)
    assert (moved.next_step, moved.next_at) == ("result", date(2026, 9, 30))
    with pytest.raises(CrmRuleError) as exc:
        _patch(new_status="invited")
    assert exc.value.code == "meeting_at_required"


def test_paused_with_date_and_deciding_with_manual_date():
    paused = _patch(new_status="paused", next_at=date(2026, 11, 1), next_at_given=True)
    assert (paused.next_step, paused.next_at) == ("resume", date(2026, 11, 1))
    with pytest.raises(CrmRuleError):
        _patch(new_status="paused")
    deciding = _patch(new_status="deciding")
    assert (deciding.next_step, deciding.next_at) == ("ping", None)
    manual = _patch(new_status="deciding", next_at=date(2026, 9, 26), next_at_given=True)
    assert (manual.next_step, manual.next_at) == ("ping", date(2026, 9, 26))


def test_same_status_keeps_step_and_invalid_values_refused():
    same = _patch(new_status="new")
    assert (same.next_step, same.next_at) == ("invite", TODAY)
    with pytest.raises(CrmRuleError) as exc:
        _patch(next_step="call", next_step_given=True)
    assert exc.value.code == "invalid_next_step"
    with pytest.raises(CrmRuleError) as exc:
        _patch(new_status="lost")
    assert exc.value.code == "invalid_status"
    cleared = _patch(next_at=None, next_at_given=True)
    assert cleared.next_at is None


def test_statuses_and_steps_match_the_migration():
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[3] / "postgres" / "sql" / "platform_crm_v14.sql").read_text(encoding="utf-8")
    for status in STATUSES:
        assert f"'{status}'" in sql
    for step in NEXT_STEPS:
        assert f"'{step}'" in sql
    assert "$" not in sql  # production SQL goes through n8n, which eats the dollar sign


@pytest.mark.parametrize(
    "raw, e164",
    [
        ("8 928 672-92-88", "+79286729288"),
        ("+7 (928) 672-92-88", "+79286729288"),
        ("9286729288", "+79286729288"),
        ("+375 29 123-45-67", "+375291234567"),
        ("12-34", None),
    ],
)
def test_phone_normalization_keeps_raw(raw, e164):
    phone_e164, phone_raw = split_phone(raw)
    assert phone_e164 == e164
    assert phone_raw == " ".join(raw.split())
    assert split_phone("") == (None, None)
    assert split_phone(None) == (None, None)


def test_lead_contact_only_phone_shaped_becomes_phone():
    assert phone_from_lead_contact("8 928 672 92 88") == ("+79286729288", "8 928 672 92 88")
    assert phone_from_lead_contact("@ivan_petrov") == (None, None)
    assert phone_from_lead_contact("ivan@mail.ru") == (None, None)
    note = lead_note_text(contact="@ivan_petrov", product_name="Стельки", comment="перезвонить вечером", phone_known=False)
    assert note.splitlines() == [
        "Заявка с сайта.",
        "Интерес: Стельки",
        "Контакт: @ivan_petrov",
        "Комментарий: перезвонить вечером",
    ]
    assert "Контакт" not in lead_note_text(contact="89286729288", product_name="", comment="", phone_known=True)


def test_names_notes_and_timezones():
    assert clean_name("  Анна   Петрова ") == "Анна Петрова"
    with pytest.raises(CrmRuleError):
        clean_name("   ")
    assert clean_note("  перезвонить  ") == "перезвонить"
    with pytest.raises(CrmRuleError):
        clean_note("")
    with pytest.raises(CrmRuleError):
        clean_note("x" * 4001)
    for name in ("Europe/Moscow", "Asia/Yekaterinburg", "America/Argentina/Buenos_Aires", "UTC", "Etc/GMT-3"):
        assert looks_like_timezone(name)
    for name in ("", "Europe/Moscow; drop table", "../etc/passwd", "x" * 70):
        assert not looks_like_timezone(name)


def test_today_groups_follow_step_order_and_count_overdue():
    rows = [
        {"id": "a", "next_step": "ping", "next_at": date(2026, 9, 20)},
        {"id": "b", "next_step": "invite", "next_at": TODAY},
        {"id": "c", "next_step": "result", "next_at": date(2026, 9, 24)},
        {"id": "d", "next_step": "invite", "next_at": date(2026, 9, 26)},  # завтра — не сегодня
        {"id": "e", "next_step": None, "next_at": TODAY},
        {"id": "f", "next_step": "decide", "next_at": None},
    ]
    view = group_today(rows, TODAY)
    assert view["date"] == "2026-09-25"
    assert [group["step"] for group in view["groups"]] == ["invite", "result", "ping"]
    assert [group["title"] for group in view["groups"]] == ["Пригласить на встречу", "Узнать результат", "Напомнить о себе"]
    assert [row["id"] for row in view["groups"][2]["contacts"]] == ["a", "e"]
    assert view["overdue"] == 2


def test_access_pilot_is_fail_closed():
    paid = CrmViewer(telegram_user_id=10, is_preview_admin=False, partner_paid=True)
    unpaid = CrmViewer(telegram_user_id=11, is_preview_admin=False, partner_paid=False)
    admin = CrmViewer(telegram_user_id=1, is_preview_admin=True, partner_paid=False)
    # '*' → None: every partner with paid PRO
    assert access_lock_reason(paid, None) is None
    assert access_lock_reason(unpaid, None) == "pro_required"
    assert access_lock_reason(admin, None) is None
    # empty list → nobody but preview admins (a forgotten variable opens nothing)
    assert access_lock_reason(paid, frozenset()) == "crm_pilot_only"
    assert access_lock_reason(admin, frozenset()) is None
    pilot = frozenset({11, 12})
    assert access_lock_reason(paid, pilot) == "crm_pilot_only"
    assert access_lock_reason(unpaid, pilot) == "pro_required"
    assert access_lock_reason(admin, pilot) is None


def test_pilot_setting_parsing(monkeypatch):
    from app.settings import get_settings

    for raw, expected in (("*", None), ("", frozenset()), ("7, 8,x", frozenset({7, 8})), (" * ", None)):
        monkeypatch.setenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS", raw)
        get_settings.cache_clear()
        assert get_settings().parsed_crm_pilot() == expected, raw
    monkeypatch.delenv("PLATFORM_CRM_PILOT_TELEGRAM_IDS")
    get_settings.cache_clear()
    assert get_settings().parsed_crm_pilot() == frozenset()
    assert get_settings().platform_crm_lead_cards is False
    get_settings.cache_clear()


@pytest.mark.parametrize(
    "raw",
    ["+７ ９１６ １２３-４５-６７", "＋７９１６１２３４５６７", "٨٩١٦١٢٣٤٥٦٧", "８ (９１６) １２３ ４５ ６７"],
)
def test_unicode_digits_become_ascii_or_nothing(raw):
    """Full-width and other Unicode digits used to pass as «+７…» and hit the CHECK."""
    e164, kept = split_phone(raw)
    assert e164 == "+79161234567"
    assert kept == " ".join(raw.split())
    assert phone_from_lead_contact(raw)[0] == "+79161234567"
    assert (kept or "").isprintable()


def test_export_cells_are_spreadsheet_safe():
    assert csv_cell("=HYPERLINK(\"x\")").startswith("'=")
    assert csv_cell("+79286729288") == "'+79286729288"
    # A normalized phone is not a formula: the phone column has no apostrophe.
    assert export_row({"name": "Б", "phone_e164": "+79286729288", "status": "new"})[1] == "+79286729288"
    assert export_row({"name": "Б", "phone_raw": "=1+2", "status": "new"})[1] == "'=1+2"
    assert csv_cell("Анна") == "Анна"
    row = export_row(
        {
            "name": "Анна",
            "phone_e164": None,
            "phone_raw": "8 928 672-92-88",
            "source": "соседка",
            "status": "presented",
            "next_step": "decide",
            "next_at": date(2026, 9, 27),
            "notes": "25.09.2026: была на презентации",
        }
    )
    assert row == ["Анна", "8 928 672-92-88", "соседка", "Презентация проведена", "Довести до решения",
                   "27.09.2026", "25.09.2026: была на презентации"]
