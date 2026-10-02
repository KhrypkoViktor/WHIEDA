"""Academy v2: the author's cabinet («Автор Академии») — courses, modules, lessons, students.

Who is an author: a person with a place in the Academy (an ``academy_shelf`` row of
any of their actor rows, paid or not) or with a course of their own; the owner and
preview admins act as the author of every course. Anyone else gets 403
``not_author`` — the site shows no author cabinet then.

Rules kept from the key model (25.09.2026): a course is created as a draft with
access by key (``purchase``); publishing needs the author's place in the Academy
paid and active (owner / preview admins publish anything); a ``pro`` course is the
owner's only. The student pays the author directly — the price is for display.

Text is Markdown, rendered and cleaned on the server (``app.academy.content``).
Media the author attaches must be their own uploads (or already in this lesson /
course). Lesson ``position`` is the course order (modules, then lessons) and is
renumbered after every structural change — the bot addresses lessons by it.
Deleting a lesson archives it: progress and homework stay.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timezone
from typing import Any

from app.academy.content import media_refs, render_markdown, sanitize_html
from app.academy.keys import shelf_active
from app.academy.media_service import _valid_ids, load_media, media_summary
from app.academy.rules import _parse_at, parse_unlock
from app.academy.service import AcademyError, AcademyViewer, price_out, viewer_actor_ids
from app.db import fetch_all, fetch_one, tenant_connection

AUTHOR_ACCESS_RULES = ("free", "purchase")
ADMIN_ACCESS_RULES = ("free", "purchase", "pro")
CURRENCIES = ("WUSD", "BYN", "RUB", "USD", "EUR", "KZT")
VIDEO_PROVIDERS = ("kinescope", "youtube", "rutube", "vk")
COURSE_STATUSES = ("draft", "published")
LESSON_STATUSES = ("draft", "published")
LESSON_KINDS = ("lesson", "live")
MAX_PRICE = 10_000_000
MAX_FILES = 20
MAX_PROMPT_CHARS = 20_000
END_POSITION = 1_000_000
_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*\Z")
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_\-=&.:]{1,200}\Z")
_TRANSLIT = {
    **dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", [
        "a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t",
        "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya",
    ])),
    "і": "i", "ў": "u", "ї": "yi", "є": "ye", "ґ": "g",
}


def slugify(text: str, *, fallback: str, max_len: int = 60) -> str:
    out = "".join(_TRANSLIT.get(ch, ch) for ch in str(text or "").lower())
    out = re.sub(r"[^a-z0-9]+", "-", out).strip("-")
    out = out[:max_len].strip("-")
    return out or fallback


def _iso(value: Any) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


@dataclass(frozen=True)
class Author:
    viewer: AcademyViewer
    actor_ids: list[str]
    is_admin: bool
    primary_actor_id: str | None

    def owns(self, media: dict[str, Any]) -> bool:
        return (
            self.is_admin
            or media.get("owner_telegram_user_id") == self.viewer.telegram_user_id
            or str(media.get("owner_actor_id") or "") in self.actor_ids
        )


async def _author(conn: Any, tenant_id: str, viewer: AcademyViewer) -> Author:
    actor_ids = await viewer_actor_ids(conn, tenant_id, viewer.telegram_user_id)
    primary = None
    if actor_ids:
        row = await fetch_one(
            conn,
            """
            select a.actor_id,
                   exists (select 1 from academy_shelf s where s.tenant_id = %(t)s and s.actor_id = a.actor_id) as shelf,
                   exists (select 1 from referral_profiles rp
                           where rp.tenant_id = %(t)s and rp.owner_id = a.actor_id and rp.enabled = true) as profile,
                   exists (select 1 from academy_courses c
                           where c.tenant_id = %(t)s and c.author_actor_id = any(%(ids)s::text[])) as courses
            from unnest(%(ids)s::text[]) as a(actor_id)
            order by shelf desc, profile desc, a.actor_id
            limit 1
            """,
            {"t": tenant_id, "ids": actor_ids},
        )
        primary = str(row["actor_id"]) if row else actor_ids[0]
        is_author = bool(row and (row["shelf"] or row["courses"]))
        if not is_author:
            shelf_any = await fetch_one(
                conn,
                "select 1 as ok from academy_shelf where tenant_id = %s and actor_id = any(%s::text[]) limit 1",
                (tenant_id, actor_ids),
            )
            is_author = bool(shelf_any)
    else:
        is_author = False
    if not (viewer.is_preview_admin or is_author):
        raise AcademyError(403, "not_author")
    return Author(viewer=viewer, actor_ids=actor_ids, is_admin=viewer.is_preview_admin, primary_actor_id=primary)


_COURSE_COLUMNS = """
    course_id::text as course_id, slug, title, subtitle, kind, status, access_rule, author_actor_id,
    description_md, description_html, cover_media_id::text as cover_media_id, price_wusd_minor, price_currency,
    created_at, updated_at
