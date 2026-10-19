"""WWC Academy: courses, modules, lessons, access and progress.

One course is shown on the site (/academy/) and in the bot (cabinet →
«Академия»); a person is a telegram_user_id, so progress is shared. The site
reaches this through the content-access session, the bot through the update.

Access to a course:
- preview (PLATFORM_ACADEMY_OPEN=false): only the billing owner and super
  admins — the owner checks the course before partners see it;
- access_rule 'pro'      — partner with paid PRO (partner_paid) or a row in
  academy_access (a key or a purchase opens a PRO course for a non-PRO person);
- access_rule 'free'     — anyone signed in;
- access_rule 'purchase' — a row in academy_access (purchase / gift / admin / key), or
  ``free_for`` (V35, mini-courses): ``{"club": true}`` — the club is active or in grace,
  ``{"courses": [slug, …]}`` — an active access row to one of those courses;
  ``{"unlock": {"quiz": {"course": c, "lesson": l}, "app": true}}`` (V39) — the person passed the
  test of that lesson and the WWC app was seen installed on their device
  (``academy_app_installs``); every listed condition must hold. The reason goes out as
  ``free_reason`` (``club`` | ``course`` | ``unlock``) and no «Купить» button is shown;
  a course still closed by purchase carries ``unlock`` — the person's progress towards it
  (``quiz_passed``, ``app_installed``, ``quiz_url``, ``quiz_course_title``, ``eligible``), else null;
- an access row with ``expires_at`` in the past counts as none;
- staff — the course author (any actor row of the person) and preview admins —
  see their course whole: no access lock, no schedule.

A test with ``retry_after_min`` (V39) puts a pause after a failed attempt: ``POST …/quiz`` is
429 ``quiz_cooldown`` with ``retry_at`` until it passes; ``quiz_state.retry_at`` says when.

Inside a course (Academy v2, 02.10.2026): modules with unlock rules, a lesson may
override its module's rule, a lesson with a required homework is complete only
when the homework is accepted, a lesson with a test (V35) only when a test attempt
passed — ``app.academy.rules``. A passed attempt marks the lesson done itself;
«Сделал» by hand on such a lesson is 409 ``quiz_required``. Lessons loaded before
modules existed (``module_id`` null) are grouped by ``module_title`` and open.

Writers of academy_access: ``grant_course_access`` (the payment path, line
``course_<код>`` → ``partner_subscription_plans.course_slug``),
``grant_course_to_telegram_user`` (a course bought in «Мастерская», app/shop) and
``app.academy.keys.redeem_key`` (an author's key). The author's term in the
Academy is extended from the payment path too (``extend_shelf_in_connection``).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import psycopg

from app.academy.content import media_refs, resolve_media, sanitize_html
from app.academy.keys import actor_ids_for_telegram
from app.academy.media_service import load_media, media_links, media_summary
from app.academy.quiz import grade_quiz, public_quiz, retry_after_min, retry_at
from app.academy.showcases import (
    Showcase,
    default_showcase,
    is_own_course,
    load_showcases,
    owner_actor_ids,
)
from app.academy.rules import (
    LessonIn,
    LessonLock,
    ModuleIn,
    evaluate_locks,
    is_complete,
    next_lesson_key,
)
from app.db import fetch_all, fetch_one, tenant_connection
from app.settings import get_settings
from app.subscriptions.service import (
    add_calendar_months,
    paid_access_allowed,
    resolve_partner_hostname,
    resolve_partner_subscription_by_telegram_user_id,
)

logger = logging.getLogger(__name__)

SHELF_PRODUCT_CODE = "academy_shelf"
# Замок курса → причина на каждом уроке. Коды уроков — ровно ТЗ §5 (after_prev | date:<iso> |
# days:<n> | purchase): PRO-курс без доступа на уроке тоже «purchase», различает course.lock_reason.
COURSE_LOCK_LESSON_REASON = {"purchase_required": "purchase", "pro_required": "purchase"}
LEGACY_MODULE_PREFIX = "legacy:"
CLUB_PRODUCT_CODE = "club_subscription"
QUIZ_REQUIRED = "quiz_required"
MAX_QUIZ_ANSWERS = 100


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


def course_lock_reason(
    course: dict[str, Any], viewer: AcademyViewer, *, has_access_row: bool, free_reason: str | None = None
) -> str | None:
    """None — course is open for the viewer; otherwise a machine reason.
    ``free_reason`` (``free_reason_for``) opens a ``purchase`` course without a purchase."""
    if not academy_visible(viewer):
        return "academy_not_open"
    if viewer.is_preview_admin:
        return None
    rule = str(course.get("access_rule") or "pro")
    if rule == "free":
        return None
    if rule == "pro":
        return None if viewer.partner_paid or has_access_row else "pro_required"
    return None if has_access_row or free_reason else "purchase_required"


@dataclass(frozen=True)
class FreeFacts:
    """What ``free_for`` looks at: the club, the courses the person has access to and (V39, ``unlock``)
    the tests they passed — (course slug, lesson slug) — and whether the WWC app was seen on their device."""

    club: bool
    course_slugs: frozenset[str]
    passed_quizzes: frozenset[tuple[str, str]] = frozenset()
    app_installed: bool = False


@dataclass(frozen=True)
class UnlockSpec:
    """``free_for.unlock`` (V39): every condition present must hold. ``quiz`` — (course slug, lesson slug)
    of the test to pass, ``app`` — the WWC app is installed."""

    quiz: tuple[str, str] | None
    app: bool


def unlock_spec(rule: dict[str, Any] | None) -> UnlockSpec | None:
    """The rule's ``unlock`` as a spec; None when it is absent or not exactly the documented shape.
    A shape that is off (an unknown key, a half-written quiz, nothing to check) is refused whole:
    a condition lost on the way must never open the course."""
    raw = (rule or {}).get("unlock")
    if not isinstance(raw, dict) or set(raw) - {"quiz", "app"}:
        return None
    quiz = None
    if "quiz" in raw:
        item = raw["quiz"]
        if not isinstance(item, dict) or set(item) != {"course", "lesson"}:
            return None
        course, lesson = item["course"], item["lesson"]
        if not (isinstance(course, str) and course and isinstance(lesson, str) and lesson):
            return None
        quiz = (course, lesson)
    app = raw.get("app", False)
    if not isinstance(app, bool) or (quiz is None and not app):
        return None
    return UnlockSpec(quiz=quiz, app=app)


def unlock_met(spec: UnlockSpec, facts: FreeFacts | None) -> bool:
    if facts is None:
        return False
    return (spec.quiz is None or spec.quiz in facts.passed_quizzes) and (not spec.app or facts.app_installed)


def course_unlock(course: dict[str, Any]) -> UnlockSpec | None:
    """The ``unlock`` rule of a ``purchase`` course with ``free_for``; None — the course has none."""
    return unlock_spec(_free_rule(course))


def quiz_path(course_slug: str, lesson_slug: str) -> str:
    """Where the test is on the site: ``/academy/?course=<slug>&lesson=<slug>`` (as the rhythm card)."""
    return f"/academy/?{urlencode({'course': course_slug, 'lesson': lesson_slug})}"


def _free_rule(course: dict[str, Any]) -> dict[str, Any] | None:
    rule = course.get("free_for")
    if str(course.get("access_rule") or "") != "purchase" or not isinstance(rule, dict):
        return None
    return rule


def free_reason_for(course: dict[str, Any], facts: FreeFacts | None) -> str | None:
    """Почему платный курс открыт без покупки (V35, V39): ``club`` | ``course`` | ``unlock`` | None."""
    rule = _free_rule(course)
    if rule is None or facts is None:
        return None
    if rule.get("club") is True and facts.club:
        return "club"
    wanted = {str(slug) for slug in (rule.get("courses") or []) if isinstance(slug, str)}
    if wanted & facts.course_slugs:
        return "course"
    spec = unlock_spec(rule)
    if spec is not None and unlock_met(spec, facts):
        return "unlock"
    return None


async def club_open(conn: Any, tenant_id: str, telegram_user_id: int) -> bool:
    """Клуб открыт: ``club_subscription`` одного из профилей человека действует или в грейсе —
    как «клуб» в кабинете /me/ (account.club: active | grace), не ``is_club_member`` Мастерской
    (там без грейса: клубная цена)."""
    actors = await actor_ids_for_telegram(conn, tenant_id, int(telegram_user_id))
    if not actors:
        return False
    row = await fetch_one(
        conn,
        """
        select max(pa.paid_until) as paid_until from referral_profiles rp
        join partner_product_access pa
          on pa.tenant_id = rp.tenant_id and pa.ref_code = rp.ref_code and pa.product_code = %s
        where rp.tenant_id = %s and rp.enabled = true and rp.owner_id = any(%s)
        """,
        (CLUB_PRODUCT_CODE, tenant_id, actors),
    )
    return paid_access_allowed((row or {}).get("paid_until"))


async def free_facts(
    conn: Any, tenant_id: str, telegram_user_id: int, courses: list[dict[str, Any]]
) -> FreeFacts | None:
    """Facts for the ``free_for`` of these courses; None (no query) when none of them has one."""
    rules = [rule for rule in (_free_rule(course) for course in courses) if rule is not None]
    if not rules:
        return None
    club = any(rule.get("club") is True for rule in rules) and await club_open(conn, tenant_id, telegram_user_id)
    wanted = sorted({str(slug) for rule in rules for slug in (rule.get("courses") or []) if isinstance(slug, str)})
    slugs: set[str] = set()
    if wanted:
        rows = await fetch_all(
            conn,
            f"""
            select c.slug from academy_access a
            join academy_courses c on c.tenant_id = a.tenant_id and c.course_id = a.course_id
            where a.tenant_id = %s and a.telegram_user_id = %s and c.slug = any(%s) and {_ACCESS_ACTIVE}
            """,
            (tenant_id, int(telegram_user_id), wanted),
        )
        slugs = {str(row["slug"]) for row in rows}
    specs = [spec for spec in (unlock_spec(rule) for rule in rules) if spec is not None]
    tests = sorted({spec.quiz for spec in specs if spec.quiz is not None})
    passed: set[tuple[str, str]] = set()
    if tests:
        rows = await fetch_all(
            conn,
            """
            select distinct c.slug as course, l.slug as lesson
            from academy_quiz_attempts q
            join academy_lessons l on l.tenant_id = q.tenant_id and l.lesson_id = q.lesson_id
            join academy_courses c on c.tenant_id = l.tenant_id and c.course_id = l.course_id
            join unnest(%s::text[], %s::text[]) as w(course, lesson) on w.course = c.slug and w.lesson = l.slug
            where q.tenant_id = %s and q.telegram_user_id = %s and q.passed
            """,
            ([course for course, _ in tests], [lesson for _, lesson in tests], tenant_id, int(telegram_user_id)),
        )
        passed = {(str(row["course"]), str(row["lesson"])) for row in rows}
    installed = False
    if any(spec.app for spec in specs):
        installed = await app_installed(conn, tenant_id, telegram_user_id)
    return FreeFacts(
        club=bool(club), course_slugs=frozenset(slugs), passed_quizzes=frozenset(passed), app_installed=installed
    )


async def app_installed(conn: Any, tenant_id: str, telegram_user_id: int) -> bool:
    """The WWC app was seen installed on a device of the person (V39, ``academy_app_installs``)."""
    row = await fetch_one(
        conn,
        "select 1 as ok from academy_app_installs where tenant_id = %s and telegram_user_id = %s",
        (tenant_id, int(telegram_user_id)),
    )
    return row is not None


def price_out(minor: Any, currency: Any) -> dict[str, Any] | None:
    """Price for display only (the student pays the author directly)."""
    if minor is None:
        return None
    amount = int(minor) / 100
    return {"amount": int(amount) if amount.is_integer() else round(amount, 2), "currency": str(currency or "WUSD")}


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


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


async def _author_rows(conn: Any, tenant_id: str, actor_ids: set[str]) -> list[dict[str, Any]]:
    if not actor_ids:
        return []
    return await fetch_all(
        conn,
        """
        select la.actor_id, la.display_name, la.telegram_username, rp.ref_code, rp.public_profile
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


