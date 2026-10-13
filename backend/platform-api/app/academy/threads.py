"""Домашка как переписка (V32, 13.10.2026): нить ученик ↔ преподаватель, работы группы, анкета.

Голова нити — строка ``academy_submissions`` (текст и фото сдачи, статус); ответы и
вопросы — ``academy_submission_messages``. Ученик пишет, пока работа не принята; после
«Вернули» он присылает исправления в ту же нить, и статус снова «submitted».
Преподаватель отвечает текстом, замечанием к конкретному фото (``about_media_id``),
файлами и видео; «Принять» закрывает нить. Уведомления: ученику — всегда, ботом витрины
автора курса; преподавателю — по настройке витрины (``author_notify``).

Работы группы: все нити урока с именами и ответами преподавателя — ученикам с доступом к
уроку, когда у курса ``works_public`` (решение автора ALMIRA 13.10: «группа видит
обратную связь»). Анкета ученика (``academy_profiles``) — имя, город, опыт, цель,
Instagram: её видит преподаватель в карточке и группа в работах (имя и Instagram).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from app.academy.content import sanitize_html
from app.academy.media_service import _valid_ids, load_media, media_summary
from app.academy.notify import (
    OPEN_BUTTON,
    OPEN_LESSON_BUTTON,
    author_inbox_url,
    author_telegram_id,
    enqueue_notification,
    lesson_page_url,
    person,
)
from app.academy.service import (
    AcademyError,
    AcademyViewer,
    course_progress,
    is_staff_for,
    open_lesson,
    submission_out,
    viewer_actor_ids,
)
from app.academy.showcases import showcase_for_author
from app.db import fetch_all, fetch_one, tenant_connection

MAX_MESSAGE_CHARS = 4_000
MAX_MESSAGE_MEDIA = 10
MESSAGE_EVENT = "academy_hw_message"
STUDENT_MEDIA_KINDS = ("image", "file")
AUTHOR_MEDIA_KINDS = ("image", "file", "video")
PROFILE_LIMITS = {"name": 120, "city": 120, "experience": 1000, "goal": 1000, "instagram": 64}
_INSTAGRAM_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

_MESSAGE_COLUMNS = """
    message_id::text as message_id, submission_id::text as submission_id, sender, telegram_user_id,
    text, media, about_media_id::text as about_media_id, created_at
