"""WWC Academy: courses, lessons, access and progress.

One course is shown on the site (/academy/) and in the bot (cabinet →
«Академия»); a person is a telegram_user_id, so progress is shared. The site
reaches this through the content-access session, the bot through the update.

Access (V1):
- preview (PLATFORM_ACADEMY_OPEN=false): only the billing owner and super
  admins — the owner checks the course before partners see it;
- access_rule 'pro'      — partner with paid PRO (partner_paid) or a row in
  academy_access (a key or a purchase opens a PRO course for a non-PRO person);
- access_rule 'free'     — anyone signed in;
- access_rule 'purchase' — a row in academy_access (purchase / gift / admin / key).

Writers of academy_access: ``grant_course_access`` (the payment path, line
``course_<код>`` → ``partner_subscription_plans.course_slug``) and
``app.academy.keys.redeem_key`` (an author's key). The author's shelf term is
extended from the payment path too (``extend_shelf_in_connection``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg

from app.db import fetch_all, fetch_one, tenant_connection
from app.settings import get_settings
from app.subscriptions.service import (
    add_calendar_months,
    resolve_partner_hostname,
    resolve_partner_subscription_by_telegram_user_id,
)

logger = logging.getLogger(__name__)

SHELF_PRODUCT_CODE = "academy_shelf"


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
        return None if viewer.partner_paid or has_access_row else "pro_required"
    return None if has_access_row else "purchase_required"


def _author_contact_from_row(row: dict[str, Any] | None) -> dict[str, str | None] | None:
    """Public contact of a course author: Telegram @username and/or the partner site."""
    if not row:
        return None
    username = str(row.get("telegram_username") or "").strip().lstrip("@")
    site_url = None
    if row.get("ref_code"):
        try:
            site_url = "https://" + resolve_partner_hostname(str(row["ref_code"]), row.get("public_profile"))
        except Exception:  # a reserved or odd subdomain: the @username is enough
            site_url = None
    if not username and not site_url:
        return None
    return {"telegram": username or None, "site_url": site_url}


async def author_contact(conn: Any, tenant_id: str, actor_id: str) -> dict[str, str | None] | None:
    return (await _author_contacts(conn, tenant_id, {str(actor_id)})).get(str(actor_id))


async def _author_contacts(conn: Any, tenant_id: str, actor_ids: set[str]) -> dict[str, dict[str, str | None]]:
    if not actor_ids:
        return {}
    rows = await fetch_all(
        conn,
        """
        select la.actor_id, la.telegram_username, rp.ref_code, rp.public_profile
        from lead_actors la
        left join lateral (
          select ref_code, public_profile from referral_profiles
          where tenant_id = la.tenant_id and owner_id = la.actor_id and enabled = true
          order by ref_code limit 1
        ) rp on true
        where la.tenant_id = %s and la.actor_id = any(%s::text[])
        """,
        (tenant_id, sorted(actor_ids)),
    )
    contacts = {}
    for row in rows:
        contact = _author_contact_from_row(row)
        if contact:
            contacts[str(row["actor_id"])] = contact
    return contacts


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
                   c.price_wusd_minor, c.author_actor_id,
                   count(l.lesson_id) filter (where l.status = 'published') as lessons_total,
                   count(p.lesson_id) as lessons_done
            from academy_courses c
            left join academy_lessons l
              on l.tenant_id = c.tenant_id and l.course_id = c.course_id
            left join academy_progress p
              on p.tenant_id = l.tenant_id and p.lesson_id = l.lesson_id
             and p.telegram_user_id = %s and l.status = 'published'
            where c.tenant_id = %s and c.status = 'published'
            group by c.course_id, c.slug, c.title, c.subtitle, c.access_rule, c.price_wusd_minor,
                     c.author_actor_id, c.sort_order
            order by c.sort_order, c.title
            """,
            (viewer.telegram_user_id, tenant_id),
        )
        access = await _access_rows(conn, tenant_id, viewer.telegram_user_id)
        locks = {
            course["course_id"]: course_lock_reason(course, viewer, has_access_row=course["course_id"] in access)
            for course in courses
        }
        contacts = await _author_contacts(
            conn,
            tenant_id,
            {
                str(course["author_actor_id"])
                for course in courses
                if course.get("author_actor_id") and locks[course["course_id"]] == "purchase_required"
            },
        )
    out = []
    for course in courses:
        lock = locks[course["course_id"]]
        item = {
            "slug": course["slug"],
            "title": course["title"],
            "subtitle": course.get("subtitle"),
            "access_rule": course["access_rule"],
            "lessons_total": int(course["lessons_total"] or 0),
            "lessons_done": int(course["lessons_done"] or 0),
            "locked": lock is not None,
            "lock_reason": lock,
        }
        if lock == "purchase_required":
            # Доступ выдаёт автор курса («полка»): ученик берёт у него ключ.
            item["author_contact"] = contacts.get(str(course.get("author_actor_id") or ""))
        out.append(item)
    return out


