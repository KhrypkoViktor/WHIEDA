"""Academy in the bot: the «тренер» half of the course.

The site shows the lesson itself; the bot keeps the person moving: which
lesson is next, «Сделал ✓», the list of lessons. Progress is the same rows
the site writes (academy_progress), keyed by telegram_user_id.

Callbacks (≤ 64 bytes):
  acad:home              — courses (one course → its card right away)
  acad:c:<slug>          — next lesson of the course
  acad:l:<slug>:<pos>    — lesson card
  acad:d:<slug>:<pos>    — mark done, show the next one
  acad:all:<slug>        — all lessons

Text commands «начать обучение», «мой план», «академия», «коуч старт»,
«коуч день N» open the Academy for those who have it: the old 7-day plan and
the coach's first week were two competing «first weeks» nobody finished
(owner, 23.09.2026). The objection practice («коуч», «коуч ответ …») stays.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlencode

from app.academy.service import (
    AcademyError,
    AcademyViewer,
    academy_visible,
    course_outline,
    list_courses,
    load_viewer,
    set_lesson_done,
)
from app.settings import get_settings
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import answer_callback_query, send_telegram_text
from app.telegram.site_login import with_site_login
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

ACADEMY_BUTTON_LABEL = "🎓 Академия"
ACADEMY_HOME_CALLBACK = "acad:home"

_CALLBACK_RE = re.compile(
    r"^acad:(?:(home)|c:([a-z0-9-]{1,40})|l:([a-z0-9-]{1,40}):(\d{1,3})|d:([a-z0-9-]{1,40}):(\d{1,3})|all:([a-z0-9-]{1,40}))$"
)
_TEXT_RE = re.compile(
    r"^/?(?:академия|academy|обучение|начать\s+обучение|старт\s+обучения|мой\s+план|план\s+обучения|"
    r"продолжить\s+обучение|коуч\s+(?:старт|7\s*дней|первые\s+7\s+дней|продолжить|дальше|текущий\s+день|день\s+[1-7]))\s*$",
    re.I,
)

LOCK_TEXT = {
    "pro_required": "Академия входит в PRO — платформу вашего сайта. Продлите PRO, и курс откроется сразу.",
    "purchase_required": "Этот курс продаётся отдельно. Напишите в поддержку, чтобы подключить.",
    "academy_not_open": "Академия скоро откроется. Мы сообщим, когда курс будет доступен.",
}


def lesson_url(course_slug: str, lesson_slug: str) -> str:
    base = str(get_settings().platform_academy_site_base or "https://wwc.best").rstrip("/")
    return f"{base}/academy/?{urlencode({'course': course_slug, 'lesson': lesson_slug})}"


def is_academy_text(text: str) -> bool:
    return bool(_TEXT_RE.match(str(text or "").strip()))


async def academy_button_rows(tenant_id: str, telegram_user_id: int | None) -> list[list[dict[str, Any]]]:
    """The cabinet row, or nothing while the Academy is closed for this person."""
    if telegram_user_id is None:
        return []
    try:
        viewer = await load_viewer(tenant_id, int(telegram_user_id))
    except Exception:  # the cabinet must open even if the Academy lookup fails
        return []
    if not academy_visible(viewer):
        return []
    return [[{"text": ACADEMY_BUTTON_LABEL, "callback_data": ACADEMY_HOME_CALLBACK}]]


async def _send(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    binding = current_bot_binding()
    await send_telegram_text(chat_id=str(chat_id), text=text, bot_token=binding.bot_token, reply_markup=reply_markup)


def _progress_bar(done: int, total: int) -> str:
    if total <= 0:
        return ""
    filled = round(10 * done / total)
    return "▰" * filled + "▱" * (10 - filled)


def _lesson_card(
    course: dict[str, Any], lesson: dict[str, Any], lessons_total: int, lessons_done: int, *, open_url: str | None = None
) -> tuple[str, dict]:
    lines = [
        f"🎓 {course['title']} · урок {lesson['position']} из {lessons_total}",
        "",
        lesson["title"],
    ]
    if lesson.get("module_title"):
        lines.insert(1, f"Раздел: {lesson['module_title']}")
    if lesson.get("result"):
        lines += ["", f"Что получится: {lesson['result']}"]
    if lesson.get("minutes"):
        lines.append(f"Время: около {lesson['minutes']} мин.")
    lines += ["", f"Пройдено {lessons_done} из {lessons_total} {_progress_bar(lessons_done, lessons_total)}"]
    if lesson.get("done"):
        lines.append("✓ Этот урок уже отмечен.")
    slug = course["slug"]
    keyboard = {
        "inline_keyboard": [
            [{"text": "📖 Открыть урок", "url": open_url or lesson_url(slug, lesson["slug"])}],
            [{"text": "✅ Сделал — дальше", "callback_data": f"acad:d:{slug}:{lesson['position']}"}],
            [{"text": "📋 Все уроки", "callback_data": f"acad:all:{slug}"}],
        ]
    }
    return "\n".join(lines), keyboard


def _all_lessons(course: dict[str, Any], lessons: list[dict[str, Any]]) -> tuple[str, dict]:
    lines = [f"🎓 {course['title']}", f"Пройдено {course['lessons_done']} из {course['lessons_total']}", ""]
    module = None
    for lesson in lessons:
        if lesson.get("module_title") and lesson["module_title"] != module:
            module = lesson["module_title"]
            lines += ["", module]
        mark = "✓" if lesson.get("done") else "○"
        lines.append(f"{mark} {lesson['position']}. {lesson['short_title']}")
    rows = [
        [{"text": f"{'✓' if lesson.get('done') else '○'} {lesson['position']}. {lesson['short_title']}"[:60],
          "callback_data": f"acad:l:{course['slug']}:{lesson['position']}"}]
        for lesson in lessons
    ]
    return "\n".join(lines).replace("\n\n\n", "\n\n"), {"inline_keyboard": rows}


async def _show_lock(chat_id: int, reason: str) -> None:
    keyboard = None
    if reason == "pro_required":
        keyboard = {"inline_keyboard": [[{"text": "Продлить платформу", "callback_data": "renew:start"}]]}
    await _send(chat_id, LOCK_TEXT.get(reason, LOCK_TEXT["academy_not_open"]), keyboard)


async def _show_course(tenant_id: str, chat_id: int, viewer: AcademyViewer, slug: str, position: int | None) -> dict[str, Any]:
    outline = await course_outline(tenant_id, slug, viewer)
    course, lessons = outline["course"], outline["lessons"]
    if not lessons:
        await _send(chat_id, "В курсе пока нет уроков.")
        return {"status": "empty"}
    if position is None:
        lesson = next((row for row in lessons if not row["done"]), None)
        if lesson is None:
            await _send(
                chat_id,
                f"🎉 Курс «{course['title']}» пройден: {course['lessons_total']} из {course['lessons_total']}.\n\n"
                "Уроки остаются открыты — можно вернуться к любому.",
                {"inline_keyboard": [[{"text": "📋 Все уроки", "callback_data": f"acad:all:{slug}"}]]},
            )
            return {"status": "completed"}
    else:
        lesson = next((row for row in lessons if row["position"] == position), None)
        if lesson is None:
            await _send(chat_id, "Такого урока нет.")
            return {"status": "lesson_not_found"}
    # Ссылка сразу входит на сайт (#wwc-login): человек уже в боте, второй вход не нужен.
    open_url = await with_site_login(
        lesson_url(course["slug"], lesson["slug"]), tenant_id=tenant_id, telegram_user_id=viewer.telegram_user_id
    )
    text, keyboard = _lesson_card(course, lesson, course["lessons_total"], course["lessons_done"], open_url=open_url)
    await _send(chat_id, text, keyboard)
    return {"status": "lesson", "lesson": lesson["slug"]}


async def show_academy_home(tenant_id: str, chat_id: int, telegram_user_id: int) -> dict[str, Any]:
    viewer = await load_viewer(tenant_id, telegram_user_id)
    if not academy_visible(viewer):
        await _show_lock(chat_id, "academy_not_open")
        return {"status": "academy_not_open"}
    courses = await list_courses(tenant_id, viewer)
    if not courses:
        await _send(chat_id, "Курсы появятся здесь совсем скоро.")
        return {"status": "no_courses"}
    if len(courses) == 1:
        course = courses[0]
        if course["locked"]:
            await _show_lock(chat_id, course["lock_reason"])
            return {"status": course["lock_reason"]}
        return await _show_course(tenant_id, chat_id, viewer, course["slug"], None)
    lines = ["🎓 Академия WWC", ""]
    rows = []
    for course in courses:
        mark = "🔒 " if course["locked"] else ""
        lines.append(f"{mark}{course['title']} — {course['lessons_done']} из {course['lessons_total']}")
        rows.append([{"text": f"{mark}{course['title']}"[:60], "callback_data": f"acad:c:{course['slug']}"}])
    await _send(chat_id, "\n".join(lines), {"inline_keyboard": rows})
    return {"status": "courses"}


async def try_handle_academy_callback(
    tenant: TenantContext, callback: TelegramCallbackQuery, *, trace_id: str
) -> dict[str, Any] | None:
    match = _CALLBACK_RE.fullmatch(callback.data)
    if not match:
        return None
    binding = current_bot_binding()
    await answer_callback_query(callback_query_id=callback.callback_query_id, bot_token=binding.bot_token)
    if callback.chat_type != "private":
        return {"ok": True, "route": "academy", "status": "private_chat_required", "trace_id": trace_id}
    home, course_slug, les_slug, les_pos, done_slug, done_pos, all_slug = match.groups()
    tenant_id = tenant.tenant_id
    try:
        if home:
            result = await show_academy_home(tenant_id, callback.chat_id, callback.user_id)
            return {"ok": True, "route": "academy_home", **result, "trace_id": trace_id}
        viewer = await load_viewer(tenant_id, callback.user_id)
        if course_slug:
            result = await _show_course(tenant_id, callback.chat_id, viewer, course_slug, None)
        elif les_slug:
            result = await _show_course(tenant_id, callback.chat_id, viewer, les_slug, int(les_pos))
        elif done_slug:
            outline = await course_outline(tenant_id, done_slug, viewer)
            lesson = next((row for row in outline["lessons"] if row["position"] == int(done_pos)), None)
            if lesson is None:
                await _send(callback.chat_id, "Такого урока нет.")
                return {"ok": False, "route": "academy_done", "status": "lesson_not_found", "trace_id": trace_id}
            await set_lesson_done(tenant_id, done_slug, lesson["slug"], viewer, done=True, source="bot")
            result = await _show_course(tenant_id, callback.chat_id, viewer, done_slug, None)
        else:
            outline = await course_outline(tenant_id, all_slug, viewer)
            text, keyboard = _all_lessons(outline["course"], outline["lessons"])
            await _send(callback.chat_id, text, keyboard)
            result = {"status": "all_lessons"}
    except AcademyError as exc:
        await _show_lock(callback.chat_id, exc.code if exc.code in LOCK_TEXT else "academy_not_open")
        return {"ok": False, "route": "academy", "status": exc.code, "trace_id": trace_id}
    return {"ok": True, "route": "academy", **result, "trace_id": trace_id}


async def try_handle_academy_text(tenant: TenantContext, msg: TelegramMessage, *, trace_id: str) -> dict[str, Any] | None:
    """Old «обучение» commands → the Academy, but only where it is open."""
    if not is_academy_text(msg.text or ""):
        return None
    try:
        viewer = await load_viewer(tenant.tenant_id, msg.user_id)
    except Exception:  # no Academy lookup → the old onboarding answers as before
        return None
    if not academy_visible(viewer):
        return None
    result = await show_academy_home(tenant.tenant_id, msg.chat_id, msg.user_id)
    return {"ok": True, "route": "academy_text", **result, "trace_id": trace_id}