"""


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


# ---- чтение нити -----------------------------------------------------------------------------


async def load_messages(conn: Any, tenant_id: str, submission_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    """submission_id → сообщения нити по порядку."""
    ids = _valid_ids(list(submission_ids or []))
    if not ids:
        return {}
    rows = await fetch_all(
        conn,
        f"""
        select {_MESSAGE_COLUMNS} from academy_submission_messages
        where tenant_id = %s and submission_id = any(%s::uuid[])
        order by created_at, message_id
        """,
        (tenant_id, ids),
    )
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault(str(row["submission_id"]), []).append(row)
    return out


def messages_media_ids(messages: list[dict[str, Any]]) -> list[str]:
    return [str(item) for row in messages for item in (row.get("media") or [])]


def message_out(row: dict[str, Any], media: dict[str, dict[str, Any]], telegram_user_id: int) -> dict[str, Any]:
    return {
        "message_id": row["message_id"],
        "sender": row["sender"],
        "text": row.get("text") or "",
        "media": [
            media_summary(media[str(media_id)], telegram_user_id)
            for media_id in (row.get("media") or [])
            if str(media_id) in media
        ],
        "about_media_id": row.get("about_media_id"),
        "created_at": _iso(row.get("created_at")),
    }


def thread_out(
    submission: dict[str, Any] | None,
    messages: list[dict[str, Any]],
    media: dict[str, dict[str, Any]],
    telegram_user_id: int,
) -> dict[str, Any] | None:
    """Голова нити + сообщения; ``unread`` — ответы преподавателя после последнего просмотра учеником."""
    head = submission_out(submission, media, telegram_user_id)
    if head is None:
        return None
    seen = (submission or {}).get("student_seen_at")
    head["messages"] = [message_out(row, media, telegram_user_id) for row in messages]
    head["unread"] = sum(
        1 for row in messages
        if row["sender"] == "author" and (seen is None or row["created_at"] > seen)
    )
    head["last_message_at"] = _iso((submission or {}).get("last_message_at"))
    return head


async def _check_media(
    conn: Any, tenant_id: str, media_ids: list[str], *, owner_check: Any, kinds: tuple[str, ...], statuses: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    media = await load_media(conn, tenant_id, media_ids)
    for media_id in media_ids:
        row = media.get(media_id)
        if not row or not owner_check(row) or row["status"] not in statuses or row["kind"] not in kinds:
            raise AcademyError(400, "bad_media", {"media_id": media_id})
    return media


def _clean_message(text: str | None, media_ids: list[Any]) -> tuple[str, list[str]]:
    body = str(text or "").strip()
    wanted = _valid_ids(list(media_ids or []))
    if len(body) > MAX_MESSAGE_CHARS:
        raise AcademyError(400, "text_too_long", {"limit": MAX_MESSAGE_CHARS})
    if len(list(media_ids or [])) > MAX_MESSAGE_MEDIA:
        raise AcademyError(400, "too_many_files", {"limit": MAX_MESSAGE_MEDIA})
    if len(wanted) != len(list(media_ids or [])):
        raise AcademyError(400, "bad_media")
    if not body and not wanted:
        raise AcademyError(400, "empty_message")
    return body, wanted


async def insert_message(
    conn: Any,
    tenant_id: str,
    *,
    submission_id: str,
    sender: str,
    telegram_user_id: int,
    text: str,
    media_ids: list[str],
    about_media_id: str | None = None,
) -> dict[str, Any]:
    row = await fetch_one(
        conn,
        f"""
        insert into academy_submission_messages (tenant_id, submission_id, sender, telegram_user_id, text, media, about_media_id)
        values (%s, %s::uuid, %s, %s, %s, %s::jsonb, %s::uuid)
        returning {_MESSAGE_COLUMNS}
        """,
        (tenant_id, submission_id, sender, int(telegram_user_id), text, json.dumps(media_ids), about_media_id),
    )
    return dict(row)


# ---- ученик ----------------------------------------------------------------------------------


async def _student_thread(conn: Any, tenant_id: str, lesson_id: str, telegram_user_id: int) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select submission_id::text as submission_id, text, media, status, author_comment, created_at, updated_at,
               reviewed_at, seen_at, student_seen_at, last_message_at
        from academy_submissions
        where tenant_id = %s and lesson_id = %s::uuid and telegram_user_id = %s
        order by created_at desc
        limit 1
        """,
        (tenant_id, lesson_id, int(telegram_user_id)),
    )


