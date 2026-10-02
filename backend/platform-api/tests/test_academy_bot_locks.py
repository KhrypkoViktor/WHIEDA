"""Academy v2 in the bot: the next lesson respects locks and homework, locked lessons say when."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.academy.service import AcademyError, AcademyViewer
from app.telegram.academy import lesson_lock_text, try_handle_academy_callback
from app.telegram.bindings import binding_context_scope
from app.telegram.update_parser import TelegramCallbackQuery

STUDENT = AcademyViewer(telegram_user_id=9001, is_preview_admin=False, partner_paid=False)


def lesson(slug, number, *, position=None, done=False, complete=False, locked=False, reason=None, opens_at=None,
           homework=None):
    return {
        "slug": slug, "position": position or number, "number": number, "title": f"Урок {slug}",
        "short_title": f"Урок {slug}", "module_title": "Неделя 1", "result": "", "minutes": None,
        "done": done, "complete": complete, "locked": locked, "lock_reason": reason, "opens_at": opens_at,
        "assignment_status": homework,
    }


def outline(lessons, next_lesson):
    return {
        "course": {
            "slug": "akvarel", "title": "Акварель", "lessons_total": len(lessons),
            "lessons_done": sum(1 for row in lessons if row["complete"]), "next_lesson": next_lesson,
        },
        "lessons": lessons,
    }


def callback(data: str) -> TelegramCallbackQuery:
    return TelegramCallbackQuery(chat_id=9001, user_id=9001, callback_query_id="cb", data=data, chat_type="private", raw={})


@pytest.fixture
def bot(whieda_bot_binding):
    send = AsyncMock(return_value={"ok": True})
    with binding_context_scope(whieda_bot_binding), patch("app.telegram.academy.send_telegram_text", send), patch(
        "app.telegram.academy.answer_callback_query", AsyncMock()
    ), patch("app.telegram.academy.load_viewer", AsyncMock(return_value=STUDENT)), patch(
        "app.telegram.academy.with_site_login", AsyncMock(side_effect=lambda url, **_: url)
    ):
        yield send


async def press(tenant, data, plan):
    with patch("app.telegram.academy.course_outline", AsyncMock(return_value=plan)):
        return await try_handle_academy_callback(tenant, callback(data), trace_id="t")


def test_lock_texts():
    assert lesson_lock_text("after_prev") == "🔒 Урок откроется, когда пройдёте предыдущий."
    # 07:00 UTC = 10:00 по Москве.
    assert lesson_lock_text("date:2026-10-10T07:00:00+00:00", "2026-10-10T07:00:00+00:00") == "🔒 Урок откроется 10.10.2026."
    assert lesson_lock_text("days:7", "2026-10-09T21:30:00+00:00") == "🔒 Урок откроется 10.10.2026."
    assert lesson_lock_text("days:7") == "🔒 Урок откроется через 7 дн. после старта курса."


@pytest.mark.asyncio
async def test_card_asks_for_homework_not_handed_in(whieda_tenant, bot):
    plan = outline([lesson("a", 1, done=True, homework="none")], "a")
    await press(whieda_tenant, "acad:c:akvarel", plan)
    assert "Домашка: сдайте её на странице урока." in bot.await_args.kwargs["text"]


@pytest.mark.asyncio
async def test_waiting_for_homework_review_instead_of_a_locked_lesson(whieda_tenant, bot):
    plan = outline([
        lesson("a", 1, done=True, homework="submitted"),
        lesson("b", 2, locked=True, reason="after_prev"),
    ], None)
    result = await press(whieda_tenant, "acad:c:akvarel", plan)
    assert result["status"] == "waiting_review"
    assert bot.await_args.kwargs["text"].startswith("Домашка на проверке у автора.")


@pytest.mark.asyncio
async def test_next_lesson_locked_by_date_says_when(whieda_tenant, bot):
    plan = outline([
        lesson("a", 1, done=True, complete=True),
        lesson("b", 2, locked=True, reason="date:2026-10-10T07:00:00+00:00", opens_at="2026-10-10T07:00:00+00:00"),
    ], None)
    result = await press(whieda_tenant, "acad:c:akvarel", plan)
    assert result["status"] == "locked"
    assert bot.await_args.kwargs["text"] == "🔒 Урок откроется 10.10.2026."


@pytest.mark.asyncio
async def test_card_follows_next_lesson_with_number_and_homework(whieda_tenant, bot):
    plan = outline([
        lesson("a", 1, done=True, complete=True),
        lesson("b", 2, position=5, done=True, homework="returned"),
        lesson("c", 3, position=6),
    ], "b")
    result = await press(whieda_tenant, "acad:c:akvarel", plan)
    assert result == {"ok": True, "route": "academy", "status": "lesson", "lesson": "b", "trace_id": "t"}
    text = bot.await_args.kwargs["text"]
    assert "урок 2 из 3" in text
    assert "Домашку вернули — посмотрите комментарий на странице урока." in text


@pytest.mark.asyncio
async def test_tapping_a_locked_lesson_shows_the_lock_not_the_card(whieda_tenant, bot):
    plan = outline([
        lesson("a", 1),
        lesson("b", 2, locked=True, reason="days:7", opens_at="2026-10-09T21:30:00+00:00"),
    ], "a")
    result = await press(whieda_tenant, "acad:l:akvarel:2", plan)
    assert result["status"] == "locked"
    assert bot.await_args.kwargs["text"] == "🔒 Урок откроется 10.10.2026."


@pytest.mark.asyncio
async def test_done_on_a_locked_lesson_says_why(whieda_tenant, bot):
    plan = outline([lesson("a", 1, locked=True, reason="after_prev")], None)
    error = AcademyError(403, "lesson_locked", {"lock_reason": "after_prev", "opens_at": None})
    with patch("app.telegram.academy.set_lesson_done", AsyncMock(side_effect=error)):
        result = await press(whieda_tenant, "acad:d:akvarel:1", plan)
    assert result["status"] == "lesson_locked"
    assert bot.await_args.kwargs["text"] == "🔒 Урок откроется, когда пройдёте предыдущий."


@pytest.mark.asyncio
async def test_all_lessons_marks_locks_and_completed(whieda_tenant, bot):
    plan = outline([
        lesson("a", 1, done=True, complete=True),
        lesson("b", 2),
        lesson("c", 3, locked=True, reason="after_prev"),
    ], "b")
    await press(whieda_tenant, "acad:all:akvarel", plan)
    rows = [row[0]["text"] for row in bot.await_args.kwargs["reply_markup"]["inline_keyboard"]]
    assert rows == ["✓ 1. Урок a", "○ 2. Урок b", "🔒 3. Урок c"]


@pytest.mark.asyncio
async def test_finished_course(whieda_tenant, bot):
    plan = outline([lesson("a", 1, done=True, complete=True)], None)
    result = await press(whieda_tenant, "acad:c:akvarel", plan)
    assert result["status"] == "completed"
    assert "пройден" in bot.await_args.kwargs["text"]