"""


async def _course_for(conn: Any, tenant_id: str, author: Author, slug: str, *, lock: bool = False) -> dict[str, Any]:
    course = await fetch_one(
        conn,
        f"""
        select {_COURSE_COLUMNS} from academy_courses
        where tenant_id = %s and slug = %s and status <> 'archived'
        {"for update" if lock else ""}
        """,
        (tenant_id, str(slug or "").strip().lower()),
    )
    if not course:
        raise AcademyError(404, "course_not_found")
    if not author.is_admin and str(course.get("author_actor_id") or "") not in author.actor_ids:
        raise AcademyError(403, "not_author")
    return course


def _course_out(course: dict[str, Any], media: dict[str, dict[str, Any]], telegram_user_id: int) -> dict[str, Any]:
    cover = media.get(str(course.get("cover_media_id") or ""))
    return {
        "slug": course["slug"],
        "title": course["title"],
        "subtitle": course.get("subtitle"),
        "kind": course.get("kind") or "course",
        "status": course["status"],
        "access_rule": course["access_rule"],
        "description_md": course.get("description_md") or "",
        "description_html": sanitize_html(course.get("description_html")),
        "cover_media_id": course.get("cover_media_id"),
        "cover_url": media_summary(cover, telegram_user_id)["url"] if cover else None,
        "price": price_out(course.get("price_wusd_minor"), course.get("price_currency")),
        "created_at": _iso(course.get("created_at")),
        "updated_at": _iso(course.get("updated_at")),
    }


def _text(value: Any, *, code: str, max_len: int, required: bool = True) -> str | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise AcademyError(400, code)
        return None
    if len(text) > max_len or any(ord(ch) < 32 and ch not in "\n\t" for ch in text):
        raise AcademyError(400, code)
    return text


def _markdown(value: Any) -> str:
    try:
        return render_markdown(str(value or ""))
    except ValueError as exc:
        raise AcademyError(400, "text_too_long") from exc


async def _check_media(
    conn: Any,
    tenant_id: str,
    author: Author,
    ids: list[str],
    kinds: tuple[str, ...],
    *,
    known: set[str] | None = None,
    allow_processing: bool = False,
) -> dict[str, dict[str, Any]]:
    """Own uploads of the right kind; media already in this lesson/course stay allowed."""
    media = await load_media(conn, tenant_id, ids)
    statuses = ("ready", "processing") if allow_processing else ("ready",)
    for media_id in ids:
        row = media.get(media_id)
        if (
            not row
            or row["kind"] not in kinds
            or row["status"] not in statuses
            or not (author.owns(row) or media_id in (known or set()))
        ):
            raise AcademyError(400, "bad_media", {"media_id": media_id})
    return media


# ---- courses ---------------------------------------------------------------------------------


async def author_courses(tenant_id: str, viewer: AcademyViewer) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        author = await _author(conn, tenant_id, viewer)
        courses = await fetch_all(
            conn,
            f"""
            select {_COURSE_COLUMNS},
                   (select count(*) from academy_lessons l
                    where l.tenant_id = c.tenant_id and l.course_id = c.course_id and l.status = 'published') as lessons_total,
                   (select count(*) from academy_access a
                    where a.tenant_id = c.tenant_id and a.course_id = c.course_id and a.revoked_at is null) as students,
                   (select count(*) from academy_submissions s
                    join academy_lessons l on l.tenant_id = s.tenant_id and l.lesson_id = s.lesson_id
                    where s.tenant_id = c.tenant_id and l.course_id = c.course_id and s.status = 'submitted') as submissions_pending
            from academy_courses c
            where c.tenant_id = %s and c.status <> 'archived'
              and (%s or c.author_actor_id = any(%s::text[]))
            order by c.sort_order, c.created_at
            """,
            (tenant_id, author.is_admin, author.actor_ids),
        )
        covers = await load_media(conn, tenant_id, [course["cover_media_id"] for course in courses])
        paid = await fetch_one(
            conn,
            """
            select max(paid_until) as paid_until from academy_shelf
            where tenant_id = %s and actor_id = any(%s::text[]) and status = 'active'
            """,
            (tenant_id, author.actor_ids),
        )
        active = bool(author.primary_actor_id) and await shelf_active(conn, tenant_id, str(author.primary_actor_id))
    return {
        "courses": [
            {
                **_course_out(course, covers, viewer.telegram_user_id),
                "lessons_total": int(course["lessons_total"]),
                "students": int(course["students"]),
                "submissions_pending": int(course["submissions_pending"]),
            }
            for course in courses
        ],
        "shelf": {"active": bool(active), "paid_until": _iso((paid or {}).get("paid_until"))},
        "is_admin": author.is_admin,
    }


async def _free_slug(conn: Any, tenant_id: str, base: str, *, course_id: str | None = None) -> str:
    table, scope = ("academy_lessons", "and course_id = %s::uuid") if course_id else ("academy_courses", "")
    params: tuple = (tenant_id, f"{base}%", course_id) if course_id else (tenant_id, f"{base}%")
    taken = {
        row["slug"]
        for row in await fetch_all(conn, f"select slug from {table} where tenant_id = %s and slug like %s {scope}", params)
    }
    if base not in taken:
        return base
    number = 2
    while f"{base}-{number}" in taken:
        number += 1
    return f"{base}-{number}"


async def create_course(
    tenant_id: str, viewer: AcademyViewer, *, title: Any, slug: Any = None, subtitle: Any = None
) -> dict[str, Any]:
    clean_title = _text(title, code="bad_title", max_len=200)
    clean_subtitle = _text(subtitle, code="bad_subtitle", max_len=300, required=False)
    async with tenant_connection(tenant_id) as conn:
        author = await _author(conn, tenant_id, viewer)
        # Два одновременных «создать» с одним адресом: второй ждёт и получает 409/«-2», а не 500.
        await fetch_one(
            conn, "select pg_advisory_xact_lock(hashtext(%s)) as locked", (f"academy_course_slug:{tenant_id}",)
        )
        if slug:
            wanted = str(slug).strip().lower()
            if not _SLUG_RE.match(wanted) or len(wanted) > 60:
                raise AcademyError(400, "bad_slug")
            exists = await fetch_one(
                conn, "select 1 as ok from academy_courses where tenant_id = %s and slug = %s", (tenant_id, wanted)
            )
            if exists:
                raise AcademyError(409, "slug_taken")
        else:
            wanted = await _free_slug(conn, tenant_id, slugify(clean_title, fallback="kurs"))
        course = await fetch_one(
            conn,
            f"""
            insert into academy_courses (tenant_id, slug, title, subtitle, access_rule, author_actor_id, status)
            values (%s, %s, %s, %s, 'purchase', %s, 'draft')
            returning {_COURSE_COLUMNS}
            """,
            (tenant_id, wanted, clean_title, clean_subtitle, author.primary_actor_id),
        )
    return {"ok": True, "course": _course_out(course, {}, viewer.telegram_user_id)}


async def update_course(tenant_id: str, viewer: AcademyViewer, slug: str, patch: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if "title" in patch:
        changes["title"] = _text(patch["title"], code="bad_title", max_len=200)
    if "subtitle" in patch:
        changes["subtitle"] = _text(patch["subtitle"], code="bad_subtitle", max_len=300, required=False)
    if "price" in patch:
        price = patch["price"]
        if price is None:
            changes["price_wusd_minor"] = None
        else:
            try:
                amount = float(price)
            except (TypeError, ValueError) as exc:
                raise AcademyError(400, "bad_price") from exc
            if not 0 <= amount <= MAX_PRICE:
                raise AcademyError(400, "bad_price")
            changes["price_wusd_minor"] = int(round(amount * 100))
    if "currency" in patch:
        currency = str(patch["currency"] or "").strip().upper()
        if currency not in CURRENCIES:
            raise AcademyError(400, "bad_currency", {"allowed": list(CURRENCIES)})
        changes["price_currency"] = currency
    if "status" in patch and patch["status"] not in COURSE_STATUSES:
        raise AcademyError(400, "bad_status")
    async with tenant_connection(tenant_id) as conn:
        author = await _author(conn, tenant_id, viewer)
        course = await _course_for(conn, tenant_id, author, slug, lock=True)
        known = set(media_refs(course.get("description_html") or "")) | {str(course.get("cover_media_id") or "")}
        if "description_md" in patch:
            source = str(patch["description_md"] or "")
            html = _markdown(source)
            await _check_media(conn, tenant_id, author, media_refs(html), ("image", "file"), known=known)
            changes["description_md"] = source
            changes["description_html"] = html
        if "cover_media_id" in patch:
            cover = patch["cover_media_id"]
            if cover in (None, ""):
                changes["cover_media_id"] = None
            else:
                ids = _valid_ids([cover])
                if not ids:
                    raise AcademyError(400, "bad_media")
                await _check_media(conn, tenant_id, author, ids, ("image",), known=known)
                changes["cover_media_id"] = ids[0]
        if "access_rule" in patch:
            allowed = ADMIN_ACCESS_RULES if author.is_admin else AUTHOR_ACCESS_RULES
            if patch["access_rule"] not in allowed:
                raise AcademyError(400, "bad_access_rule", {"allowed": list(allowed)})
            changes["access_rule"] = patch["access_rule"]
        if "status" in patch:
            if patch["status"] == "published" and course["status"] != "published" and not author.is_admin:
                owner_actor = str(course.get("author_actor_id") or "")
                if not owner_actor or not await shelf_active(conn, tenant_id, owner_actor):
                    raise AcademyError(403, "shelf_inactive")
            changes["status"] = patch["status"]
        if changes:
            columns = ", ".join(
                f"{name} = %s::uuid" if name == "cover_media_id" else f"{name} = %s" for name in changes
            )
            course = await fetch_one(
                conn,
                f"""
                update academy_courses set {columns}, updated_at = now()
                where tenant_id = %s and course_id = %s::uuid
                returning {_COURSE_COLUMNS}
                """,
                (*changes.values(), tenant_id, course["course_id"]),
            )
        media = await load_media(conn, tenant_id, [course.get("cover_media_id")])
    return {"ok": True, "course": _course_out(course, media, viewer.telegram_user_id)}


# ---- structure -----------------------------------------------------------------------------------


async def renumber_lessons(conn: Any, tenant_id: str, course_id: str) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            with ordered as (
              select l.lesson_id,
                     row_number() over (order by m.position nulls first, l.position, l.created_at) as rn
              from academy_lessons l
              left join academy_modules m on m.tenant_id = l.tenant_id and m.module_id = l.module_id
              where l.tenant_id = %s and l.course_id = %s::uuid and l.status <> 'archived'
            )
            update academy_lessons l set position = o.rn, updated_at = now()
            from ordered o
            where l.tenant_id = %s and l.lesson_id = o.lesson_id and l.position <> o.rn
            """,
            (tenant_id, course_id, tenant_id),
        )