async def post_student_message(
    tenant_id: str, slug: str, lesson_slug: str, viewer: AcademyViewer, *, text: str | None, media_ids: list[Any]
) -> dict[str, Any]:
    """Вопрос или исправления ученика в своей нити. После «Вернули» нить снова «на проверке»."""
    body, wanted = _clean_message(text, media_ids)
    async with tenant_connection(tenant_id) as conn:
        view, lesson = await open_lesson(conn, tenant_id, slug, lesson_slug, viewer)
        head = await _student_thread(conn, tenant_id, lesson["lesson_id"], viewer.telegram_user_id)
        if head is None:
            raise AcademyError(404, "submission_not_found")
        if head["status"] == "accepted":
            raise AcademyError(409, "already_accepted")
        await _check_media(
            conn, tenant_id, wanted,
            owner_check=lambda row: row.get("owner_telegram_user_id") == viewer.telegram_user_id,
            kinds=STUDENT_MEDIA_KINDS, statuses=("ready",),
        )
        await insert_message(
            conn, tenant_id, submission_id=head["submission_id"], sender="student",
            telegram_user_id=viewer.telegram_user_id, text=body, media_ids=wanted,
        )
        reopened = head["status"] == "returned"
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update academy_submissions
                set status = case when status = 'returned' then 'submitted' else status end,
                    last_message_at = now(), updated_at = now()
                where tenant_id = %s and submission_id = %s::uuid
                """,
                (tenant_id, head["submission_id"]),
            )
        await _notify_author(
            conn, tenant_id, view.course, lesson, submission_id=head["submission_id"],
            student_id=viewer.telegram_user_id, reopened=reopened,
        )
        thread = await student_thread_payload(conn, tenant_id, lesson["lesson_id"], viewer.telegram_user_id)
        progress = await course_progress(conn, tenant_id, view, viewer.telegram_user_id)
    return {"ok": True, "submission": thread, "assignment_status": thread["status"], **progress}


async def student_thread_payload(conn: Any, tenant_id: str, lesson_id: str, telegram_user_id: int) -> dict[str, Any] | None:
    head = await _student_thread(conn, tenant_id, lesson_id, telegram_user_id)
    if head is None:
        return None
    messages = (await load_messages(conn, tenant_id, [head["submission_id"]])).get(head["submission_id"], [])
    media = await load_media(conn, tenant_id, [*(head.get("media") or []), *messages_media_ids(messages)])
    return thread_out(head, messages, media, telegram_user_id)


async def mark_student_seen(conn: Any, tenant_id: str, submission_id: str) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "update academy_submissions set student_seen_at = now() where tenant_id = %s and submission_id = %s::uuid",
            (tenant_id, submission_id),
        )


async def _notify_author(
    conn: Any, tenant_id: str, course: dict[str, Any], lesson: dict[str, Any], *,
    submission_id: str, student_id: int, reopened: bool,
) -> None:
    """Преподавателю — если витрина автора этого хочет (у ALMIRA уведомления выключены)."""
    showcase = await showcase_for_author(tenant_id, course.get("author_actor_id"))
    if not showcase.author_notify:
        return
    recipient = await author_telegram_id(conn, tenant_id, course.get("author_actor_id"))
    if recipient is None:
        return
    student = await person(conn, tenant_id, student_id)
    who = student["name"] or "ученика"
    text = (
        f"🔁 Исправления от {who} — урок «{lesson['title']}»\nКурс: {course['title']}"
        if reopened
        else f"💬 Вопрос от {who} — урок «{lesson['title']}»\nКурс: {course['title']}"
    )
    await enqueue_notification(
        conn, tenant_id,
        event_type=MESSAGE_EVENT,
        key=f"{MESSAGE_EVENT}:{submission_id}:{datetime.now(timezone.utc).isoformat()}",
        recipient=recipient, text=text, button_text=OPEN_BUTTON,
        url=author_inbox_url(submission_id, showcase.base_url), binding_id=showcase.telegram_binding_id,
    )


# ---- преподаватель ---------------------------------------------------------------------------


_THREAD_HEAD_SQL = """
    select s.submission_id::text as submission_id, s.telegram_user_id, s.text, s.media, s.status,
           s.author_comment, s.created_at, s.updated_at, s.reviewed_at, s.seen_at, s.student_seen_at, s.last_message_at,
           l.lesson_id::text as lesson_id, l.slug as lesson_slug, l.title as lesson_title,
           c.slug as course_slug, c.title as course_title, c.author_actor_id,
           coalesce(la.display_name, '') as student_display_name, la.telegram_username as student_username,
           a.prompt_html,
           p.name as profile_name, p.city as profile_city, p.experience as profile_experience,
           p.goal as profile_goal, p.instagram as profile_instagram
    from academy_submissions s
    join academy_lessons l on l.tenant_id = s.tenant_id and l.lesson_id = s.lesson_id
    join academy_courses c on c.tenant_id = l.tenant_id and c.course_id = l.course_id
    left join academy_assignments a on a.tenant_id = l.tenant_id and a.lesson_id = l.lesson_id
    left join lateral (
      select display_name, telegram_username from lead_actors
      where tenant_id = s.tenant_id and telegram_user_id = s.telegram_user_id
      order by active desc limit 1
    ) la on true
    left join academy_profiles p on p.tenant_id = s.tenant_id and p.telegram_user_id = s.telegram_user_id
    where s.tenant_id = %s and s.submission_id = %s::uuid