async def _author_contacts(conn: Any, tenant_id: str, actor_ids: set[str]) -> dict[str, dict[str, str | None]]:
    contacts = {}
    for row in await _author_rows(conn, tenant_id, actor_ids):
        contact = _author_contact_from_row(row)
        if contact:
            contacts[str(row["actor_id"])] = contact
    # Автор со своей витриной (V29): контакт и сайт витрины — у студии свой Telegram.
    for showcase in await load_showcases(tenant_id) if actor_ids else []:
        if showcase.owner_actor_id in actor_ids:
            telegram = showcase.links.get("contact_telegram")
            site_url = showcase.links.get("home_url")
            if telegram or site_url:
                contacts[showcase.owner_actor_id] = {"telegram": telegram or None, "site_url": site_url or None}
    return contacts


async def _author_names(conn: Any, tenant_id: str, actor_ids: set[str]) -> dict[str, str]:
    return {
        str(row["actor_id"]): str(row["display_name"])
        for row in await _author_rows(conn, tenant_id, actor_ids)
        if str(row.get("display_name") or "").strip()
    }


_ACCESS_ACTIVE = "revoked_at is null and (expires_at is null or expires_at > now())"


async def _access_rows(conn: Any, tenant_id: str, telegram_user_id: int) -> set[str]:
    rows = await fetch_all(
        conn,
        f"""
        select course_id::text as course_id
        from academy_access
        where tenant_id = %s and telegram_user_id = %s and {_ACCESS_ACTIVE}
        """,
        (tenant_id, telegram_user_id),
    )
    return {row["course_id"] for row in rows}