def _video_out(video: dict[str, Any] | None, media: dict[str, dict[str, Any]], telegram_user_id: int) -> dict | None:
    if not video:
        return None
    media_id = str(video.get("media_id") or "")
    if not media_id:
        return {"provider": video.get("provider"), "id": video.get("id")}
    row = media.get(media_id)
    out: dict[str, Any] = {"media_id": media_id, "status": row["status"] if row else "missing"}
    if row and row["status"] == "ready":
        summary = media_summary(row, telegram_user_id)
        out.update({"url": summary["url"], "poster_url": summary.get("poster_url"),
                    "duration_sec": summary.get("duration_sec")})
    if row and row["status"] == "failed":
        out["error"] = row.get("error")
    return out


_LESSON_COLUMNS = """
    l.lesson_id::text as lesson_id, l.slug, l.module_id::text as module_id, l.position, l.title, l.short_title,
    l.kind, l.status, l.body_md, l.body_html, l.video, l.files, l.live_at, l.live_url, l.unlock,
    a.prompt_md, a.prompt_html, a.required
"""


async def _lesson_row(conn: Any, tenant_id: str, course_id: str, lesson_id: str) -> dict[str, Any]:
    ids = _valid_ids([lesson_id])
    row = None
    if ids:
        row = await fetch_one(
            conn,
            f"""
            select {_LESSON_COLUMNS}
            from academy_lessons l
            left join academy_assignments a on a.tenant_id = l.tenant_id and a.lesson_id = l.lesson_id
            where l.tenant_id = %s and l.course_id = %s::uuid and l.lesson_id = %s::uuid and l.status <> 'archived'
            """,
            (tenant_id, course_id, ids[0]),
        )
    if not row:
        raise AcademyError(404, "lesson_not_found")
    return row