"""


def student_card(row: dict[str, Any]) -> dict[str, Any]:
    """Шапка ученика для преподавателя и группы: имя из анкеты, иначе из Telegram."""
    username = str(row.get("student_username") or "").strip().lstrip("@") or None
    name = (
        str(row.get("profile_name") or "").strip()
        or str(row.get("student_display_name") or "").strip()
        or (f"@{username}" if username else "Ученик")
    )
    return {
        "name": name,
        "username": username,
        "city": str(row.get("profile_city") or "").strip() or None,
        "experience": str(row.get("profile_experience") or "").strip() or None,
        "goal": str(row.get("profile_goal") or "").strip() or None,
        "instagram": str(row.get("profile_instagram") or "").strip() or None,
    }


async def _staff_thread(conn: Any, tenant_id: str, submission_id: str, viewer: AcademyViewer) -> dict[str, Any]:
    wanted = _valid_ids([submission_id])
    row = await fetch_one(conn, _THREAD_HEAD_SQL, (tenant_id, wanted[0])) if wanted else None
    if not row:
        raise AcademyError(404, "submission_not_found")
    if not is_staff_for(row, viewer, await viewer_actor_ids(conn, tenant_id, viewer.telegram_user_id)):
        raise AcademyError(403, "not_author")
    return dict(row)


async def author_thread(tenant_id: str, submission_id: str, viewer: AcademyViewer) -> dict[str, Any]:
    """Карточка работы: ученик, задание, нить. Открытие помечает работу просмотренной."""
    async with tenant_connection(tenant_id) as conn:
        row = await _staff_thread(conn, tenant_id, submission_id, viewer)
        if row.get("seen_at") is None:
            async with conn.cursor() as cur:
                await cur.execute(
                    "update academy_submissions set seen_at = now() where tenant_id = %s and submission_id = %s::uuid",
                    (tenant_id, row["submission_id"]),
                )
            row = await _staff_thread(conn, tenant_id, row["submission_id"], viewer)
        return await _author_thread_payload(conn, tenant_id, row, viewer.telegram_user_id)


async def _author_thread_payload(conn: Any, tenant_id: str, row: dict[str, Any], telegram_user_id: int) -> dict[str, Any]:
    messages = (await load_messages(conn, tenant_id, [row["submission_id"]])).get(row["submission_id"], [])
    media = await load_media(conn, tenant_id, [*(row.get("media") or []), *messages_media_ids(messages)])
    thread = thread_out(row, messages, media, telegram_user_id)
    return {
        "ok": True,
        "submission": {
            **thread,
            "seen_at": _iso(row.get("seen_at")),
            "student": student_card(row),
            "course_slug": row["course_slug"],
            "course_title": row["course_title"],
            "lesson_slug": row["lesson_slug"],
            "lesson_title": row["lesson_title"],
            "prompt_html": sanitize_html(row["prompt_html"]) if row.get("prompt_html") is not None else None,
        },
    }


async def set_seen(tenant_id: str, submission_id: str, viewer: AcademyViewer, *, seen: bool) -> dict[str, Any]:
    """«Проверено / не проверено» в почте преподавателя."""
    async with tenant_connection(tenant_id) as conn:
        row = await _staff_thread(conn, tenant_id, submission_id, viewer)
        async with conn.cursor() as cur:
            await cur.execute(
                "update academy_submissions set seen_at = case when %s then now() else null end"
                " where tenant_id = %s and submission_id = %s::uuid",
                (bool(seen), tenant_id, row["submission_id"]),
            )
    return {"ok": True, "submission_id": row["submission_id"], "seen": bool(seen)}


async def post_author_message(
    tenant_id: str,
    submission_id: str,
    viewer: AcademyViewer,
    *,
    text: str | None,
    media_ids: list[Any],
    about_media_id: str | None = None,
) -> dict[str, Any]:
    """Ответ преподавателя: текст, замечание к фото ученика, файлы и видео (видео — пока
    обрабатывается, ученик видит «обрабатывается»). Ученику — уведомление в бот."""
    body, wanted = _clean_message(text, media_ids)
    about = _valid_ids([about_media_id]) if about_media_id else []
    if about_media_id and not about:
        raise AcademyError(400, "bad_media")
    async with tenant_connection(tenant_id) as conn:
        row = await _staff_thread(conn, tenant_id, submission_id, viewer)
        if row["status"] == "accepted":
            raise AcademyError(409, "already_accepted")
        actor_ids = await viewer_actor_ids(conn, tenant_id, viewer.telegram_user_id)
        await _check_media(
            conn, tenant_id, wanted,
            owner_check=lambda m: (
                viewer.is_preview_admin
                or m.get("owner_telegram_user_id") == viewer.telegram_user_id
                or str(m.get("owner_actor_id") or "") in actor_ids
            ),
            kinds=AUTHOR_MEDIA_KINDS, statuses=("ready", "processing"),
        )
        if about:
            messages = (await load_messages(conn, tenant_id, [row["submission_id"]])).get(row["submission_id"], [])
            thread_media = {str(x) for x in (row.get("media") or [])} | set(messages_media_ids(messages))
            if about[0] not in thread_media:
                raise AcademyError(400, "bad_media", {"media_id": about[0]})
        await insert_message(
            conn, tenant_id, submission_id=row["submission_id"], sender="author",
            telegram_user_id=viewer.telegram_user_id, text=body, media_ids=wanted,
            about_media_id=about[0] if about else None,
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update academy_submissions
                set last_message_at = now(), seen_at = coalesce(seen_at, now()), updated_at = now()
                where tenant_id = %s and submission_id = %s::uuid
                """,
                (tenant_id, row["submission_id"]),
            )
        showcase = await showcase_for_author(tenant_id, row.get("author_actor_id"))
        await enqueue_notification(
            conn, tenant_id,
            event_type=MESSAGE_EVENT,
            key=f"{MESSAGE_EVENT}:{row['submission_id']}:{datetime.now(timezone.utc).isoformat()}",
            recipient=int(row["telegram_user_id"]),
            text=f"💬 Преподаватель ответил — урок «{row['lesson_title']}»\nКурс: {row['course_title']}",
            button_text=OPEN_LESSON_BUTTON,
            url=lesson_page_url(row["course_slug"], row["lesson_slug"], showcase.base_url),
            binding_id=showcase.telegram_binding_id,
        )
        fresh = await _staff_thread(conn, tenant_id, row["submission_id"], viewer)
        return await _author_thread_payload(conn, tenant_id, fresh, viewer.telegram_user_id)


