"""WWC Academy: courses, lessons, access and progress.

One course is shown on the site (/academy/) and in the bot (cabinet →
«Академия»); a person is a telegram_user_id, so progress is shared. The site
reaches this through the content-access session, the bot through the update.

Access (V1):
- preview (PLATFORM_ACADEMY_OPEN=false): only the billing owner and super
  admins — the owner checks the course before partners see it;
- access_rule 'pro'      — partner with paid PRO (partner_paid);
- access_rule 'free'     — anyone signed in;
- access_rule 'purchase' — a row in academy_access (purchase / gift / admin).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.db import fetch_all, fetch_one, tenant_connection
from app.settings import get_settings
from app.subscriptions.service import resolve_partner_subscription_by_telegram_user_id


@dataclass(frozen=True)
class AcademyViewer:
    telegram_user_id: int
    is_preview_admin: bool
    partner_paid: bool


def preview_admin_ids() -> frozenset[int]:
    settings = get_settings()
    ids = set(settings.parsed_super_admin_telegram_ids())
    if settings.platform_billing_owner_telegram_id:
        ids.add(int(settings.platform_billing_owner_telegram_id))
    return frozenset(ids)


def academy_open() -> bool:
    return bool(get_settings().platform_academy_open)


async def load_viewer(tenant_id: str, telegram_user_id: int) -> AcademyViewer:
    is_admin = int(telegram_user_id) in preview_admin_ids()
    paid = False
    subscription = await resolve_partner_subscription_by_telegram_user_id(
        tenant_id, int(telegram_user_id), on_ambiguous="best"
    )
    if subscription and subscription.get("partner_paid"):
        paid = True
    return AcademyViewer(telegram_user_id=int(telegram_user_id), is_preview_admin=is_admin, partner_paid=paid)


def academy_visible(viewer: AcademyViewer) -> bool:
    """Whether the Academy exists for this person at all (preview gate)."""
    return viewer.is_preview_admin or academy_open()


def course_lock_reason(course: dict[str, Any], viewer: AcademyViewer, *, has_access_row: bool) -> str | None:
    """None — course is open for the viewer; otherwise a machine reason."""
    if not academy_visible(viewer):
        return "academy_not_open"
    if viewer.is_preview_admin:
        return None
    rule = str(course.get("access_rule") or "pro")
    if rule == "free":
        return None
    if rule == "pro":
        return None if viewer.partner_paid else "pro_required"
    return None if has_access_row else "purchase_required"


async def _access_rows(conn: Any, tenant_id: str, telegram_user_id: int) -> set[str]:
    rows = await fetch_all(
        conn,
        """
        select course_id::text as course_id
        from academy_access
        where tenant_id = %s and telegram_user_id = %s and revoked_at is null
        """,
        (tenant_id, telegram_user_id),
    )
    return {row["course_id"] for row in rows}


async def list_courses(tenant_id: str, viewer: AcademyViewer) -> list[dict[str, Any]]:
    if not academy_visible(viewer):
        return []
    async with tenant_connection(tenant_id) as conn:
        courses = await fetch_all(
            conn,
            """
            select c.course_id::text as course_id, c.slug, c.title, c.subtitle, c.access_rule,
                   c.price_wusd_minor,
                   count(l.lesson_id) filter (where l.status = 'published') as lessons_total,
                   count(p.lesson_id) as lessons_done
            from academy_courses c
            left join academy_lessons l
              on l.tenant_id = c.tenant_id and l.course_id = c.course_id
            left join academy_progress p
              on p.tenant_id = l.tenant_id and p.lesson_id = l.lesson_id
             and p.telegram_user_id = %s and l.status = 'published'
            where c.tenant_id = %s and c.status = 'published'
            group by c.course_id, c.slug, c.title, c.subtitle, c.access_rule, c.price_wusd_minor, c.sort_order
            order by c.sort_order, c.title
            """,
            (viewer.telegram_user_id, tenant_id),
        )
        access = await _access_rows(conn, tenant_id, viewer.telegram_user_id)
    out = []
    for course in courses:
        lock = course_lock_reason(course, viewer, has_access_row=course["course_id"] in access)
        out.append(
            {
                "slug": course["slug"],
                "title": course["title"],
                "subtitle": course.get("subtitle"),
                "access_rule": course["access_rule"],
                "lessons_total": int(course["lessons_total"] or 0),
                "lessons_done": int(course["lessons_done"] or 0),
                "locked": lock is not None,
                "lock_reason": lock,
            }
        )
    return out


async def _load_course(conn: Any, tenant_id: str, slug: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select course_id::text as course_id, slug, title, subtitle, access_rule, price_wusd_minor
        from academy_courses
        where tenant_id = %s and slug = %s and status = 'published'
        """,
        (tenant_id, slug),
    )