def _lesson_out(row: dict[str, Any], media: dict[str, dict[str, Any]], telegram_user_id: int) -> dict[str, Any]:
    return {
        "lesson_id": row["lesson_id"],
        "slug": row["slug"],
        "module_id": row.get("module_id"),
        "position": int(row["position"]),
        "title": row["title"],
        "short_title": row.get("short_title"),
        "kind": row.get("kind") or "lesson",
        "status": row["status"],
        "body_md": row.get("body_md") or "",
        "body_html": sanitize_html(row.get("body_html")),
        "video": _video_out(row.get("video"), media, telegram_user_id),
        "files": [
            media_summary(media[str(item)], telegram_user_id)
            for item in (row.get("files") or [])
            if str(item) in media
        ],
        "live_at": _iso(row.get("live_at")),
        "live_url": row.get("live_url"),
        "unlock": row.get("unlock"),
        "assignment": (
            {"prompt_md": row.get("prompt_md") or "", "prompt_html": sanitize_html(row.get("prompt_html")),
             "required": bool(row.get("required"))}
            if row.get("required") is not None
            else None
        ),
    }


async def _lesson_media(conn: Any, tenant_id: str, row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return await load_media(
        conn,
        tenant_id,
        [*(row.get("files") or []), (row.get("video") or {}).get("media_id"), *media_refs(row.get("body_html") or "")],
    )


def _module_out(row: dict[str, Any], lessons: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out = {
        "module_id": row["module_id"],
        "title": row["title"],
        "position": int(row["position"]),
        "unlock": row.get("unlock") or {"type": "open"},
    }
    if lessons is not None:
        out["lessons"] = lessons
    return out


async def author_course(tenant_id: str, viewer: AcademyViewer, slug: str) -> dict[str, Any]:
    """Everything the editor needs: the course, modules with their lessons (short)."""
    async with tenant_connection(tenant_id) as conn:
        author = await _author(conn, tenant_id, viewer)
        course = await _course_for(conn, tenant_id, author, slug)
        modules = await fetch_all(
            conn,
            """
            select module_id::text as module_id, title, position, unlock from academy_modules
            where tenant_id = %s and course_id = %s::uuid order by position, created_at
            """,
            (tenant_id, course["course_id"]),
        )
        lessons = await fetch_all(
            conn,
            """
            select l.lesson_id::text as lesson_id, l.slug, l.module_id::text as module_id, l.module_title, l.position,
                   l.title, l.kind, l.status, l.live_at, l.unlock, l.video,
                   (a.lesson_id is not null) as has_assignment,
                   (select count(*) from academy_submissions s
                    where s.tenant_id = l.tenant_id and s.lesson_id = l.lesson_id and s.status = 'submitted') as pending
            from academy_lessons l
            left join academy_assignments a on a.tenant_id = l.tenant_id and a.lesson_id = l.lesson_id
            where l.tenant_id = %s and l.course_id = %s::uuid and l.status <> 'archived'
            order by l.position, l.created_at
            """,
            (tenant_id, course["course_id"]),
        )
        media = await load_media(
            conn, tenant_id, [course.get("cover_media_id"), *[(row.get("video") or {}).get("media_id") for row in lessons]]
        )
        active = bool(course.get("author_actor_id")) and await shelf_active(conn, tenant_id, str(course["author_actor_id"]))

    def short(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "lesson_id": row["lesson_id"],
            "slug": row["slug"],
            "position": int(row["position"]),
            "title": row["title"],
            "kind": row.get("kind") or "lesson",
            "status": row["status"],
            "live_at": _iso(row.get("live_at")),
            "unlock": row.get("unlock"),
            "has_assignment": bool(row["has_assignment"]),
            "submissions_pending": int(row["pending"]),
            "video": _video_out(row.get("video"), media, viewer.telegram_user_id),
        }

    by_module: dict[str, list[dict[str, Any]]] = {}
    loose = []
    for row in lessons:
        if row.get("module_id"):
            by_module.setdefault(row["module_id"], []).append(short(row))
        else:
            loose.append({**short(row), "module_title": row.get("module_title") or ""})
    return {
        "ok": True,
        "course": _course_out(course, media, viewer.telegram_user_id),
        "modules": [_module_out(module, by_module.get(module["module_id"], [])) for module in modules],
        # Уроки старого импорта без модуля (module_title): перенесите их в модуль правкой урока.
        "unassigned_lessons": loose,
        "can_publish": author.is_admin or bool(active),
    }


def _unlock(value: Any, *, allow_none: bool) -> dict[str, Any] | None:
    try:
        return parse_unlock(value, allow_none=allow_none)
    except ValueError as exc:
        raise AcademyError(400, "bad_unlock", {"detail": str(exc)}) from exc


async def _module_row(conn: Any, tenant_id: str, course_id: str, module_id: str) -> dict[str, Any]:
    ids = _valid_ids([module_id])
    row = None
    if ids:
        row = await fetch_one(
            conn,
            """
            select module_id::text as module_id, title, position, unlock from academy_modules
            where tenant_id = %s and course_id = %s::uuid and module_id = %s::uuid
            """,
            (tenant_id, course_id, ids[0]),
        )
    if not row:
        raise AcademyError(404, "module_not_found")
    return row


async def create_module(tenant_id: str, viewer: AcademyViewer, slug: str, data: dict[str, Any]) -> dict[str, Any]:
    title = _text(data.get("title"), code="bad_title", max_len=200)
    unlock = _unlock(data.get("unlock"), allow_none=False)
    async with tenant_connection(tenant_id) as conn:
        author = await _author(conn, tenant_id, viewer)
        course = await _course_for(conn, tenant_id, author, slug, lock=True)
        row = await fetch_one(
            conn,
            """
            insert into academy_modules (tenant_id, course_id, position, title, unlock)
            values (%s, %s::uuid,
                    (select coalesce(max(position), 0) + 1 from academy_modules where tenant_id = %s and course_id = %s::uuid),
                    %s, %s::jsonb)
            returning module_id::text as module_id, title, position, unlock
            """,
            (tenant_id, course["course_id"], tenant_id, course["course_id"], title, json.dumps(unlock)),
        )
    return {"ok": True, "module": _module_out(row)}


async def update_module(
    tenant_id: str, viewer: AcademyViewer, slug: str, module_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    if "title" in data:
        changes["title"] = _text(data["title"], code="bad_title", max_len=200)
    if "unlock" in data:
        changes["unlock"] = json.dumps(_unlock(data["unlock"], allow_none=False))
    async with tenant_connection(tenant_id) as conn:
        author = await _author(conn, tenant_id, viewer)
        course = await _course_for(conn, tenant_id, author, slug)
        row = await _module_row(conn, tenant_id, course["course_id"], module_id)
        if changes:
            columns = ", ".join(f"{name} = %s::jsonb" if name == "unlock" else f"{name} = %s" for name in changes)
            row = await fetch_one(
                conn,
                f"""
                update academy_modules set {columns}, updated_at = now()
                where tenant_id = %s and module_id = %s::uuid
                returning module_id::text as module_id, title, position, unlock
                """,
                (*changes.values(), tenant_id, row["module_id"]),
            )
    return {"ok": True, "module": _module_out(row)}


async def delete_module(tenant_id: str, viewer: AcademyViewer, slug: str, module_id: str) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        author = await _author(conn, tenant_id, viewer)
        course = await _course_for(conn, tenant_id, author, slug, lock=True)
        row = await _module_row(conn, tenant_id, course["course_id"], module_id)
        busy = await fetch_one(
            conn,
            """
            select count(*) as n from academy_lessons
            where tenant_id = %s and module_id = %s::uuid and status <> 'archived'
            """,
            (tenant_id, row["module_id"]),
        )
        if busy and int(busy["n"]):
            raise AcademyError(409, "module_not_empty", {"lessons": int(busy["n"])})
        async with conn.cursor() as cur:
            await cur.execute(
                "update academy_lessons set module_id = null where tenant_id = %s and module_id = %s::uuid",
                (tenant_id, row["module_id"]),
            )
            await cur.execute(
                "delete from academy_modules where tenant_id = %s and module_id = %s::uuid", (tenant_id, row["module_id"])
            )
            await cur.execute(
                """
                with ordered as (
                  select module_id, row_number() over (order by position, created_at) as rn
                  from academy_modules where tenant_id = %s and course_id = %s::uuid
                )
                update academy_modules m set position = o.rn from ordered o
                where m.module_id = o.module_id and m.position <> o.rn
                """,
                (tenant_id, course["course_id"]),
            )
    return {"ok": True}


async def reorder_modules(tenant_id: str, viewer: AcademyViewer, slug: str, order: list[Any]) -> dict[str, Any]:
    wanted = _valid_ids(list(order or []))
    async with tenant_connection(tenant_id) as conn:
        author = await _author(conn, tenant_id, viewer)
        course = await _course_for(conn, tenant_id, author, slug, lock=True)
        current = [
            row["module_id"]
            for row in await fetch_all(
                conn,
                "select module_id::text as module_id from academy_modules where tenant_id = %s and course_id = %s::uuid",
                (tenant_id, course["course_id"]),
            )
        ]
        if len(wanted) != len(list(order or [])) or sorted(wanted) != sorted(current):
            raise AcademyError(400, "bad_order")
        async with conn.cursor() as cur:
            for position, module in enumerate(wanted, start=1):
                await cur.execute(
                    "update academy_modules set position = %s, updated_at = now() where tenant_id = %s and module_id = %s::uuid",
                    (position, tenant_id, module),
                )
        await renumber_lessons(conn, tenant_id, course["course_id"])
    return {"ok": True}


async def reorder_lessons(
    tenant_id: str, viewer: AcademyViewer, slug: str, module_id: str, order: list[Any]
) -> dict[str, Any]:
    wanted = _valid_ids(list(order or []))
    async with tenant_connection(tenant_id) as conn:
        author = await _author(conn, tenant_id, viewer)
        course = await _course_for(conn, tenant_id, author, slug, lock=True)
        module = await _module_row(conn, tenant_id, course["course_id"], module_id)
        current = [
            row["lesson_id"]
            for row in await fetch_all(
                conn,
                """
                select lesson_id::text as lesson_id from academy_lessons
                where tenant_id = %s and module_id = %s::uuid and status <> 'archived'
                """,
                (tenant_id, module["module_id"]),
            )
        ]
        if len(wanted) != len(list(order or [])) or sorted(wanted) != sorted(current):
            raise AcademyError(400, "bad_order")
        async with conn.cursor() as cur:
            for position, lesson in enumerate(wanted, start=1):
                await cur.execute(
                    "update academy_lessons set position = %s where tenant_id = %s and lesson_id = %s::uuid",
                    (position, tenant_id, lesson),
                )
        await renumber_lessons(conn, tenant_id, course["course_id"])
    return {"ok": True}


# ---- lessons -------------------------------------------------------------------------------------


async def _lesson_changes(
    conn: Any, tenant_id: str, author: Author, data: dict[str, Any], *, current: dict[str, Any] | None
) -> tuple[dict[str, Any], Any]:
    """Validated column values; the second item is the assignment (``...`` — untouched)."""
    known: set[str] = set()
    if current:
        known = {
            *[str(item) for item in (current.get("files") or [])],
            str((current.get("video") or {}).get("media_id") or ""),
            *media_refs(current.get("body_html") or ""),
        }
    changes: dict[str, Any] = {}
    if "title" in data or current is None:
        changes["title"] = _text(data.get("title"), code="bad_title", max_len=200)
    if "short_title" in data:
        changes["short_title"] = _text(data["short_title"], code="bad_title", max_len=120, required=False)
    if "body_md" in data:
        source = str(data["body_md"] or "")
        html = _markdown(source)
        await _check_media(conn, tenant_id, author, media_refs(html), ("image", "file", "video"), known=known)
        changes["body_md"] = source
        changes["body_html"] = html
    if "video" in data:
        video = data["video"]
        if video in (None, {}):
            changes["video"] = None
        elif isinstance(video, dict) and video.get("media_id"):
            ids = _valid_ids([video["media_id"]])
            if not ids:
                raise AcademyError(400, "bad_media")
            await _check_media(conn, tenant_id, author, ids, ("video",), known=known, allow_processing=True)
            changes["video"] = json.dumps({"media_id": ids[0]})
        elif (
            isinstance(video, dict)
            and video.get("provider") in VIDEO_PROVIDERS
            and _VIDEO_ID_RE.match(str(video.get("id") or ""))
        ):
            changes["video"] = json.dumps({"provider": video["provider"], "id": str(video["id"])})
        else:
            raise AcademyError(400, "bad_video", {"providers": list(VIDEO_PROVIDERS)})
    if "files" in data:
        raw = list(data["files"] or [])
        ids = _valid_ids(raw)
        if len(ids) != len(raw) or len(ids) > MAX_FILES:
            raise AcademyError(400, "bad_media")
        await _check_media(conn, tenant_id, author, ids, ("file", "image"), known=known)
        changes["files"] = json.dumps(ids)
    if "kind" in data:
        if data["kind"] not in LESSON_KINDS:
            raise AcademyError(400, "bad_kind")
        changes["kind"] = data["kind"]
    if "live_at" in data:
        if data["live_at"] in (None, ""):
            changes["live_at"] = None
        else:
            try:
                changes["live_at"] = _parse_at(data["live_at"])
            except ValueError as exc:
                raise AcademyError(400, "bad_live_at") from exc
    if "live_url" in data:
        url = str(data["live_url"] or "").strip()
        if url and (not url.startswith("https://") or len(url) > 500 or any(ch.isspace() for ch in url)):
            raise AcademyError(400, "bad_live_url")
        changes["live_url"] = url or None
    if "unlock" in data:
        rule = _unlock(data["unlock"], allow_none=True)
        changes["unlock"] = json.dumps(rule) if rule is not None else None
    if "status" in data:
        if data["status"] not in LESSON_STATUSES:
            raise AcademyError(400, "bad_status")
        changes["status"] = data["status"]
    assignment: Any = ...
    if "assignment" in data:
        spec = data["assignment"]
        if spec is None:
            assignment = None
        elif isinstance(spec, dict):
            prompt = str(spec.get("prompt_md") or "")
            if len(prompt) > MAX_PROMPT_CHARS:
                raise AcademyError(400, "text_too_long")
            assignment = {"prompt_md": prompt, "prompt_html": _markdown(prompt), "required": bool(spec.get("required", True))}
        else:
            raise AcademyError(400, "bad_assignment")
    return changes, assignment


_JSONB_COLUMNS = ("video", "files", "unlock")


async def _save_assignment(conn: Any, tenant_id: str, lesson_id: str, assignment: Any) -> None:
    if assignment is ...:
        return
    async with conn.cursor() as cur:
        if assignment is None:
            await cur.execute(
                "delete from academy_assignments where tenant_id = %s and lesson_id = %s::uuid", (tenant_id, lesson_id)
            )
            return
        await cur.execute(
            """
            insert into academy_assignments (tenant_id, lesson_id, prompt_md, prompt_html, required)
            values (%s, %s::uuid, %s, %s, %s)
            on conflict (tenant_id, lesson_id) do update set
              prompt_md = excluded.prompt_md, prompt_html = excluded.prompt_html,
              required = excluded.required, updated_at = now()
            """,
            (tenant_id, lesson_id, assignment["prompt_md"], assignment["prompt_html"], assignment["required"]),
        )


async def create_lesson(
    tenant_id: str, viewer: AcademyViewer, slug: str, module_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        author = await _author(conn, tenant_id, viewer)
        course = await _course_for(conn, tenant_id, author, slug, lock=True)
        module = await _module_row(conn, tenant_id, course["course_id"], module_id)
        changes, assignment = await _lesson_changes(conn, tenant_id, author, data, current=None)
        lesson_slug = await _free_slug(
            conn, tenant_id, slugify(changes["title"], fallback="urok"), course_id=course["course_id"]
        )
        values = {
            "body_html": "",
            "status": "published",
            **changes,
            "slug": lesson_slug,
            "module_id": module["module_id"],
            "module_title": module["title"],
            "position": END_POSITION,
        }
        names = ", ".join(values)
        placeholders = ", ".join(
            "%s::jsonb" if name in _JSONB_COLUMNS else "%s::uuid" if name == "module_id" else "%s" for name in values
        )
        row = await fetch_one(
            conn,
            f"""
            insert into academy_lessons (tenant_id, course_id, {names})
            values (%s, %s::uuid, {placeholders})
            returning lesson_id::text as lesson_id
            """,
            (tenant_id, course["course_id"], *values.values()),
        )
        await _save_assignment(conn, tenant_id, row["lesson_id"], assignment)
        await renumber_lessons(conn, tenant_id, course["course_id"])
        saved = await _lesson_row(conn, tenant_id, course["course_id"], row["lesson_id"])
        media = await _lesson_media(conn, tenant_id, saved)
    return {"ok": True, "lesson": _lesson_out(saved, media, viewer.telegram_user_id)}


async def get_lesson(tenant_id: str, viewer: AcademyViewer, slug: str, lesson_id: str) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        author = await _author(conn, tenant_id, viewer)
        course = await _course_for(conn, tenant_id, author, slug)
        row = await _lesson_row(conn, tenant_id, course["course_id"], lesson_id)
        media = await _lesson_media(conn, tenant_id, row)
    return {"ok": True, "lesson": _lesson_out(row, media, viewer.telegram_user_id)}


async def update_lesson(
    tenant_id: str, viewer: AcademyViewer, slug: str, lesson_id: str, data: dict[str, Any]
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        author = await _author(conn, tenant_id, viewer)
        course = await _course_for(conn, tenant_id, author, slug, lock=True)
        current = await _lesson_row(conn, tenant_id, course["course_id"], lesson_id)
        changes, assignment = await _lesson_changes(conn, tenant_id, author, data, current=current)
        moved = False
        if data.get("module_id") and data["module_id"] != current.get("module_id"):
            target = await _module_row(conn, tenant_id, course["course_id"], str(data["module_id"]))
            changes.update({"module_id": target["module_id"], "module_title": target["title"], "position": END_POSITION})
            moved = True
        if changes:
            columns = ", ".join(
                f"{name} = %s::jsonb" if name in _JSONB_COLUMNS else f"{name} = %s::uuid" if name == "module_id"
                else f"{name} = %s"
                for name in changes
            )
            async with conn.cursor() as cur:
                await cur.execute(
                    f"update academy_lessons set {columns}, updated_at = now() where tenant_id = %s and lesson_id = %s::uuid",
                    (*changes.values(), tenant_id, current["lesson_id"]),
                )
        await _save_assignment(conn, tenant_id, current["lesson_id"], assignment)
        if moved:
            await renumber_lessons(conn, tenant_id, course["course_id"])
        saved = await _lesson_row(conn, tenant_id, course["course_id"], current["lesson_id"])
        media = await _lesson_media(conn, tenant_id, saved)
    return {"ok": True, "lesson": _lesson_out(saved, media, viewer.telegram_user_id)}


async def delete_lesson(tenant_id: str, viewer: AcademyViewer, slug: str, lesson_id: str) -> dict[str, Any]:
    """Archive: hidden everywhere, progress and homework kept."""
    async with tenant_connection(tenant_id) as conn:
        author = await _author(conn, tenant_id, viewer)
        course = await _course_for(conn, tenant_id, author, slug, lock=True)
        row = await _lesson_row(conn, tenant_id, course["course_id"], lesson_id)
        async with conn.cursor() as cur:
            await cur.execute(
                "update academy_lessons set status = 'archived', updated_at = now() where tenant_id = %s and lesson_id = %s::uuid",
                (tenant_id, row["lesson_id"]),
            )
        await renumber_lessons(conn, tenant_id, course["course_id"])
    return {"ok": True}


# ---- students and preview ---------------------------------------------------------------------------


async def course_students(tenant_id: str, viewer: AcademyViewer, slug: str) -> list[dict[str, Any]]:
    """Students with progress %, last activity and homework waiting for review."""
    async with tenant_connection(tenant_id) as conn:
        author = await _author(conn, tenant_id, viewer)
        course = await _course_for(conn, tenant_id, author, slug)
        rows = await fetch_all(
            conn,
            """
            with lessons as (
              select l.lesson_id, coalesce(a.required, false) as required
              from academy_lessons l
              left join academy_assignments a on a.tenant_id = l.tenant_id and a.lesson_id = l.lesson_id
              where l.tenant_id = %(t)s and l.course_id = %(c)s::uuid and l.status = 'published'
            ),
            people as (
              select telegram_user_id from academy_access
              where tenant_id = %(t)s and course_id = %(c)s::uuid and revoked_at is null
              union
              select p.telegram_user_id from academy_progress p join lessons on lessons.lesson_id = p.lesson_id
              where p.tenant_id = %(t)s
              union
              select s.telegram_user_id from academy_submissions s join lessons on lessons.lesson_id = s.lesson_id
              where s.tenant_id = %(t)s
            )
            select people.telegram_user_id,
                   acc.source, acc.started_at,
                   (select count(*) from lessons) as lessons_total,
                   (select count(*) from lessons
                    join academy_progress p
                      on p.tenant_id = %(t)s and p.lesson_id = lessons.lesson_id
                     and p.telegram_user_id = people.telegram_user_id
                    where not lessons.required or exists (
                      select 1 from academy_submissions s
                      where s.tenant_id = %(t)s and s.lesson_id = lessons.lesson_id
                        and s.telegram_user_id = people.telegram_user_id and s.status = 'accepted')
                   ) as lessons_complete,
                   greatest(
                     (select max(p.done_at) from academy_progress p join lessons on lessons.lesson_id = p.lesson_id
                      where p.tenant_id = %(t)s and p.telegram_user_id = people.telegram_user_id),
                     (select max(s.updated_at) from academy_submissions s join lessons on lessons.lesson_id = s.lesson_id
                      where s.tenant_id = %(t)s and s.telegram_user_id = people.telegram_user_id)
                   ) as last_activity_at,
                   (select count(*) from academy_submissions s join lessons on lessons.lesson_id = s.lesson_id
                    where s.tenant_id = %(t)s and s.telegram_user_id = people.telegram_user_id
                      and s.status = 'submitted') as submissions_pending,
                   la.display_name, la.telegram_username
            from people
            left join academy_access acc
              on acc.tenant_id = %(t)s and acc.course_id = %(c)s::uuid and acc.telegram_user_id = people.telegram_user_id
            left join lateral (
              select display_name, telegram_username from lead_actors
              where tenant_id = %(t)s and telegram_user_id = people.telegram_user_id
              order by active desc limit 1
            ) la on true
            order by last_activity_at desc nulls last, people.telegram_user_id
            """,
            {"t": tenant_id, "c": course["course_id"]},
        )
    out = []
    for row in rows:
        username = str(row.get("telegram_username") or "").strip().lstrip("@") or None
        total = int(row["lessons_total"] or 0)
        complete = int(row["lessons_complete"] or 0)
        out.append(
            {
                "name": str(row.get("display_name") or "").strip() or (f"@{username}" if username else "Ученик"),
                "username": username,
                "access": row.get("source"),
                "started_at": _iso(row.get("started_at")),
                "lessons_total": total,
                "lessons_complete": complete,
                "progress_pct": round(100 * complete / total) if total else 0,
                "last_activity_at": _iso(row.get("last_activity_at")),
                "submissions_pending": int(row["submissions_pending"] or 0),
            }
        )
    return out


async def preview_markdown(tenant_id: str, viewer: AcademyViewer, source: Any) -> str:
    async with tenant_connection(tenant_id) as conn:
        await _author(conn, tenant_id, viewer)
    return _markdown(source)


async def require_author(tenant_id: str, viewer: AcademyViewer) -> Author:
    async with tenant_connection(tenant_id) as conn:
        return await _author(conn, tenant_id, viewer)
