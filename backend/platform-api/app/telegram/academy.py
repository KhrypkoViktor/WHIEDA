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

Authors on the shelf (owner, 25.09.2026), private chat only:
  «ключи <slug> <N>» — N access keys as ``t.me/<bot>?start=course_<код>`` links
                       (more than 20 — one text file);
  «мои курсы»        — per course: status, keys redeemed / issued, students.
A student opens the link: ``/start course_<код>`` → ``handle_course_start_token``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx

from app.academy.keys import (
    KEY_ERROR_TEXT,
    MAX_KEYS_PER_BATCH,
    AcademyKeyError,
    author_actor_ids,
    author_course_stats,
    course_start_link,
    issue_keys_for_telegram,
    parse_course_start_token,
    redeem_key,
    shelf_paid_until,
)
from app.academy.service import (
    SHELF_PRODUCT_CODE,
    AcademyError,
    AcademyViewer,
    academy_visible,
    course_outline,
    course_slug_for_product,
    list_courses,
    load_viewer,
    preview_admin_ids,
    set_lesson_done,
)
from app.db import tenant_connection
from app.settings import get_settings
from app.telegram.api_base import TelegramApiBaseError, telegram_bot_api_url
from app.telegram.bindings import current_bot_binding
from app.telegram.delivery import answer_callback_query, send_telegram_text
from app.telegram.site_login import with_site_login
from app.telegram.update_parser import TelegramCallbackQuery, TelegramMessage
from app.tenancy import TenantContext

logger = logging.getLogger(__name__)

MOSCOW = ZoneInfo("Europe/Moscow")  # даты в сообщениях бота — по Москве, как в billing

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
_KEYS_RE = re.compile(r"^/?ключи(?:\s+([a-z0-9]+(?:-[a-z0-9]+)*))?(?:\s+(\d{1,4}))?\s*$", re.I)
_MY_COURSES_RE = re.compile(r"^/?мои\s+курсы\s*$", re.I)
KEYS_INLINE_LIMIT = 20  # больше — одним текстовым файлом (сообщение Telegram ≤ 4096 символов)

LOCK_TEXT = {
    "pro_required": "Академия входит в PRO — платформу вашего сайта. Продлите PRO, и курс откроется сразу.",
    "purchase_required": "Этот курс продаётся отдельно. Напишите в поддержку, чтобы подключить.",
    "academy_not_open": "Академия скоро откроется. Мы сообщим, когда курс будет доступен.",
}


def lesson_url(course_slug: str, lesson_slug: str) -> str:
    base = str(get_settings().platform_academy_site_base or "https://wwc.best").rstrip("/")
    return f"{base}/academy/?{urlencode({'course': course_slug, 'lesson': lesson_slug})}"


def is_academy_text(text: str) -> bool:
    value = str(text or "").strip()
    return bool(_TEXT_RE.match(value) or _KEYS_RE.match(value) or _MY_COURSES_RE.match(value))


def author_contact_label(contact: dict[str, Any] | None) -> str:
    if not contact:
        return ""
    if contact.get("telegram"):
        return f"@{contact['telegram']}"
    return str(contact.get("site_url") or "")


def key_error_text(exc: AcademyKeyError) -> str:
    if exc.code == "author_shelf_expired":
        who = author_contact_label(exc.extra.get("author_contact"))
        if who:
            return f"Автор курса не продлил размещение — напишите ему: {who}. Ключ не потрачен."
    return KEY_ERROR_TEXT.get(exc.code, KEY_ERROR_TEXT["key_not_found"])


def purchase_lock_text(contact: dict[str, Any] | None) -> str:
    """Замок purchase_required: доступ выдаёт автор курса («полка»), не поддержка."""
    who = author_contact_label(contact)
    if not who:
        return LOCK_TEXT["purchase_required"]
    return (
        f"Доступ к этому курсу выдаёт его автор: {who}.\n"
        "Получите у автора ключ — ссылку на бота — и откройте её: курс откроется сразу. "
        "Если у вас код из 12 символов, отправьте боту: /start course_<код>"
    )


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