async def _load_course(conn: Any, tenant_id: str, slug: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select course_id::text as course_id, slug, title, subtitle, access_rule, price_wusd_minor, author_actor_id
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
    def __init__(self, status: int, code: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.extra = dict(extra or {})


async def _open_course(conn: Any, tenant_id: str, slug: str, viewer: AcademyViewer) -> dict[str, Any]:
    course = await _load_course(conn, tenant_id, slug)
    if not course or not academy_visible(viewer):
        raise AcademyError(404, "course_not_found")
    access = await _access_rows(conn, tenant_id, viewer.telegram_user_id)
    lock = course_lock_reason(course, viewer, has_access_row=course["course_id"] in access)
    if lock:
        extra = {}
        if lock == "purchase_required" and course.get("author_actor_id"):
            author = str(course["author_actor_id"])
            extra["author_contact"] = (await _author_contacts(conn, tenant_id, {author})).get(author)
        raise AcademyError(403, lock, extra)
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


async def course_slug_for_product(conn: Any, tenant_id: str, product_code: str) -> str | None:
    """The course a ``course_<код>`` tariff opens (``partner_subscription_plans.course_slug``)."""
    row = await fetch_one(
        conn,
        """
        select course_slug from partner_subscription_plans
        where tenant_id = %s and product_code = %s and course_slug is not null
        order by active desc, valid_from desc
        limit 1
        """,
        (tenant_id, product_code),
    )
    return str(row["course_slug"]) if row and row.get("course_slug") else None


async def grant_course_access(
    conn: Any, tenant_id: str, ref_code: str, course_slug: str, payment_ref: str
) -> int | None:
    """A paid course → an ``academy_access`` row for the profile owner's Telegram id.

    Runs inside the payment transaction. Never fails the payment: no owner
    Telegram id or no such course → a warning and None."""
    owner = await fetch_one(
        conn,
        """
        select la.telegram_user_id from referral_profiles rp
        join lead_actors la on la.tenant_id = rp.tenant_id and la.actor_id = rp.owner_id
        where rp.tenant_id = %s and rp.ref_code = %s
        limit 1
        """,
        (tenant_id, ref_code),
    )
    if not owner or owner.get("telegram_user_id") is None:
        logger.warning("academy_grant_no_telegram_user", extra={"tenant_id": tenant_id, "ref_code": ref_code})
        return None
    course = await fetch_one(
        conn,
        "select course_id::text as course_id from academy_courses where tenant_id = %s and slug = %s",
        (tenant_id, course_slug),
    )
    if not course:
        logger.warning("academy_grant_course_missing", extra={"tenant_id": tenant_id, "course_slug": course_slug})
        return None
    telegram_user_id = int(owner["telegram_user_id"])
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into academy_access (tenant_id, course_id, telegram_user_id, source, payment_ref)
            values (%s, %s::uuid, %s, 'purchase', %s)
            on conflict (tenant_id, course_id, telegram_user_id)
            do update set revoked_at = null, source = 'purchase', payment_ref = excluded.payment_ref, granted_at = now()
            """,
            (tenant_id, course["course_id"], telegram_user_id, payment_ref),
        )
    return telegram_user_id


async def grant_course_access_for_product(
    conn: Any, *, tenant_id: str, ref_code: str, product_code: str, payment_ref: str
) -> int | None:
    """Payment line ``course_<код>`` → course access. Inside a savepoint: whatever
    goes wrong here (no course yet, schema lag) is a warning, not a lost payment."""
    try:
        async with conn.transaction():
            course_slug = await course_slug_for_product(conn, tenant_id, product_code)
            if not course_slug:
                logger.warning(
                    "academy_grant_no_course_slug", extra={"tenant_id": tenant_id, "product_code": product_code}
                )
                return None
            return await grant_course_access(conn, tenant_id, ref_code, course_slug, payment_ref)
    except psycopg.Error:
        logger.warning(
            "academy_grant_failed", extra={"tenant_id": tenant_id, "product_code": product_code}, exc_info=True
        )
        return None


async def extend_shelf_in_connection(
    conn: Any, *, tenant_id: str, ref_code: str, access_months: int, current: datetime
) -> tuple[datetime, datetime, datetime | None]:
    """Paid shelf line → ``academy_shelf.paid_until`` of the profile owner.

    A shelf still paid is extended from its end, a lapsed one from now — like
    PRO. Returns (period_start, period_end, previous_paid_until) for the ledger."""
    owner = await fetch_one(
        conn,
        "select owner_id from referral_profiles where tenant_id = %s and ref_code = %s limit 1",
        (tenant_id, ref_code),
    )
    if not owner:
        raise ValueError("referral profile not found for the shelf payment")
    actor_id = str(owner["owner_id"])
    row = await fetch_one(
        conn,
        "select paid_until from academy_shelf where tenant_id = %s and actor_id = %s for update",
        (tenant_id, actor_id),
    )
    previous = row.get("paid_until") if row else None
    start = previous if previous is not None and previous > current else current
    end = add_calendar_months(start, int(access_months))
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into academy_shelf (tenant_id, actor_id, paid_until, status)
            values (%s, %s, %s, 'active')
            on conflict (tenant_id, actor_id)
            do update set paid_until = excluded.paid_until, updated_at = now()
            """,
            (tenant_id, actor_id, end),
        )
    return start, end, previous