async def _access_row(conn: Any, tenant_id: str, course_id: str, telegram_user_id: int) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        f"""
        select started_at, granted_at, expires_at, source
        from academy_access
        where tenant_id = %s and course_id = %s::uuid and telegram_user_id = %s and {_ACCESS_ACTIVE}
        """,
        (tenant_id, course_id, telegram_user_id),
    )


async def viewer_actor_ids(conn: Any, tenant_id: str, telegram_user_id: int) -> list[str]:
    return await actor_ids_for_telegram(conn, tenant_id, telegram_user_id)


def is_staff_for(course: dict[str, Any], viewer: AcademyViewer, actor_ids: list[str]) -> bool:
    """The owner / a preview admin, or the course author (any actor row of the person)."""
    author = str(course.get("author_actor_id") or "")
    return viewer.is_preview_admin or bool(author and author in actor_ids)


def _showcase_keeps(
    showcase: Showcase, course: dict[str, Any], *, own: bool, has_access: bool
) -> bool:
    """Курс на витрине: свой; купленный чужой — только там, где чужие курсы вообще показывают
    (``more_mode`` не ``none``): витрина партнёра с ``none`` — только его курсы, даже владельцу
    платформы (решение владельца 14.10: «мои курсы из Алмиры скрой»)."""
    if own:
        return True
    if showcase.more_mode == "none" and not showcase.is_default:
        return False
    if has_access:
        return True
    if showcase.more_mode == "all":
        return True
    if showcase.more_mode == "no_pro":
        return str(course.get("access_rule") or "") != "pro"
    return False


async def list_courses(
    tenant_id: str, viewer: AcademyViewer, showcase: Showcase | None = None
) -> list[dict[str, Any]]:
    """Курсы витрины (V29): свои первыми (``section: own``), затем купленные и — по
    ``more_mode`` — чужие (``section: more``). Замки от витрины не зависят."""
    if not academy_visible(viewer):
        return []
    showcases = await load_showcases(tenant_id)
    if showcase is None:
        showcase = next((s for s in showcases if s.is_default), None) or default_showcase(tenant_id)
    partner_owners = owner_actor_ids(showcases)
    async with tenant_connection(tenant_id) as conn:
        courses = await fetch_all(
            conn,
            """
            select c.course_id::text as course_id, c.slug, c.title, c.subtitle, c.access_rule, c.kind,
                   c.price_wusd_minor, c.price_currency, c.author_actor_id, c.cover_media_id::text as cover_media_id,
                   c.free_for,
                   count(l.lesson_id) as lessons_total,
                   count(l.lesson_id) filter (
                     where p.lesson_id is not null
                       and (a.lesson_id is null or not a.required or acc.ok)
                       and (l.quiz_json is null or qz.ok)
                   ) as lessons_done
            from academy_courses c
            left join academy_lessons l
              on l.tenant_id = c.tenant_id and l.course_id = c.course_id and l.status = 'published'
            left join academy_progress p
              on p.tenant_id = l.tenant_id and p.lesson_id = l.lesson_id and p.telegram_user_id = %(user)s
            left join academy_assignments a
              on a.tenant_id = l.tenant_id and a.lesson_id = l.lesson_id
            left join lateral (
              select true as ok from academy_submissions s
              where s.tenant_id = l.tenant_id and s.lesson_id = l.lesson_id
                and s.telegram_user_id = %(user)s and s.status = 'accepted'
              limit 1
            ) acc on true
            left join lateral (
              select true as ok from academy_quiz_attempts q
              where q.tenant_id = l.tenant_id and q.lesson_id = l.lesson_id
                and q.telegram_user_id = %(user)s and q.passed
              limit 1
            ) qz on true
            where c.tenant_id = %(tenant)s and c.status = 'published'
            group by c.course_id, c.slug, c.title, c.subtitle, c.access_rule, c.kind, c.price_wusd_minor,
                     c.price_currency, c.author_actor_id, c.cover_media_id, c.free_for, c.sort_order
            order by c.sort_order, c.title
            """,
            {"user": viewer.telegram_user_id, "tenant": tenant_id},
        )
        access = await _access_rows(conn, tenant_id, viewer.telegram_user_id)
        actor_ids = await viewer_actor_ids(conn, tenant_id, viewer.telegram_user_id)
        sections: dict[str, str] = {}
        kept = []
        for course in courses:
            own = is_own_course(showcase, course.get("author_actor_id"), partner_owners)
            if _showcase_keeps(showcase, course, own=own, has_access=course["course_id"] in access):
                sections[course["course_id"]] = "own" if own else "more"
                kept.append(course)
        # Свои курсы витрины — первыми; внутри групп прежний порядок (sort_order, title).
        courses = sorted(kept, key=lambda course: sections[course["course_id"]] != "own")
        staff = {course["course_id"] for course in courses if is_staff_for(course, viewer, actor_ids)}
        # Бесплатно клубу / участникам курса (V35) — только тем, кому курс не открыт иначе.
        candidates = [course for course in courses if course["course_id"] not in staff | access]
        facts = await free_facts(conn, tenant_id, viewer.telegram_user_id, candidates)
        free = {course["course_id"]: free_reason_for(course, facts) for course in candidates}
        locks = {
            course["course_id"]: None
            if course["course_id"] in staff
            else course_lock_reason(
                course, viewer, has_access_row=course["course_id"] in access, free_reason=free.get(course["course_id"])
            )
            for course in courses
        }
        # Закрытый покупкой курс с правилом unlock (V39): прогресс человека — тест и приложение.
        from app.academy.unlock import unlock_progress

        unlocks = {
            course["course_id"]: await unlock_progress(conn, tenant_id, viewer, course, facts)
            for course in courses
            if locks[course["course_id"]] == "purchase_required"
        }
        authors = {str(course["author_actor_id"]) for course in courses if course.get("author_actor_id")}
        contacts = await _author_contacts(
            conn,
            tenant_id,
            {
                str(course["author_actor_id"])
                for course in courses
                if course.get("author_actor_id") and locks[course["course_id"]] == "purchase_required"
            },
        )
        names = await _author_names(conn, tenant_id, authors)
        covers = await load_media(conn, tenant_id, [course["cover_media_id"] for course in courses])
        offers = await _purchase_offers(
            conn, tenant_id, [course["slug"] for course in courses if locks[course["course_id"]] == "purchase_required"]
        )
    out = []
    for course in courses:
        lock = locks[course["course_id"]]
        cover = covers.get(str(course.get("cover_media_id") or ""))
        item = {
            "slug": course["slug"],
            "title": course["title"],
            "subtitle": course.get("subtitle"),
            "kind": course.get("kind") or "course",
            "access_rule": course["access_rule"],
            "lessons_total": int(course["lessons_total"] or 0),
            "lessons_done": int(course["lessons_done"] or 0),
            "locked": lock is not None,
            "lock_reason": lock,
            "cover_url": media_links(cover, viewer.telegram_user_id).get("url") if cover else None,
            "price": price_out(course.get("price_wusd_minor"), course.get("price_currency")),
            "author_name": names.get(str(course.get("author_actor_id") or "")),
            "section": sections[course["course_id"]],
            # Открыт без покупки (V35): «club» — клуб, «course» — участник курса, «unlock» (V39) — тест
            # и приложение; кнопки «Купить» нет.
            "free_reason": free.get(course["course_id"]) if lock is None else None,
            # Закрытому курсу с правилом unlock — прогресс (V39); открытому и без правила — null.
            "unlock": unlocks.get(course["course_id"]),
        }
        if lock == "purchase_required":
            # Доступ выдаёт автор курса: ученик берёт у него ключ.
            item["author_contact"] = contacts.get(str(course.get("author_actor_id") or ""))
            if course["slug"] in offers:
                # …или курс продаётся в Мастерской: кнопка «Купить» ведёт в бота.
                item["purchase"] = offers[course["slug"]]
        out.append(item)
    return out