# ---- работы группы ---------------------------------------------------------------------------


async def lesson_works(
    tenant_id: str, slug: str, lesson_slug: str, viewer: AcademyViewer, *, limit: int = 100
) -> dict[str, Any]:
    """Все работы урока с именами и ответами преподавателя — для учеников курса."""
    async with tenant_connection(tenant_id) as conn:
        view, lesson = await open_lesson(conn, tenant_id, slug, lesson_slug, viewer)
        if not view.course.get("works_public") and not view.staff:
            raise AcademyError(403, "works_private")
        rows = await fetch_all(
            conn,
            """
            select s.submission_id::text as submission_id, s.telegram_user_id, s.text, s.media, s.status,
                   s.author_comment, s.created_at, s.updated_at, s.reviewed_at, s.last_message_at,
                   coalesce(la.display_name, '') as student_display_name, la.telegram_username as student_username,
                   p.name as profile_name, p.city as profile_city, p.instagram as profile_instagram
            from academy_submissions s
            left join lateral (
              select display_name, telegram_username from lead_actors
              where tenant_id = s.tenant_id and telegram_user_id = s.telegram_user_id
              order by active desc limit 1
            ) la on true
            left join academy_profiles p on p.tenant_id = s.tenant_id and p.telegram_user_id = s.telegram_user_id
            where s.tenant_id = %s and s.lesson_id = %s::uuid
            order by coalesce(s.last_message_at, s.created_at) desc, s.created_at desc
            limit %s
            """,
            (tenant_id, lesson["lesson_id"], int(limit)),
        )
        messages = await load_messages(conn, tenant_id, [row["submission_id"] for row in rows])
        media = await load_media(
            conn, tenant_id,
            [
                *[item for row in rows for item in (row.get("media") or [])],
                *messages_media_ids([m for rows_ in messages.values() for m in rows_]),
            ],
        )
    works = []
    for row in rows:
        thread = thread_out(row, messages.get(row["submission_id"], []), media, viewer.telegram_user_id)
        card = student_card(row)
        works.append(
            {
                **thread,
                "student": {
                    "name": card["name"],
                    "username": card["username"],
                    "city": card["city"],
                    "instagram": card["instagram"],
                    "is_me": int(row["telegram_user_id"]) == viewer.telegram_user_id,
                },
            }
        )
    return {
        "ok": True,
        "course": {"slug": view.course["slug"], "title": view.course["title"]},
        "lesson": {"slug": lesson["slug"], "title": lesson.get("title")},
        "works": works,
    }