async def _show_lock(chat_id: int, reason: str, author_contact: dict[str, Any] | None = None) -> None:
    keyboard = None
    if reason == "pro_required":
        keyboard = {"inline_keyboard": [[{"text": "Продлить платформу", "callback_data": "renew:start"}]]}
    if reason == "purchase_required":
        await _send(chat_id, purchase_lock_text(author_contact))
        return
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
            await _show_lock(chat_id, course["lock_reason"], course.get("author_contact"))
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
        await _show_lock(
            callback.chat_id,
            exc.code if exc.code in LOCK_TEXT else "academy_not_open",
            exc.extra.get("author_contact"),
        )
        return {"ok": False, "route": "academy", "status": exc.code, "trace_id": trace_id}
    return {"ok": True, "route": "academy", **result, "trace_id": trace_id}


async def try_handle_academy_text(tenant: TenantContext, msg: TelegramMessage, *, trace_id: str) -> dict[str, Any] | None:
    """Old «обучение» commands → the Academy, but only where it is open.
    «ключи …» and «мои курсы» — the author's tools (the shelf)."""
    text = str(msg.text or "").strip()
    if not is_academy_text(text):
        return None
    keys = _KEYS_RE.match(text)
    if keys:
        slug, count = keys.groups()
        if slug and slug.isdigit() and not count:  # «ключи 5» — без адреса курса
            slug, count = None, None
        return await handle_keys_command(
            tenant, msg, (slug or "").lower() or None, int(count) if count else None, trace_id=trace_id
        )
    if _MY_COURSES_RE.match(text):
        mine = await handle_my_courses(tenant, msg, trace_id=trace_id)
        if mine is not None:
            return mine
        # Не автор: «мои курсы» — это курсы ученика, то есть Академия.
    try:
        viewer = await load_viewer(tenant.tenant_id, msg.user_id)
    except Exception:  # no Academy lookup → the old onboarding answers as before
        return None
    if not academy_visible(viewer):
        return None
    result = await show_academy_home(tenant.tenant_id, msg.chat_id, msg.user_id)
    return {"ok": True, "route": "academy_text", **result, "trace_id": trace_id}


def _date(value: datetime | None) -> str:
    return value.astimezone(MOSCOW).strftime("%d.%m.%Y") if value else ""


async def _send_text_file(chat_id: int, filename: str, content: str, caption: str) -> dict[str, Any]:
    """sendDocument with an in-memory text file (Bot API needs multipart for uploads)."""
    binding = current_bot_binding()
    try:
        url = telegram_bot_api_url(binding.bot_token, "sendDocument")
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                url,
                data={"chat_id": str(chat_id), "caption": caption[:1024]},
                files={"document": (filename, content.encode("utf-8"), "text/plain")},
            )
        data = response.json() if response.text else {}
    except (httpx.HTTPError, ValueError, TelegramApiBaseError):
        # Ключи уже созданы: не роняем обработчик (повтор апдейта), отдаём сообщениями.
        logger.warning("academy_keys_file_failed", exc_info=True)
        return {"ok": False}
    if response.status_code >= 400 or not isinstance(data, dict) or not data.get("ok"):
        logger.warning("academy_keys_file_failed", extra={"status": response.status_code})
        return {"ok": False, "status_code": response.status_code}
    return {"ok": True}


async def _usage_text(tenant_id: str, telegram_user_id: int, is_admin: bool) -> str:
    actor_ids = await author_actor_ids(tenant_id, telegram_user_id)
    courses = await author_course_stats(tenant_id, None if is_admin else actor_ids) if (actor_ids or is_admin) else []
    lines = [
        "Ключи доступа к курсу: напишите «ключи <адрес курса> <сколько>».",
        f"Например: «ключи {courses[0]['slug'] if courses else 'moy-kurs'} 5». До {MAX_KEYS_PER_BATCH} за раз, каждый ключ — на одного ученика.",
    ]
    if courses:
        lines += ["", "Ваши курсы:"] + [f"• {row['slug']} — {row['title']}" for row in courses]
    return "\n".join(lines)