_COURSE_COLUMNS = """
    course_id::text as course_id, slug, title, subtitle, access_rule, kind, price_wusd_minor, price_currency,
    author_actor_id, description_html, cover_media_id::text as cover_media_id, status,
    works_public, review_days, teaser, landing_url, free_for
"""


async def _load_course(conn: Any, tenant_id: str, slug: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        f"select {_COURSE_COLUMNS} from academy_courses where tenant_id = %s and slug = %s and status = 'published'",
        (tenant_id, slug),
    )


class AcademyError(Exception):
    def __init__(self, status: int, code: str, extra: dict[str, Any] | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.extra = dict(extra or {})


@dataclass
class CourseView:
    """A published course as one viewer sees it."""

    course: dict[str, Any]
    lock: str | None
    staff: bool
    started_at: datetime | None
    lock_extra: dict[str, Any] = field(default_factory=dict)
    free_reason: str | None = None  # V35, V39: открыт без покупки — club | course | unlock
    # Без оплаты — самостоятельно (Виктор, 17.10.2026): курс открыт подарком (source gift) или
    # по free_for — задания видны, но на проверку автору не сдаются; проверка — только с оплатой.
    self_study: bool = False
    # Факты ``free_for`` человека (клуб, курсы, тесты, приложение): закрытому курсу с unlock — для прогресса.
    facts: FreeFacts | None = None


async def _first_progress_at(conn: Any, tenant_id: str, course_id: str, telegram_user_id: int) -> datetime | None:
    row = await fetch_one(
        conn,
        """
        select min(p.done_at) as first_done from academy_progress p
        join academy_lessons l on l.tenant_id = p.tenant_id and l.lesson_id = p.lesson_id
        where p.tenant_id = %s and l.course_id = %s::uuid and p.telegram_user_id = %s
        """,
        (tenant_id, course_id, telegram_user_id),
    )
    return row.get("first_done") if row else None


async def _course_view(conn: Any, tenant_id: str, slug: str, viewer: AcademyViewer) -> CourseView:
    course = await _load_course(conn, tenant_id, slug)
    if not course or not academy_visible(viewer):
        raise AcademyError(404, "course_not_found")
    access = await _access_row(conn, tenant_id, course["course_id"], viewer.telegram_user_id)
    staff = is_staff_for(course, viewer, await viewer_actor_ids(conn, tenant_id, viewer.telegram_user_id))
    free = None
    facts = None
    if not staff and access is None:
        facts = await free_facts(conn, tenant_id, viewer.telegram_user_id, [course])
        free = free_reason_for(course, facts)
    lock = None if staff else course_lock_reason(course, viewer, has_access_row=access is not None, free_reason=free)
    extra: dict[str, Any] = {}
    if lock == "purchase_required" and course.get("author_actor_id"):
        author = str(course["author_actor_id"])
        extra["author_contact"] = (await _author_contacts(conn, tenant_id, {author})).get(author)
    if access and access.get("started_at"):
        started = access["started_at"]
    elif access:
        started = access.get("granted_at")
    else:
        # PRO/бесплатный курс без строки доступа: старт — первый пройденный урок.
        started = await _first_progress_at(conn, tenant_id, course["course_id"], viewer.telegram_user_id)
    self_study = not staff and lock is None and (
        free is not None or (access is not None and access.get("source") == "gift")
    )
    return CourseView(
        course=course, lock=lock, staff=staff, started_at=started, lock_extra=extra,
        free_reason=free if lock is None else None, self_study=self_study, facts=facts,
    )


async def _open_course(conn: Any, tenant_id: str, slug: str, viewer: AcademyViewer) -> CourseView:
    view = await _course_view(conn, tenant_id, slug, viewer)
    if view.lock:
        raise AcademyError(403, view.lock, {**view.lock_extra, "lock_reason": COURSE_LOCK_LESSON_REASON.get(view.lock)})
    return view


async def _load_modules(conn: Any, tenant_id: str, course_id: str) -> list[dict[str, Any]]:
    return await fetch_all(
        conn,
        """
        select module_id::text as module_id, position, title, unlock
        from academy_modules
        where tenant_id = %s and course_id = %s::uuid
        order by position, created_at
        """,
        (tenant_id, course_id),
    )


async def _load_lessons(
    conn: Any, tenant_id: str, course_id: str, telegram_user_id: int, *, self_study: bool = False,
) -> list[dict[str, Any]]:
    rows = await fetch_all(
        conn,
        """
        select l.lesson_id::text as lesson_id, l.slug, l.module_id::text as module_id, l.module_title,
               l.position, l.title, l.short_title, l.result_text, l.est_minutes, l.kind, l.live_at, l.unlock,
               (p.lesson_id is not null) as done,
               a.required as assignment_required,
               s.status as submission_status,
               s.unread as submission_unread,
               (l.quiz_json is not null) as has_quiz,
               exists (
                 select 1 from academy_quiz_attempts q
                 where q.tenant_id = l.tenant_id and q.lesson_id = l.lesson_id
                   and q.telegram_user_id = %(user)s and q.passed
               ) as quiz_passed
        from academy_lessons l
        left join academy_progress p
          on p.tenant_id = l.tenant_id and p.lesson_id = l.lesson_id and p.telegram_user_id = %(user)s
        left join academy_assignments a
          on a.tenant_id = l.tenant_id and a.lesson_id = l.lesson_id
        left join lateral (
          select s.status,
                 (select count(*) from academy_submission_messages m
                   where m.tenant_id = s.tenant_id and m.submission_id = s.submission_id and m.sender = 'author'
                     and (s.student_seen_at is null or m.created_at > s.student_seen_at)) as unread
          from academy_submissions s
          where s.tenant_id = l.tenant_id and s.lesson_id = l.lesson_id and s.telegram_user_id = %(user)s
          order by s.created_at desc
          limit 1
        ) s on true
        where l.tenant_id = %(tenant)s and l.course_id = %(course)s::uuid and l.status = 'published'
        order by l.position, l.created_at
        """,
        {"user": telegram_user_id, "tenant": tenant_id, "course": course_id},
    )
    if self_study:
        # Самостоятельно: задание есть, но не сдаётся — урок не ждёт, пока автор примет работу.
        rows = [{**row, "assignment_required": False} if row.get("assignment_required") is not None else row for row in rows]
    return rows


def build_structure(
    module_rows: list[dict[str, Any]], lesson_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Modules in course order (legacy groups by ``module_title`` first) and lessons
    in course order, each lesson with its ``module_key`` and ``module_title``."""
    real = {str(row["module_id"]): row for row in module_rows}
    legacy: list[str] = []
    for lesson in lesson_rows:
        if str(lesson.get("module_id") or "") not in real:
            key = LEGACY_MODULE_PREFIX + str(lesson.get("module_title") or "")
            if key not in legacy:
                legacy.append(key)
    modules = [
        {"key": key, "module_id": None, "title": key[len(LEGACY_MODULE_PREFIX):], "position": 0, "unlock": None}
        for key in legacy
    ] + [
        {"key": str(row["module_id"]), "module_id": str(row["module_id"]), "title": row["title"],
         "position": int(row["position"]), "unlock": row.get("unlock")}
        for row in module_rows
    ]
    order = {module["key"]: index for index, module in enumerate(modules)}
    titles = {module["key"]: module["title"] for module in modules}
    lessons = []
    for lesson in lesson_rows:
        module_id = str(lesson.get("module_id") or "")
        key = module_id if module_id in real else LEGACY_MODULE_PREFIX + str(lesson.get("module_title") or "")
        lessons.append({**lesson, "module_key": key, "module_title": titles[key]})
    lessons.sort(key=lambda item: (order[item["module_key"]], int(item["position"])))
    return modules, lessons


def _lesson_in(row: dict[str, Any]) -> LessonIn:
    return LessonIn(
        key=row["lesson_id"],
        module_key=row["module_key"],
        unlock=row.get("unlock"),
        done=bool(row.get("done")),
        assignment_required=row.get("assignment_required"),
        submission_status=row.get("submission_status"),
        quiz_required=bool(row.get("has_quiz")),
        quiz_passed=bool(row.get("quiz_passed")),
    )


def _evaluate(view: CourseView, modules: list[dict[str, Any]], lessons: list[dict[str, Any]]) -> dict[str, LessonLock]:
    return evaluate_locks(
        [ModuleIn(module["key"], module.get("unlock")) for module in modules],
        [_lesson_in(row) for row in lessons],
        now=datetime.now(timezone.utc),
        started_at=view.started_at,
        course_lock=COURSE_LOCK_LESSON_REASON.get(view.lock or ""),
        bypass=view.staff,
    )


def assignment_status(row: dict[str, Any]) -> str | None:
    """None — no homework; ``none`` (not handed in) | ``submitted`` | ``accepted`` | ``returned`` (ТЗ §5)."""
    if row.get("assignment_required") is None:
        return None
    return str(row.get("submission_status") or "none")


def _lesson_summary(row: dict[str, Any], number: int, lock: LessonLock, slugs: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "slug": row["slug"],
        "position": int(row["position"]),
        "number": number,
        "module_id": None if row["module_key"].startswith(LEGACY_MODULE_PREFIX) else row["module_key"],
        "module_title": row.get("module_title") or "",
        "title": row["title"],
        "short_title": row.get("short_title") or row["title"],
        "result": row.get("result_text") or "",
        "minutes": row.get("est_minutes"),
        "kind": row.get("kind") or "lesson",
        "live_at": _iso(row.get("live_at")),
        "done": bool(row.get("done")),
        "complete": is_complete(_lesson_in(row)),
        "locked": lock.locked,
        "lock_reason": lock.reason,
        "opens_at": _iso(lock.opens_at),
        # after_prev: урок, после которого откроется (сайт пишет «откроется после урока N»).
        "lock_after": (slugs or {}).get(lock.after or ""),
        "assignment_status": assignment_status(row),
        # Непрочитанные ответы преподавателя (V32) — метка «Новый ответ» в содержании.
        "unread": int(row.get("submission_unread") or 0),
        # Тест в конце урока (V35): урок зачтён, когда тест сдан (``complete``).
        "has_quiz": bool(row.get("has_quiz")),
    }


def _summaries(lessons: list[dict[str, Any]], locks: dict[str, LessonLock]) -> list[dict[str, Any]]:
    slugs = {row["lesson_id"]: row["slug"] for row in lessons}
    return [_lesson_summary(row, index + 1, locks[row["lesson_id"]], slugs) for index, row in enumerate(lessons)]


def _locked_extra(lessons: list[dict[str, Any]], lock: LessonLock) -> dict[str, Any]:
    after = next((row["slug"] for row in lessons if row["lesson_id"] == lock.after), None) if lock.after else None
    return {"lock_reason": lock.reason, "opens_at": _iso(lock.opens_at), "lock_after": after}


async def viewer_profile(tenant_id: str, telegram_user_id: int) -> dict[str, str | None]:
    """Имя вошедшего для шапки кабинета (инициалы): из lead_actors, иначе пусто.
    Украшение, не доступ: сбой поиска пишется в лог и не роняет страницу курса."""
    try:
        async with tenant_connection(tenant_id) as conn:
            row = await fetch_one(
                conn,
                """
                select display_name, telegram_username from lead_actors
                where tenant_id = %s and telegram_user_id = %s
                order by active desc
                limit 1
                """,
                (tenant_id, int(telegram_user_id)),
            )
    except Exception:
        logger.warning("academy_viewer_profile_failed", exc_info=True)
        row = None
    name = str((row or {}).get("display_name") or "").strip()
    username = str((row or {}).get("telegram_username") or "").strip().lstrip("@")
    # label — подпись водяного знака: всегда непустая (имя → @ник → id), иначе защита молча
    # пропала бы у ученика с пустым или «точечным» именем в Telegram.
    label = name or (f"@{username}" if username else f"ID {int(telegram_user_id)}")
    return {"name": name or None, "username": username or None, "label": label}


def _next_slug(view: CourseView, lessons: list[dict[str, Any]], locks: dict[str, LessonLock]) -> str | None:
    if view.lock:
        # Курс под замком: «следующий шаг» — первый урок, он покажет замок с контактом автора.
        return lessons[0]["slug"] if lessons else None
    key = next_lesson_key([_lesson_in(row) for row in lessons], locks)
    return next((row["slug"] for row in lessons if row["lesson_id"] == key), None)


def _progress(summaries: list[dict[str, Any]]) -> dict[str, int]:
    total = len(summaries)
    done = sum(1 for item in summaries if item["complete"])
    return {"lessons_total": total, "lessons_done": done, "progress_pct": round(100 * done / total) if total else 0}


def _module_payload(modules: list[dict[str, Any]], summaries: list[dict[str, Any]], lessons: list[dict[str, Any]]):
    by_key: dict[str, list[dict[str, Any]]] = {}
    for summary, row in zip(summaries, lessons):
        by_key.setdefault(row["module_key"], []).append(summary)
    out = []
    for module in modules:
        items = by_key.get(module["key"])
        if not items:
            continue  # пустой модуль ученику не показываем
        reasons = {item["lock_reason"] for item in items}
        all_locked = all(item["locked"] for item in items)
        out.append(
            {
                "module_id": module["module_id"],
                "title": module["title"],
                "position": module["position"],
                "locked": all_locked,
                "lock_reason": next(iter(reasons)) if all_locked and len(reasons) == 1 else None,
                "lessons": items,
            }
        )
    return out


def _signed_urls(media: dict[str, dict[str, Any]], telegram_user_id: int) -> dict[str, str]:
    urls = {}
    for media_id, row in media.items():
        url = media_links(row, telegram_user_id).get("url")
        if url:
            urls[media_id] = url
    return urls


async def course_outline(
    tenant_id: str, slug: str, viewer: AcademyViewer, *, allow_locked: bool = False
) -> dict[str, Any]:
    """Course plan. ``allow_locked`` (the site): a course without access still shows its
    modules and lessons, every lesson locked with ``purchase`` / ``pro``. The bot keeps
    the old contract: AcademyError with the course lock."""
    async with tenant_connection(tenant_id) as conn:
        view = await (_course_view if allow_locked else _open_course)(conn, tenant_id, slug, viewer)
        course = view.course
        module_rows = await _load_modules(conn, tenant_id, course["course_id"])
        lesson_rows = await _load_lessons(conn, tenant_id, course["course_id"], viewer.telegram_user_id, self_study=view.self_study)
        description = sanitize_html(course.get("description_html")) if course.get("description_html") else ""
        media = await load_media(
            conn, tenant_id,
            [course.get("cover_media_id"), *media_refs(description), (course.get("teaser") or {}).get("media_id")],
        )
        author = str(course.get("author_actor_id") or "")
        names = await _author_names(conn, tenant_id, {author} if author else set())
        unlock = None
        if view.lock == "purchase_required":
            from app.academy.unlock import unlock_progress

            unlock = await unlock_progress(conn, tenant_id, viewer, course, view.facts)
    modules, lessons = build_structure(module_rows, lesson_rows)
    locks = _evaluate(view, modules, lessons)
    summaries = _summaries(lessons, locks)
    urls = _signed_urls(media, viewer.telegram_user_id)
    payload_course = {
        "slug": course["slug"],
        "title": course["title"],
        "subtitle": course.get("subtitle"),
        "kind": course.get("kind") or "course",
        "description_html": resolve_media(description, urls) if description else "",
        "cover_url": urls.get(str(course.get("cover_media_id") or "")),
        "price": price_out(course.get("price_wusd_minor"), course.get("price_currency")),
        "author_name": names.get(author),
        "locked": view.lock is not None,
        "lock_reason": view.lock,
        "free_reason": view.free_reason,
        # Закрытому курсу с правилом unlock (V39) — прогресс человека; открытому и без правила — null.
        "unlock": unlock,
        "self_study": view.self_study,
        **_progress(summaries),
        "next_lesson": _next_slug(view, lessons, locks),
        "started_at": _iso(view.started_at),
        # Витрина курса (V32): видеообращение и страница на сайте — для закрытого курса тоже;
        # работы группы и срок ответа преподавателя — для открытого.
        "teaser": video_out(course.get("teaser") or None, media, viewer.telegram_user_id),
        "landing_url": course.get("landing_url") or None,
        "works_public": bool(course.get("works_public")),
        "review_days": int(course.get("review_days") or 0),
    }
    if view.lock_extra.get("author_contact") is not None or view.lock == "purchase_required":
        payload_course["author_contact"] = view.lock_extra.get("author_contact")
    if view.lock == "purchase_required":
        async with tenant_connection(tenant_id) as conn:
            offer = (await _purchase_offers(conn, tenant_id, [course["slug"]])).get(course["slug"])
        if offer:
            payload_course["purchase"] = offer
    return {"course": payload_course, "modules": _module_payload(modules, summaries, lessons), "lessons": summaries}


async def _purchase_offers(conn: Any, tenant_id: str, slugs: list[str]) -> dict[str, dict[str, Any]]:
    """Карточки Мастерской для платных курсов; сбой — без кнопки «Купить», страница жива."""
    if not slugs:
        return {}
    from app.shop.service import course_offers  # Мастерская импортирует Академию: только здесь

    try:
        async with conn.transaction():  # точка сохранения: сбой не ломает остальной запрос страницы
            return await course_offers(conn, tenant_id, slugs)
    except Exception:
        logger.warning("academy_purchase_offers_failed", exc_info=True, extra={"courses": slugs})
        return {}


async def _latest_submission(conn: Any, tenant_id: str, lesson_id: str, telegram_user_id: int) -> dict[str, Any] | None:
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
        (tenant_id, lesson_id, telegram_user_id),
    )


def submission_out(row: dict[str, Any] | None, media: dict[str, dict[str, Any]], telegram_user_id: int) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "submission_id": row["submission_id"],
        "status": row["status"],
        "text": row.get("text") or "",
        "media": [
            media_summary(media[str(media_id)], telegram_user_id)
            for media_id in (row.get("media") or [])
            if str(media_id) in media
        ],
        "author_comment": row.get("author_comment"),
        "created_at": _iso(row.get("created_at")),
        "reviewed_at": _iso(row.get("reviewed_at")),
    }


def video_out(video: dict[str, Any] | None, media: dict[str, dict[str, Any]], telegram_user_id: int) -> dict[str, Any] | None:
    """``{media_id}`` → our file (the old site's ``provider: file`` shape); a provider
    embed stays as it is."""
    if not video:
        return None
    media_id = str(video.get("media_id") or "")
    if not media_id:
        return dict(video) if video.get("provider") and video.get("id") else None
    row = media.get(media_id)
    if not row:
        return None
    links = media_links(row, telegram_user_id)
    variants = row.get("variants") or {}
    return {
        "provider": "file",
        "media_id": media_id,
        "status": row["status"],
        "id": links.get("url"),
        "poster": links.get("poster_url"),
        "duration_sec": variants.get("duration_sec"),
    }


async def lesson_detail(tenant_id: str, slug: str, lesson_slug: str, viewer: AcademyViewer) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        view = await _open_course(conn, tenant_id, slug, viewer)
        course = view.course
        modules, lessons = build_structure(
            await _load_modules(conn, tenant_id, course["course_id"]),
            await _load_lessons(conn, tenant_id, course["course_id"], viewer.telegram_user_id, self_study=view.self_study),
        )
        locks = _evaluate(view, modules, lessons)
        index = next((i for i, item in enumerate(lessons) if item["slug"] == lesson_slug), None)
        if index is None:
            raise AcademyError(404, "lesson_not_found")
        lock = locks[lessons[index]["lesson_id"]]
        if lock.locked:
            raise AcademyError(403, "lesson_locked", _locked_extra(lessons, lock))
        lesson_id = lessons[index]["lesson_id"]
        row = await fetch_one(
            conn,
            """
            select body_html, checklist, video, files, live_url, quiz_json
            from academy_lessons where tenant_id = %s and lesson_id = %s::uuid
            """,
            (tenant_id, lesson_id),
        )
        quiz_state = (
            await _quiz_state(
                conn, tenant_id, lesson_id, viewer.telegram_user_id, retry_after_min(row["quiz_json"])
            )
            if row.get("quiz_json")
            else None
        )
        assignment = await fetch_one(
            conn,
            "select prompt_html, required from academy_assignments where tenant_id = %s and lesson_id = %s::uuid",
            (tenant_id, lesson_id),
        )
        submission = await _latest_submission(conn, tenant_id, lesson_id, viewer.telegram_user_id)
        # Нить домашки (V32): сообщения ученика и преподавателя; открытие урока — прочтение ответов.
        from app.academy.threads import load_messages, mark_student_seen, messages_media_ids, thread_out

        thread_messages = (
            (await load_messages(conn, tenant_id, [submission["submission_id"]])).get(submission["submission_id"], [])
            if submission else []
        )
        body = sanitize_html(row["body_html"])
        video = row.get("video") or None
        files = [str(item) for item in (row.get("files") or [])]
        media = await load_media(
            conn,
            tenant_id,
            [
                *media_refs(body), *files, (video or {}).get("media_id"),
                *((submission or {}).get("media") or []), *messages_media_ids(thread_messages),
            ],
        )
        my_submission = thread_out(submission, thread_messages, media, viewer.telegram_user_id)
        if my_submission and my_submission["unread"]:
            await mark_student_seen(conn, tenant_id, submission["submission_id"])
        author_id = str(course.get("author_actor_id") or "")
        author_name = (await _author_names(conn, tenant_id, {author_id})).get(author_id) if author_id else None
    summaries = _summaries(lessons, locks)
    summary = summaries[index]
    urls = _signed_urls(media, viewer.telegram_user_id)
    return {
        "course": {
            "slug": course["slug"],
            "title": course["title"],
            "author_name": author_name,
            **_progress(summaries),
        },
        "lesson": {
            **summary,
            "body_html": resolve_media(body, urls),
            "checklist": list(row.get("checklist") or []),
            "video": video_out(video, media, viewer.telegram_user_id),
            "files": [
                media_summary(media[media_id], viewer.telegram_user_id)
                for media_id in files
                if media_id in media and media[media_id]["status"] == "ready"
            ],
            "live_url": row.get("live_url") if summary["kind"] == "live" else None,
            "assignment": (
                {
                    "prompt_html": sanitize_html(assignment["prompt_html"]),
                    "required": bool(assignment["required"]) and not view.self_study,
                    "review_days": int(course.get("review_days") or 0),
                    # Без оплаты: задание для себя, кнопки «Сдать» нет (17.10.2026).
                    "self_study": view.self_study,
                }
                if assignment
                else None
            ),
            "my_submission": my_submission,
            "works_public": bool(course.get("works_public")),
            # Тест (V35): без верных ответов и подсказок — они приходят в ответ на попытку.
            "quiz": public_quiz(row.get("quiz_json")),
            "quiz_state": quiz_state,
        },
        "prev": summaries[index - 1] if index > 0 else None,
        "next": summaries[index + 1] if index + 1 < len(summaries) else None,
    }


async def open_lesson(conn: Any, tenant_id: str, slug: str, lesson_slug: str, viewer: AcademyViewer):
    """(view, lesson row) for an action on an open lesson; AcademyError otherwise."""
    view = await _open_course(conn, tenant_id, slug, viewer)
    modules, lessons = build_structure(
        await _load_modules(conn, tenant_id, view.course["course_id"]),
        await _load_lessons(conn, tenant_id, view.course["course_id"], viewer.telegram_user_id, self_study=view.self_study),
    )
    lesson = next((item for item in lessons if item["slug"] == lesson_slug), None)
    if lesson is None:
        raise AcademyError(404, "lesson_not_found")
    lock = _evaluate(view, modules, lessons)[lesson["lesson_id"]]
    if lock.locked:
        raise AcademyError(403, "lesson_locked", _locked_extra(lessons, lock))
    return view, lesson


async def course_progress(conn: Any, tenant_id: str, view: CourseView, telegram_user_id: int) -> dict[str, Any]:
    modules, lessons = build_structure(
        await _load_modules(conn, tenant_id, view.course["course_id"]),
        await _load_lessons(conn, tenant_id, view.course["course_id"], telegram_user_id, self_study=view.self_study),
    )
    locks = _evaluate(view, modules, lessons)
    summaries = _summaries(lessons, locks)
    upcoming = _next_slug(view, lessons, locks)
    return {
        **_progress(summaries),
        "next_lesson": next((item for item in summaries if item["slug"] == upcoming), None),
    }


async def _mark_done(conn: Any, tenant_id: str, telegram_user_id: int, lesson_id: str, source: str) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into academy_progress (tenant_id, telegram_user_id, lesson_id, source)
            values (%s, %s, %s::uuid, %s)
            on conflict (tenant_id, telegram_user_id, lesson_id) do nothing
            """,
            (tenant_id, telegram_user_id, lesson_id, source),
        )


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
        view, lesson = await open_lesson(conn, tenant_id, slug, lesson_slug, viewer)
        if done and lesson.get("has_quiz") and not lesson.get("quiz_passed"):
            # Урок с тестом засчитывает сданный тест (POST …/quiz), а не кнопка.
            raise AcademyError(409, QUIZ_REQUIRED)
        if done:
            await _mark_done(conn, tenant_id, viewer.telegram_user_id, lesson["lesson_id"], source)
        else:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    delete from academy_progress
                    where tenant_id = %s and telegram_user_id = %s and lesson_id = %s::uuid
                    """,
                    (tenant_id, viewer.telegram_user_id, lesson["lesson_id"]),
                )
        progress = await course_progress(conn, tenant_id, view, viewer.telegram_user_id)
    return {"ok": True, "done": done, **progress}


async def _quiz_state(
    conn: Any, tenant_id: str, lesson_id: str, telegram_user_id: int, pause_min: int = 0
) -> dict[str, Any]:
    """Попытки человека по тесту урока. ``retry_at`` (V39) — ISO UTC, когда можно пересдавать, если у
    теста есть пауза (``pause_min``) и он не сдан; иначе null. Часы — базы: ``created_at`` тоже её."""
    row = await fetch_one(
        conn,
        """
        select count(*) as attempts, coalesce(max(score), 0) as best, coalesce(bool_or(passed), false) as passed,
               max(created_at) as last_at, now() as db_now
        from academy_quiz_attempts
        where tenant_id = %s and lesson_id = %s::uuid and telegram_user_id = %s
        """,
        (tenant_id, lesson_id, telegram_user_id),
    )
    row = row or {}
    passed = bool(row.get("passed"))
    moment = retry_at(pause_min, row.get("last_at"), passed=passed, now=row.get("db_now") or datetime.now(timezone.utc))
    return {
        "passed": passed,
        "best": int(row.get("best") or 0),
        "attempts": int(row.get("attempts") or 0),
        "retry_at": _iso(moment),
    }


async def submit_quiz(
    tenant_id: str, slug: str, lesson_slug: str, viewer: AcademyViewer, *, answers: dict[str, Any]
) -> dict[str, Any]:
    """Попытка теста (V35): баллы, по каждому вопросу — верно ли и подсказка выбранного варианта.
    Число попыток не ограничено; сдан — урок отмечен (как «Сделал» на сайте). Пауза после неудачной
    попытки (V39, ``retry_after_min``): пока она идёт — 429 ``quiz_cooldown`` с ``retry_at``,
    попытка не пишется. Сдан тест — человеку, у которого от этого открывается мини-курс
    (``free_for.unlock``), уходит сообщение в бот (``app.academy.unlock``), один раз."""
    if not isinstance(answers, dict) or len(answers) > MAX_QUIZ_ANSWERS:
        raise AcademyError(400, "bad_answers")
    async with tenant_connection(tenant_id) as conn:
        view, lesson = await open_lesson(conn, tenant_id, slug, lesson_slug, viewer)
        row = await fetch_one(
            conn,
            "select quiz_json from academy_lessons where tenant_id = %s and lesson_id = %s::uuid",
            (tenant_id, lesson["lesson_id"]),
        )
        quiz = (row or {}).get("quiz_json")
        if not quiz:
            raise AcademyError(404, "quiz_not_found")
        pause = retry_after_min(quiz)
        if pause:
            waiting = (await _quiz_state(conn, tenant_id, lesson["lesson_id"], viewer.telegram_user_id, pause))["retry_at"]
            if waiting:
                raise AcademyError(429, "quiz_cooldown", {"retry_at": waiting})
        result = grade_quiz(quiz, answers)
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into academy_quiz_attempts (tenant_id, lesson_id, telegram_user_id, answers, score, passed)
                values (%s, %s::uuid, %s, %s::jsonb, %s, %s)
                """,
                (
                    tenant_id, lesson["lesson_id"], viewer.telegram_user_id, json.dumps(result.answers),
                    result.score, result.passed,
                ),
            )
        if result.passed:
            await _mark_done(conn, tenant_id, viewer.telegram_user_id, lesson["lesson_id"], "site")
            from app.academy.unlock import notify_unlocks

            await notify_unlocks(conn, tenant_id, viewer.telegram_user_id, quiz=(slug, lesson_slug))
        progress = await course_progress(conn, tenant_id, view, viewer.telegram_user_id)
        state = await _quiz_state(conn, tenant_id, lesson["lesson_id"], viewer.telegram_user_id, pause)
    return {
        "ok": True,
        "score": result.score,
        "pass": int(quiz["pass"]),
        "passed": result.passed,
        "results": result.results,
        # Урок отмечен: этой попыткой или раньше (ручная отметка до появления теста).
        "done": result.passed or bool(lesson.get("done")),
        "quiz_state": state,
        **progress,
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


# Курс в подарок к покупке (владелец, 12.10.2026: «всем, кто купил курс — открыть бесплатный базовый курс»).
# Строка academy_access source='gift' — открыт и без PRO, срок сайта на него не влияет.
BONUS_COURSES: dict[str, tuple[str, ...]] = {"online-start-4w": ("zapusk-wwc",)}


async def grant_bonus_courses(conn: Any, tenant_id: str, *, course_slug: str, telegram_user_id: int) -> list[str]:
    """Выдать подарочные курсы к ``course_slug``. Возвращает слаги, которые нашлись в Академии."""
    given: list[str] = []
    for slug in BONUS_COURSES.get(course_slug, ()):
        course = await fetch_one(
            conn,
            "select course_id::text as course_id from academy_courses where tenant_id = %s and slug = %s",
            (tenant_id, slug),
        )
        if not course:
            logger.warning("academy_bonus_course_missing", extra={"tenant_id": tenant_id, "course_slug": slug})
            continue
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into academy_access (tenant_id, course_id, telegram_user_id, source, payment_ref)
                values (%s, %s::uuid, %s, 'gift', %s)
                on conflict (tenant_id, course_id, telegram_user_id)
                do update set revoked_at = null, expires_at = null
                """,
                (tenant_id, course["course_id"], int(telegram_user_id), f"bonus:{course_slug}"),
            )
        given.append(slug)
    return given


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
            do update set revoked_at = null, expires_at = null, source = 'purchase',
                          payment_ref = excluded.payment_ref, granted_at = now()
            """,
            (tenant_id, course["course_id"], telegram_user_id, payment_ref),
        )
    await grant_bonus_courses(conn, tenant_id, course_slug=course_slug, telegram_user_id=telegram_user_id)
    return telegram_user_id


async def grant_course_to_telegram_user(
    conn: Any, tenant_id: str, *, course_slug: str, telegram_user_id: int, payment_ref: str
) -> bool:
    """Мастерская WWC (03.10.2026): курс куплен через бота — доступ по Telegram id
    покупателя, а не владельца профиля: купить может любой. Курса нет — False."""
    course = await fetch_one(
        conn,
        "select course_id::text as course_id from academy_courses where tenant_id = %s and slug = %s",
        (tenant_id, course_slug),
    )
    if not course:
        logger.warning("academy_grant_course_missing", extra={"tenant_id": tenant_id, "course_slug": course_slug})
        return False
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into academy_access (tenant_id, course_id, telegram_user_id, source, payment_ref)
            values (%s, %s::uuid, %s, 'purchase', %s)
            on conflict (tenant_id, course_id, telegram_user_id)
            do update set revoked_at = null, expires_at = null, source = 'purchase',
                          payment_ref = excluded.payment_ref, granted_at = now()
            """,
            (tenant_id, course["course_id"], int(telegram_user_id), payment_ref),
        )
    await grant_bonus_courses(conn, tenant_id, course_slug=course_slug, telegram_user_id=int(telegram_user_id))
    return True


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
    """Paid author line → ``academy_shelf.paid_until`` of the profile owner.

    A term still paid is extended from its end, a lapsed one from now — like
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