# ---- анкета ученика --------------------------------------------------------------------------


def _profile_out(row: dict[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    return {
        "name": str(row.get("name") or ""),
        "city": str(row.get("city") or ""),
        "experience": str(row.get("experience") or ""),
        "goal": str(row.get("goal") or ""),
        "instagram": str(row.get("instagram") or ""),
        "filled": bool(str(row.get("name") or "").strip()),
    }


def clean_instagram(raw: Any) -> str:
    value = str(raw or "").strip()
    value = re.sub(r"^https?://(www\.)?instagram\.com/", "", value, flags=re.I).strip("/@ ")
    value = value.split("?", 1)[0].split("/", 1)[0]
    if value and not _INSTAGRAM_RE.match(value):
        raise AcademyError(400, "bad_instagram")
    return value


async def get_profile(tenant_id: str, viewer: AcademyViewer) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            "select name, city, experience, goal, instagram from academy_profiles where tenant_id = %s and telegram_user_id = %s",
            (tenant_id, viewer.telegram_user_id),
        )
    return {"ok": True, "profile": _profile_out(row)}


async def save_profile(tenant_id: str, viewer: AcademyViewer, fields: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, str] = {}
    for key, limit in PROFILE_LIMITS.items():
        value = " ".join(str(fields.get(key) or "").split())
        if len(value) > limit:
            raise AcademyError(400, f"bad_{key}", {"limit": limit})
        clean[key] = value
    clean["instagram"] = clean_instagram(clean["instagram"])
    if not clean["name"]:
        raise AcademyError(400, "name_required")
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            insert into academy_profiles (tenant_id, telegram_user_id, name, city, experience, goal, instagram)
            values (%s, %s, %s, %s, %s, %s, %s)
            on conflict (tenant_id, telegram_user_id) do update set
              name = excluded.name, city = excluded.city, experience = excluded.experience,
              goal = excluded.goal, instagram = excluded.instagram, updated_at = now()
            returning name, city, experience, goal, instagram
            """,
            (tenant_id, viewer.telegram_user_id, clean["name"], clean["city"], clean["experience"], clean["goal"], clean["instagram"]),
        )
    return {"ok": True, "profile": _profile_out(row)}