async def _load_lessons(conn: Any, tenant_id: str, course_id: str, telegram_user_id: int) -> list[dict[str, Any]]:
    return await fetch_all(
        conn,
        """
        select l.lesson_id::text as lesson_id, l.slug, l.module_title, l.position, l.title,
               l.short_title, l.result_text, l.est_minutes,
               (p.lesson_id is not null) as done
        from academy_lessons l
        left join academy_progress p
          on p.tenant_id = l.tenant_id and p.lesson_id = l.lesson_id and p.telegram_user_id = %s
        where l.tenant_id = %s and l.course_id = %s::uuid and l.status = 'published'
        order by l.position
        """,
        (telegram_user_id, tenant_id, course_id),
    )


class AcademyError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


async def _open_course(conn: Any, tenant_id: str, slug: str, viewer: AcademyViewer) -> dict[str, Any]:
    course = await _load_course(conn, tenant_id, slug)
    if not course or not academy_visible(viewer):
        raise AcademyError(404, "course_not_found")
    access = await _access_rows(conn, tenant_id, viewer.telegram_user_id)
    lock = course_lock_reason(course, viewer, has_access_row=course["course_id"] in access)
    if lock:
        raise AcademyError(403, lock)
    return course


def _lesson_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "slug": row["slug"],
        "position": int(row["position"]),
        "module_title": row.get("module_title") or "",
        "title": row["title"],
        "short_title": row.get("short_title") or row["title"],
        "result": row.get("result_text") or "",
        "minutes": row.get("est_minutes"),
        "done": bool(row.get("done")),
    }


def next_lesson(lessons: list[dict[str, Any]]) -> dict[str, Any] | None:
    for row in lessons:
        if not row.get("done"):
            return row
    return None


async def course_outline(tenant_id: str, slug: str, viewer: AcademyViewer) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        course = await _open_course(conn, tenant_id, slug, viewer)
        lessons = await _load_lessons(conn, tenant_id, course["course_id"], viewer.telegram_user_id)
    upcoming = next_lesson(lessons)
    return {
        "course": {
            "slug": course["slug"],
            "title": course["title"],
            "subtitle": course.get("subtitle"),
            "lessons_total": len(lessons),
            "lessons_done": sum(1 for row in lessons if row.get("done")),
            "next_lesson": upcoming["slug"] if upcoming else None,
        },
        "lessons": [_lesson_summary(row) for row in lessons],
    }


async def lesson_detail(tenant_id: str, slug: str, lesson_slug: str, viewer: AcademyViewer) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        course = await _open_course(conn, tenant_id, slug, viewer)
        lessons = await _load_lessons(conn, tenant_id, course["course_id"], viewer.telegram_user_id)
        row = await fetch_one(
            conn,
            """
            select l.lesson_id::text as lesson_id, l.slug, l.body_html, l.checklist, l.video
            from academy_lessons l
            where l.tenant_id = %s and l.course_id = %s::uuid and l.slug = %s and l.status = 'published'
            """,
            (tenant_id, course["course_id"], lesson_slug),
        )
    if not row:
        raise AcademyError(404, "lesson_not_found")
    index = next(i for i, item in enumerate(lessons) if item["slug"] == lesson_slug)
    summary = _lesson_summary(lessons[index])
    return {
        "course": {
            "slug": course["slug"],
            "title": course["title"],
            "lessons_total": len(lessons),
            "lessons_done": sum(1 for item in lessons if item.get("done")),
        },
        "lesson": {
            **summary,
            "number": index + 1,
            "body_html": row["body_html"],
            "checklist": list(row.get("checklist") or []),
            "video": row.get("video") or None,
        },
        "prev": _lesson_summary(lessons[index - 1]) if index > 0 else None,
        "next": _lesson_summary(lessons[index + 1]) if index + 1 < len(lessons) else None,
    }


async def set_lesson_done(
    tenant_id: str,
    slug: str,
    lesson_slug: str,
    viewer: AcademyViewer,
    *,
    done: bool,
    source: str,
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        course = await _open_course(conn, tenant_id, slug, viewer)
        lesson = await fetch_one(
            conn,
            """
            select lesson_id::text as lesson_id from academy_lessons
            where tenant_id = %s and course_id = %s::uuid and slug = %s and status = 'published'
            """,
            (tenant_id, course["course_id"], lesson_slug),
        )
        if not lesson:
            raise AcademyError(404, "lesson_not_found")
        async with conn.cursor() as cur:
            if done:
                await cur.execute(
                    """
                    insert into academy_progress (tenant_id, telegram_user_id, lesson_id, source)
                    values (%s, %s, %s::uuid, %s)
                    on conflict (tenant_id, telegram_user_id, lesson_id) do nothing
                    """,
                    (tenant_id, viewer.telegram_user_id, lesson["lesson_id"], source),
                )
            else:
                await cur.execute(
                    """
                    delete from academy_progress
                    where tenant_id = %s and telegram_user_id = %s and lesson_id = %s::uuid
                    """,
                    (tenant_id, viewer.telegram_user_id, lesson["lesson_id"]),
                )
        lessons = await _load_lessons(conn, tenant_id, course["course_id"], viewer.telegram_user_id)
    upcoming = next_lesson(lessons)
    return {
        "ok": True,
        "done": done,
        "lessons_total": len(lessons),
        "lessons_done": sum(1 for row in lessons if row.get("done")),
        "next_lesson": _lesson_summary(upcoming) if upcoming else None,
    }