async def handle_keys_command(
    tenant: TenantContext, msg: TelegramMessage, slug: str | None, count: int | None, *, trace_id: str
) -> dict[str, Any]:
    """«ключи <slug> <N>» от автора курса (полка оплачена) или владельца."""
    tenant_id = tenant.tenant_id
    is_admin = int(msg.user_id) in preview_admin_ids()
    if not slug:
        await _send(msg.chat_id, await _usage_text(tenant_id, msg.user_id, is_admin))
        return {"ok": True, "route": "academy_keys", "status": "usage", "trace_id": trace_id}
    binding = current_bot_binding()
    # Повтор того же апдейта (inbox-воркер) отдаёт ту же пачку, а не новую.
    batch = f"{binding.binding_id}:{msg.chat_id}:{msg.message_id}" if msg.message_id else None
    try:
        issued = await issue_keys_for_telegram(
            tenant_id, slug, msg.user_id, 1 if count is None else count, is_admin=is_admin,
            issued_for_message=batch,
        )
    except AcademyKeyError as exc:
        await _send(msg.chat_id, KEY_ERROR_TEXT.get(exc.code, KEY_ERROR_TEXT["course_not_found"]))
        return {"ok": False, "route": "academy_keys", "status": exc.code, "trace_id": trace_id}
    links = [course_start_link(binding.bot_username, code) for code in issued.codes]
    head = (
        f"🔑 Ключи к курсу «{issued.course_title}»: {len(links)} шт.\n"
        "Каждый ключ — для одного ученика: отправьте ему его ссылку. "
        "Ученик нажмёт её — курс откроется в боте и на сайте."
    )
    if len(links) <= KEYS_INLINE_LIMIT:
        await _send(msg.chat_id, head + "\n\n" + "\n".join(f"{i}. {link}" for i, link in enumerate(links, 1)))
        return {"ok": True, "route": "academy_keys", "status": "issued", "count": len(links), "trace_id": trace_id}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    body = "\n".join(f"{code}\t{link}" for code, link in zip(issued.codes, links)) + "\n"
    sent = await _send_text_file(msg.chat_id, f"keys-{issued.course_slug}-{stamp}.txt", body, head)
    if not sent.get("ok"):
        # Файл не ушёл — ключи уже созданы, отдаём их сообщениями по 20.
        for start in range(0, len(links), KEYS_INLINE_LIMIT):
            chunk = links[start:start + KEYS_INLINE_LIMIT]
            await _send(msg.chat_id, "\n".join(f"{start + i}. {link}" for i, link in enumerate(chunk, 1)))
    return {"ok": True, "route": "academy_keys", "status": "issued_file", "count": len(links), "trace_id": trace_id}


_STATUS_LABEL = {"draft": "черновик — на проверке у владельца", "published": "опубликован"}


async def handle_my_courses(tenant: TenantContext, msg: TelegramMessage, *, trace_id: str) -> dict[str, Any] | None:
    """«мои курсы» автора: статус, ключи, ученики — без имён. None — человек не автор."""
    tenant_id = tenant.tenant_id
    is_admin = int(msg.user_id) in preview_admin_ids()
    actor_ids = await author_actor_ids(tenant_id, msg.user_id)
    courses = await author_course_stats(tenant_id, actor_ids) if actor_ids else []
    if not courses and is_admin:
        courses = await author_course_stats(tenant_id, None)
    if not courses:
        return None
    lines = ["🎓 Мои курсы"]
    paid_until = await shelf_paid_until(tenant_id, actor_ids)
    if paid_until and paid_until > datetime.now(timezone.utc):
        lines.append(f"Полка Академии оплачена до {_date(paid_until)}.")
    elif not is_admin:
        lines.append("Полка Академии не оплачена — новые ключи не выдаются. Ученики с доступом продолжают учиться.")
    for row in courses:
        lines += [
            "",
            f"«{row['title']}» ({row['slug']}) — {_STATUS_LABEL.get(str(row['status']), str(row['status']))}",
            f"Ключи: погашено {int(row['keys_used'])} из {int(row['keys_capacity'])} · учеников: {int(row['students'])}",
        ]
    lines += ["", "Новые ключи: «ключи <адрес курса> <сколько>»."]
    await _send(msg.chat_id, "\n".join(lines))
    return {"ok": True, "route": "academy_my_courses", "status": "shown", "courses": len(courses), "trace_id": trace_id}


