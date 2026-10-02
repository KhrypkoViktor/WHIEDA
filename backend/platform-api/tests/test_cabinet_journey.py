"""Уровень кабинета, замки и «Следующий шаг» (ТЗ кабинета §3.1 и §8)."""

from __future__ import annotations

from dataclasses import replace

from app.cabinet.journey import JourneyFacts, cabinet_locks, cabinet_tier, journey_steps

PRO = JourneyFacts(
    tier="pro",
    marks=frozenset(),
    profile_complete=False,
    lesson1_done=False,
    invited_count=0,
    crm_contacts=0,
    crm_lock=None,
    club_active=False,
    has_site=True,
)


def _statuses(result: dict) -> dict[str, str]:
    return {step["key"]: step["status"] for step in result["steps"]}


def test_tier_is_free_without_paid_site_and_leader_needs_pro():
    assert cabinet_tier(partner_paid=False, leader_right=False) == "free"
    assert cabinet_tier(partner_paid=True, leader_right=False) == "pro"
    assert cabinet_tier(partner_paid=True, leader_right=True) == "leader"
    assert cabinet_tier(partner_paid=False, leader_right=True) == "free"


def test_free_tier_locks_site_tools_crm_and_club_but_not_partners_or_balance():
    locks = cabinet_locks(tier="free", has_site=False, club_active=False, crm_lock="pro_required")
    assert locks == {
        "site": "pro_required",
        "profile": "pro_required",
        "calculator": "pro_required",
        "repeat_prices": "pro_required",
        "academy_pro": "pro_required",
        "crm": "pro_required",
        "club": "club_required",
        "team": "leader_required",
    }
    for open_section in ("partners", "balance", "support", "settings", "what_to_send"):
        assert open_section not in locks


def test_pro_tier_opens_everything_but_club_team_and_crm_pilot():
    assert cabinet_locks(tier="pro", has_site=True, club_active=True, crm_lock=None) == {"team": "leader_required"}
    locks = cabinet_locks(tier="pro", has_site=True, club_active=False, crm_lock="crm_pilot_only")
    assert locks == {"crm": "crm_pilot_only", "club": "club_required", "team": "leader_required"}
    assert cabinet_locks(tier="leader", has_site=True, club_active=True, crm_lock=None) == {}
    # Сайт есть, но срок кончился: раздел «Мой сайт» открыт (там «Продлить»), профиль — нет.
    expired = cabinet_locks(tier="free", has_site=True, club_active=False, crm_lock="pro_required")
    assert "site" not in expired and expired["profile"] == "pro_required"


def test_pro_path_goes_in_order_and_marks_done_steps():
    result = journey_steps(PRO)
    assert [s["key"] for s in result["steps"]] == ["presentation", "profile", "lesson1", "invite_sent", "crm_contact", "club"]
    assert _statuses(result) == {
        "presentation": "current",
        "profile": "upcoming",
        "lesson1": "upcoming",
        "invite_sent": "upcoming",
        "crm_contact": "upcoming",
        "club": "locked",
    }
    assert result["current"] == "presentation" and result["percent"] == 0
    club = result["steps"][-1]
    assert club["lock_reason"] == "club_required" and club["action"] == "/start/"

    later = journey_steps(replace(PRO, marks=frozenset({"presentation"}), profile_complete=True, invited_count=2))
    assert _statuses(later) == {
        "presentation": "done",
        "profile": "done",
        "lesson1": "current",
        "invite_sent": "done",  # приглашённые уже есть — шаг сделан и без кнопки
        "crm_contact": "upcoming",
        "club": "locked",
    }
    assert later["done"] == 3 and later["total"] == 6 and later["percent"] == 50


def test_steps_with_their_own_lock_are_skipped_by_next_step():
    result = journey_steps(replace(PRO, marks=frozenset({"presentation"}), crm_contacts=None, crm_lock="crm_pilot_only"))
    crm = next(step for step in result["steps"] if step["key"] == "crm_contact")
    assert crm["status"] == "locked" and crm["lock_reason"] == "crm_pilot_only"
    assert result["current"] == "profile"
    everything = journey_steps(
        replace(
            PRO,
            marks=frozenset({"presentation", "invite_sent"}),
            profile_complete=True,
            lesson1_done=True,
            crm_contacts=4,
            club_active=True,
        )
    )
    assert everything["percent"] == 100 and everything["current"] is None


def test_free_path_ends_with_want_site_and_skips_missing_free_course():
    free = JourneyFacts(
        tier="free",
        marks=frozenset({"presentation"}),
        profile_complete=False,
        lesson1_done=False,
        invited_count=0,
        crm_contacts=None,
        crm_lock="pro_required",
        club_active=False,
        has_site=False,
    )
    result = journey_steps(free)
    assert result["path"] == "free"
    assert [s["key"] for s in result["steps"]] == ["presentation", "invite_sent", "want_site"]
    assert _statuses(result) == {"presentation": "done", "invite_sent": "current", "want_site": "upcoming"}
    with_course = journey_steps(replace(free, free_lesson_available=True, free_lesson_done=True))
    assert [s["key"] for s in with_course["steps"]] == ["presentation", "free_lesson", "invite_sent", "want_site"]
    assert with_course["steps"][1]["done"] is True
    assert with_course["steps"][-1]["action"] == "/start/"


def test_manual_steps_are_flagged_for_the_site_buttons():
    result = journey_steps(PRO)
    assert {s["key"] for s in result["steps"] if s["manual"]} == {"presentation", "invite_sent"}
    closed = journey_steps(replace(PRO, academy_lock="academy_not_open"))
    lesson = next(step for step in closed["steps"] if step["key"] == "lesson1")
    assert lesson["status"] == "locked" and lesson["lock_reason"] == "academy_not_open"
