from __future__ import annotations

from app.advisor.sql.coach import build_coach_feedback, build_coach_response, parse_coach_command


def test_parse_coach_menu_command():
    assert parse_coach_command("коуч") == {"kind": "practice", "objection_number": None}
    assert parse_coach_command("коуч старт") == {"kind": "first_week_menu"}
    assert parse_coach_command("коуч 4") == {"kind": "practice", "objection_number": 4}


def test_coach_objections_menu():
    objections = [{"title": "Это МЛМ / пирамида"}, {"title": "Дорого"}]
    text, mode, _ctx = build_coach_response({"kind": "practice", "objection_number": None}, objections, {})
    assert mode == "direct_coach_objections_menu"
    assert "Выбери ситуацию" in text
    assert "Это МЛМ / пирамида" in text


def test_coach_first_week_start():
    text, mode, ctx = build_coach_response({"kind": "first_week_menu"}, [], {})
    assert mode == "direct_coach_first_week_start"
    assert "День 1. Твоя опора" in text
    assert ctx["coach_first_week"]["current_day"] == 1


def test_coach_first_week_complete_advances_day():
    stored = {"coach_first_week": {"started": True, "current_day": 1, "completed_through": 0}}
    text, mode, ctx = build_coach_response({"kind": "first_week_complete"}, [], stored)
    assert mode == "direct_coach_first_week_complete"
    assert "День 2" in text
    assert ctx["coach_first_week"]["current_day"] == 2


def test_coach_first_week_continue_same_day():
    stored = {"coach_first_week": {"started": True, "current_day": 2, "completed_through": 1}}
    text, mode, _ctx = build_coach_response({"kind": "first_week_continue"}, [], stored)
    assert mode == "direct_coach_first_week_continue"
    assert "День 2" in text


def test_coach_feedback_checks():
    objection = {
        "title": "Это МЛМ / пирамида",
        "first_reply": "Понимаю опасение.",
        "next_step": "Спроси, что именно настораживает.",
    }
    draft = "Понимаю, у многих был неприятный опыт. Что именно тебя беспокоит?"
    text, mode = build_coach_feedback(objection, draft)
    assert mode == "direct_coach_objection_feedback"
    assert "✓ Сначала признал сомнение человека" in text