async def handle_course_start_token(
    tenant: TenantContext, msg: TelegramMessage, token: str, *, trace_id: str
) -> dict[str, Any]:
    """``/start course_<код>`` — ученик открывает курс ключом автора."""
    code = parse_course_start_token(token)
    if code is None:
        raise ValueError("not a course start token")
    if msg.chat_type != "private":
        return {"ok": True, "route": "academy_key", "status": "private_chat_required", "trace_id": trace_id}
    if not code:
        await _send(msg.chat_id, KEY_ERROR_TEXT["key_not_found"])
        return {"ok": False, "route": "academy_key", "status": "key_not_found", "trace_id": trace_id}
    tenant_id = tenant.tenant_id
    try:
        result = await redeem_key(tenant_id, code, msg.user_id)
    except AcademyKeyError as exc:
        await _send(msg.chat_id, key_error_text(exc))
        return {"ok": False, "route": "academy_key", "status": exc.code, "trace_id": trace_id}
    if result.status == "already_open":
        text = f"Курс «{result.course_title}» у вас уже открыт — ключ не потрачен."
    else:
        text = f"🎓 Курс «{result.course_title}» открыт."
    keyboard = None
    try:
        viewer = await load_viewer(tenant_id, msg.user_id)
        outline = await course_outline(tenant_id, result.course_slug, viewer)
        lessons = outline["lessons"]
        lesson = next((row for row in lessons if not row["done"]), lessons[0] if lessons else None)
        if lesson:
            url = await with_site_login(
                lesson_url(result.course_slug, lesson["slug"]), tenant_id=tenant_id, telegram_user_id=msg.user_id
            )
            label = "📖 Открыть первый урок" if lesson["position"] == lessons[0]["position"] else "📖 Продолжить урок"
            keyboard = {
                "inline_keyboard": [
                    [{"text": label, "url": url}],
                    [{"text": "🎓 Курс в боте", "callback_data": f"acad:c:{result.course_slug}"}],
                ]
            }
    except AcademyError:
        # Академия закрыта превью — доступ записан, урок откроется, когда её откроют.
        keyboard = None
    await _send(msg.chat_id, text, keyboard)
    return {"ok": True, "route": "academy_key", "status": result.status, "course": result.course_slug, "trace_id": trace_id}


def is_academy_payment(payment: dict[str, Any] | None) -> bool:
    product = str((payment or {}).get("product_code") or "")
    return product == SHELF_PRODUCT_CODE or product.startswith("course_")


async def notify_academy_payment(payment: dict[str, Any], *, chat_id: int, title: str) -> None:
    """Оплата полки или курса подтверждена. Общее «Сайт: … Доступ до: …» здесь
    врёт: у полки свой срок, курс — без срока."""
    product = str(payment.get("product_code") or "")
    head = f"Оплата подтверждена: {title}." if title else "Оплата подтверждена."
    if product == SHELF_PRODUCT_CODE:
        lines = [
            head,
            f"Полка Академии оплачена до {_date(payment.get('period_end'))}.",
            "Ключи ученикам: «ключи <адрес курса> <сколько>». Статистика: «мои курсы».",
        ]
    else:
        async with tenant_connection(str(payment["tenant_id"])) as conn:
            course_slug = await course_slug_for_product(conn, str(payment["tenant_id"]), product)
        lines = [
            head,
            "Курс открыт в Академии: /cabinet → «🎓 Академия»."
            if course_slug
            else "Курс появится в Академии, как только будет готов, — мы сообщим.",
        ]
    await _send(chat_id, "\n".join(lines))
