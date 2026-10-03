"""Academy v2 homework: the student hands in, the author accepts or returns.

One active submission per (lesson, student) — «submitted» or «accepted»: while it
waits for the author the student may still edit it (same row, no second
notification); after «returned» a new submission is a new row. Handing in counts
as «Сделал» for the lesson. A lesson with a required homework is complete only
when the latest submission is accepted (``app.academy.rules``).

Photos: the student's own uploads (``/media/init`` …), ready images or files.
Who reviews: the course author (any actor row of the person) or the owner /
preview admins; a course without an author goes to the billing owner.
"""

from __future__ import annotations

import json
from datetime import timezone
from typing import Any

from app.academy.content import sanitize_html
from app.academy.media_service import _valid_ids, load_media, media_summary
from app.academy.notify import (
    OPEN_BUTTON,
    OPEN_LESSON_BUTTON,
    REVIEWED_EVENT,
    SUBMITTED_EVENT,
    author_inbox_url,
    author_telegram_id,
    enqueue_notification,
    lesson_page_url,
    person,
    reviewed_text,
    submitted_text,
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
from app.db import fetch_all, fetch_one, tenant_connection

MAX_TEXT_CHARS = 20_000
MAX_COMMENT_CHARS = 4_000
MAX_MEDIA = 10
REVIEW_STATUSES = ("accepted", "returned")


async def submit_homework(
    tenant_id: str, slug: str, lesson_slug: str, viewer: AcademyViewer, *, text: str | None, media_ids: list[Any]
) -> dict[str, Any]:
    body = str(text or "").strip()
    wanted = _valid_ids(list(media_ids or []))
    if len(body) > MAX_TEXT_CHARS:
        raise AcademyError(400, "text_too_long", {"limit": MAX_TEXT_CHARS})
    if len(list(media_ids or [])) > MAX_MEDIA:
        raise AcademyError(400, "too_many_files", {"limit": MAX_MEDIA})
    if len(wanted) != len(list(media_ids or [])):
        raise AcademyError(400, "bad_media")
    if not body and not wanted:
        raise AcademyError(400, "empty_submission")
    async with tenant_connection(tenant_id) as conn:
        view, lesson = await open_lesson(conn, tenant_id, slug, lesson_slug, viewer)
        assignment = await fetch_one(
            conn,
            "select required from academy_assignments where tenant_id = %s and lesson_id = %s::uuid",
            (tenant_id, lesson["lesson_id"]),
        )
        if not assignment:
            raise AcademyError(404, "assignment_not_found")
        media = await load_media(conn, tenant_id, wanted)
        for media_id in wanted:
            row = media.get(media_id)
            if (
                not row
                or row.get("owner_telegram_user_id") != viewer.telegram_user_id
                or row["status"] != "ready"
                or row["kind"] not in ("image", "file")
            ):
                raise AcademyError(400, "bad_media", {"media_id": media_id})
        saved = await fetch_one(
            conn,
            """
            insert into academy_submissions (tenant_id, lesson_id, telegram_user_id, text, media)
            values (%s, %s::uuid, %s, %s, %s::jsonb)
            on conflict (tenant_id, lesson_id, telegram_user_id) where status <> 'returned'
            do update set text = excluded.text, media = excluded.media, updated_at = now()
              where academy_submissions.status = 'submitted'
            returning submission_id::text as submission_id, text, media, status, author_comment,
                      created_at, updated_at, reviewed_at, (xmax = 0) as created
            """,
            (tenant_id, lesson["lesson_id"], viewer.telegram_user_id, body, json.dumps(wanted)),
        )
        if not saved:
            raise AcademyError(409, "already_accepted")
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into academy_progress (tenant_id, telegram_user_id, lesson_id, source)
                values (%s, %s, %s::uuid, 'site')
                on conflict (tenant_id, telegram_user_id, lesson_id) do nothing
                """,
                (tenant_id, viewer.telegram_user_id, lesson["lesson_id"]),
            )
        if saved["created"]:
            recipient = await author_telegram_id(conn, tenant_id, view.course.get("author_actor_id"))
            if recipient is not None:
                student = await person(conn, tenant_id, viewer.telegram_user_id)
                await enqueue_notification(
                    conn,
                    tenant_id,
                    event_type=SUBMITTED_EVENT,
                    key=f"{SUBMITTED_EVENT}:{saved['submission_id']}",
                    recipient=recipient,
                    text=submitted_text(student["name"], lesson["title"], view.course["title"]),
                    button_text=OPEN_BUTTON,
                    url=author_inbox_url(saved["submission_id"]),
                )
        progress = await course_progress(conn, tenant_id, view, viewer.telegram_user_id)
    return {
        "ok": True,
        "submission": submission_out(saved, media, viewer.telegram_user_id),
        "assignment_status": saved["status"],
        **progress,
    }


async def _author_scope(conn: Any, tenant_id: str, viewer: AcademyViewer) -> list[str] | None:
    """None — every course (owner / preview admin); otherwise the person's actor ids.
    AcademyError(403) when the person authors nothing."""
    if viewer.is_preview_admin:
        return None
    actor_ids = await viewer_actor_ids(conn, tenant_id, viewer.telegram_user_id)
    if actor_ids:
        row = await fetch_one(
            conn,
            "select 1 as ok from academy_courses where tenant_id = %s and author_actor_id = any(%s::text[]) limit 1",
            (tenant_id, actor_ids),
        )
        if row:
            return actor_ids
    raise AcademyError(403, "not_author")


def _iso(value: Any) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


async def list_submissions(
    tenant_id: str,
    viewer: AcademyViewer,
    *,
    status: str = "submitted",
    course_slug: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """The author's inbox: newest first. ``status`` — submitted | accepted | returned | all."""
    if status not in ("submitted", "accepted", "returned", "all"):
        raise AcademyError(400, "bad_status")
    async with tenant_connection(tenant_id) as conn:
        scope = await _author_scope(conn, tenant_id, viewer)
        rows = await fetch_all(
            conn,
            """
            select s.submission_id::text as submission_id, s.telegram_user_id, s.text, s.media, s.status,
                   s.author_comment, s.created_at, s.updated_at, s.reviewed_at,
                   l.slug as lesson_slug, l.title as lesson_title, c.slug as course_slug, c.title as course_title,
                   coalesce(la.display_name, '') as student_display_name, la.telegram_username as student_username,
                   a.prompt_html
            from academy_submissions s
            join academy_lessons l on l.tenant_id = s.tenant_id and l.lesson_id = s.lesson_id
            join academy_courses c on c.tenant_id = l.tenant_id and c.course_id = l.course_id
            left join academy_assignments a on a.tenant_id = l.tenant_id and a.lesson_id = l.lesson_id
            left join lateral (
              select display_name, telegram_username from lead_actors
              where tenant_id = s.tenant_id and telegram_user_id = s.telegram_user_id
              order by active desc limit 1
            ) la on true
            where s.tenant_id = %(tenant)s
              and (%(status)s = 'all' or s.status = %(status)s)
              and (%(course)s::text is null or c.slug = %(course)s::text)
              and (%(scope)s::text[] is null or c.author_actor_id = any(%(scope)s::text[]))
            order by s.created_at desc
            limit %(limit)s
            """,
            {"tenant": tenant_id, "status": status, "course": course_slug, "scope": scope, "limit": int(limit)},
        )
        media = await load_media(conn, tenant_id, [item for row in rows for item in (row.get("media") or [])])
    out = []
    for row in rows:
        username = str(row.get("student_username") or "").strip().lstrip("@") or None
        name = str(row.get("student_display_name") or "").strip() or (f"@{username}" if username else "Ученик")
        out.append(
            {
                "submission_id": row["submission_id"],
                "status": row["status"],
                "text": row.get("text") or "",
                "media": [
                    media_summary(media[str(media_id)], viewer.telegram_user_id)
                    for media_id in (row.get("media") or [])
                    if str(media_id) in media
                ],
                "author_comment": row.get("author_comment"),
                "created_at": _iso(row.get("created_at")),
                "reviewed_at": _iso(row.get("reviewed_at")),
                "student_name": name,
                "student_username": username,
                "course_slug": row["course_slug"],
                "course_title": row["course_title"],
                "lesson_slug": row["lesson_slug"],
                "lesson_title": row["lesson_title"],
                # Задание урока — в той же карточке проверки (как в GET урока).
                "prompt_html": sanitize_html(row["prompt_html"]) if row.get("prompt_html") is not None else None,
            }
        )
    return out


async def review_submission(
    tenant_id: str, submission_id: str, viewer: AcademyViewer, *, status: str, comment: str | None
) -> dict[str, Any]:
    if status not in REVIEW_STATUSES:
        raise AcademyError(400, "bad_status")
    note = str(comment or "").strip()
    if len(note) > MAX_COMMENT_CHARS:
        raise AcademyError(400, "comment_too_long", {"limit": MAX_COMMENT_CHARS})
    if status == "returned" and not note:
        raise AcademyError(400, "comment_required")
    wanted = _valid_ids([submission_id])
    async with tenant_connection(tenant_id) as conn:
        row = None
        if wanted:
            row = await fetch_one(
                conn,
                """
                select s.submission_id::text as submission_id, s.telegram_user_id, s.status,
                       l.slug as lesson_slug, l.title as lesson_title, c.slug as course_slug, c.author_actor_id
                from academy_submissions s
                join academy_lessons l on l.tenant_id = s.tenant_id and l.lesson_id = s.lesson_id
                join academy_courses c on c.tenant_id = l.tenant_id and c.course_id = l.course_id
                where s.tenant_id = %s and s.submission_id = %s::uuid
                for update of s
                """,
                (tenant_id, wanted[0]),
            )
        if not row:
            raise AcademyError(404, "submission_not_found")
        if not is_staff_for(row, viewer, await viewer_actor_ids(conn, tenant_id, viewer.telegram_user_id)):
            raise AcademyError(403, "not_author")
        if row["status"] != "submitted":
            raise AcademyError(409, "already_reviewed", {"status": row["status"]})
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update academy_submissions
                set status = %s, author_comment = %s, reviewed_at = now(), reviewed_by = %s, updated_at = now()
                where tenant_id = %s and submission_id = %s::uuid
                """,
                (status, note or None, viewer.telegram_user_id, tenant_id, row["submission_id"]),
            )
        await enqueue_notification(
            conn,
            tenant_id,
            event_type=REVIEWED_EVENT,
            key=f"{REVIEWED_EVENT}:{row['submission_id']}",
            recipient=int(row["telegram_user_id"]),
            text=reviewed_text(status, row["lesson_title"], note),
            button_text=OPEN_LESSON_BUTTON,
            url=lesson_page_url(row["course_slug"], row["lesson_slug"]),
        )
    return {"ok": True, "submission_id": row["submission_id"], "status": status}
