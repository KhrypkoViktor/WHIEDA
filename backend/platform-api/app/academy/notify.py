"""Academy notifications in the bot: through the outbox, like the CRM morning message.

A homework handed in → the course author (no author: the billing owner); reviewed →
the student. A ``platform_outbox`` row (status «scheduled», ``due_at = now``) with
the bot that sends it (``PLATFORM_ACADEMY_NOTIFY_BINDING``, otherwise the first of
``PLATFORM_SCHEDULED_NOTIFY_BINDINGS``) and a ``site_button``: the job worker turns it
into a button that signs the person in on the site (``with_site_login``) when the
message goes out — no login token waits in the database. No bot configured → no
notification (the homework itself is saved).

Links carry the place in the query, not in the fragment: ``with_site_login`` puts
its own ``#wwc-login=…`` there. Author: ``/academy/author/?view=inbox&submission=<id>``;
student: the lesson ``/academy/?course=…&lesson=…``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from app.db import fetch_one
from app.jobs.outbox import enqueue_outbox_event
from app.settings import get_settings

logger = logging.getLogger(__name__)

SUBMITTED_EVENT = "academy_hw_submitted"
REVIEWED_EVENT = "academy_hw_reviewed"
OPEN_BUTTON = "Открыть"
OPEN_LESSON_BUTTON = "Открыть урок"
# Премодерация курсов (решение лида 02.10): карточка владельцу и ответ автору.
COURSE_REVIEW_EVENT = "academy_course_review"
COURSE_REVIEWED_EVENT = "academy_course_reviewed"
VIEW_COURSE_BUTTON = "Посмотреть курс"
OPEN_CABINET_BUTTON = "Открыть кабинет"
REVIEW_CALLBACK_PREFIX = "acadrev"


def notify_binding_id() -> str | None:
    settings = get_settings()
    explicit = settings.platform_academy_notify_binding.strip()
    if explicit:
        return explicit
    scheduled = settings.parsed_scheduled_notify_bindings()
    return scheduled[0] if scheduled else None


def _site_base() -> str:
    return str(get_settings().platform_academy_site_base or "https://wwc.best").rstrip("/")


def author_inbox_url(submission_id: str) -> str:
    return f"{_site_base()}/academy/author/?{urlencode({'view': 'inbox', 'submission': submission_id})}"


def lesson_page_url(course_slug: str, lesson_slug: str) -> str:
    return f"{_site_base()}/academy/?{urlencode({'course': course_slug, 'lesson': lesson_slug})}"


def course_page_url(course_slug: str) -> str:
    return f"{_site_base()}/academy/?{urlencode({'course': course_slug})}"


def author_course_url(course_slug: str) -> str:
    return f"{_site_base()}/academy/author/?{urlencode({'course': course_slug})}"


def lessons_word(count: int) -> str:
    n = abs(int(count))
    if n % 10 == 1 and n % 100 != 11:
        return "урок"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "урока"
    return "уроков"


def course_review_text(title: str, author_name: str | None, lessons: int) -> str:
    return f"📝 Курс на проверку: «{title}», автор {author_name or 'без имени'}, {lessons} {lessons_word(lessons)}"


def course_review_markup(course_id: str) -> dict[str, Any]:
    return {
        "inline_keyboard": [[
            {"text": "Опубликовать", "callback_data": f"{REVIEW_CALLBACK_PREFIX}:ok:{course_id}"},
            {"text": "Вернуть", "callback_data": f"{REVIEW_CALLBACK_PREFIX}:no:{course_id}"},
        ]]
    }


def course_reviewed_text(status: str, title: str, note: str | None) -> str:
    if status == "published":
        return f"✅ Курс «{title}» опубликован."
    return f"↩️ Курс «{title}» вернули на доработку:\n{str(note or '').strip()}"


def owner_telegram_id() -> int | None:
    """Кто проверяет курсы: владелец (billing owner), иначе первый супер-админ."""
    settings = get_settings()
    if settings.platform_billing_owner_telegram_id:
        return int(settings.platform_billing_owner_telegram_id)
    admins = sorted(settings.parsed_super_admin_telegram_ids())
    return admins[0] if admins else None


def submitted_text(student_name: str | None, lesson_title: str, course_title: str) -> str:
    who = student_name or "ученика"
    return f"📝 Домашка от {who} — урок «{lesson_title}»\nКурс: {course_title}"


def reviewed_text(status: str, lesson_title: str, comment: str | None) -> str:
    note = str(comment or "").strip()
    if status == "accepted":
        return f"✅ Домашка принята — урок «{lesson_title}»." + (f"\n{note}" if note else "")
    return f"↩️ Домашку вернули — урок «{lesson_title}»:\n{note}"


async def person(conn: Any, tenant_id: str, telegram_user_id: int) -> dict[str, Any]:
    """Name and private chat of a Telegram person (the chat id is the user id when unknown)."""
    row = await fetch_one(
        conn,
        """
        select display_name, telegram_username, telegram_chat_id from lead_actors
        where tenant_id = %s and (telegram_user_id = %s or telegram_chat_id = %s)
        order by active desc, (telegram_user_id = %s) desc
        limit 1
        """,
        (tenant_id, int(telegram_user_id), str(int(telegram_user_id)), int(telegram_user_id)),
    )
    name = str((row or {}).get("display_name") or "").strip()
    username = str((row or {}).get("telegram_username") or "").strip().lstrip("@")
    return {
        "name": name or (f"@{username}" if username else None),
        "username": username or None,
        "chat_id": str((row or {}).get("telegram_chat_id") or int(telegram_user_id)),
    }


async def author_telegram_id(conn: Any, tenant_id: str, author_actor_id: str | None) -> int | None:
    if author_actor_id:
        row = await fetch_one(
            conn,
            "select telegram_user_id from lead_actors where tenant_id = %s and actor_id = %s",
            (tenant_id, str(author_actor_id)),
        )
        if row and row.get("telegram_user_id") is not None:
            return int(row["telegram_user_id"])
    owner = get_settings().platform_billing_owner_telegram_id
    return int(owner) if owner else None


async def enqueue_notification(
    conn: Any,
    tenant_id: str,
    *,
    event_type: str,
    key: str,
    recipient: int,
    text: str,
    button_text: str,
    url: str,
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    binding_id = notify_binding_id()
    if not binding_id:
        logger.info("academy_notify_skipped_no_binding", extra={"tenant_id": tenant_id, "event_type": event_type})
        return False
    target = await person(conn, tenant_id, recipient)
    await enqueue_outbox_event(
        conn,
        tenant_id=tenant_id,
        event_type=event_type,
        idempotency_key=key,
        payload={
            "chat_id": target["chat_id"],
            "text": text,
            "binding_id": binding_id,
            "site_button": {"text": button_text, "url": url, "telegram_user_id": int(recipient)},
            **({"reply_markup": reply_markup} if reply_markup else {}),
        },
        due_at=datetime.now(timezone.utc),
    )
    return True
