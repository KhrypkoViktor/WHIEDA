"""Личный кабинет партнёра /me/: данные для сайта, путь, заявки на изменение профиля.

Человек — сессия content-access (telegram_user_id), тот же вход, что у WWC CRM и
Академии. Всё читается по его telegram_user_id; про других людей — только
счётчики и список приглашённых им (как «Мои рефералы» в боте).

Уровень и замки — ``app.cabinet.journey``; поля профиля — ``app.cabinet.profile``;
сообщения владельцу и партнёру — ``app.telegram.cabinet_profile``.

Заявка на изменение сайта: одна pending на ref_code (advisory-lock на ref,
новая переводит прежнюю в replaced). «Применить» и отказ проходят через тот же
lock, поэтому применить можно только то, что владелец видел в карточке.
ПДн в логи не пишутся: только id заявки и ref.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.academy.service import AcademyViewer, academy_open, course_lock_reason, preview_admin_ids
from app.cabinet.journey import JourneyFacts, cabinet_locks, cabinet_tier, journey_steps
from app.cabinet.photos import ProcessedPhoto
from app.cabinet.profile import (
    ADDRESS_MAX,
    BIO_MAX,
    DISPLAY_NAME_MAX,
    DISPLAY_NAME_MIN,
    ProfileValidationError,
    current_profile_fields,
    diff_profile,
    media_id_from_url,
    media_id_in_url,
    media_public_url,
    normalize_profile_input,
    overlay_changes,
    apply_profile_changes,
    profile_complete,
)
from app.content_access.account import load_site_account
from app.db import fetch_all, fetch_one, tenant_connection
from app.referral_bonus.service import (
    TelegramIdentityConflictError,
    ensure_telegram_actor,
    get_or_create_invite_code,
    referral_counts,
)
from app.settings import get_settings
from app.subscriptions.service import resolve_partner_hostname, resolve_partner_subscription_by_telegram_user_id

logger = logging.getLogger(__name__)

LAUNCH_COURSE_SLUG = "zapusk-wwc"
UPLOADS_PER_DAY = 30
REQUESTS_PER_HOUR = 10  # каждая заявка — сообщение владельцу
REQUESTS_SHOWN = 5
NEWS_CHANNEL_URL = "https://t.me/Whieda_world_club"
JOURNEY_STEPS_MANUAL = ("presentation", "invite_sent")

# Подписи статусов заявки для сообщений.
REQUEST_STATUS_WORDS = {
    "applied": "применена",
    "rejected": "отклонена",
    "replaced": "заменена новой",
    "cancelled": "отозвана партнёром",
    "pending": "на проверке",
}


class CabinetError(Exception):
    def __init__(self, code: str, status: int, **extra: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.extra = extra


def safe_error(exc: BaseException) -> dict[str, Any]:
    """В лог — класс и SQLSTATE, не текст: в тексте psycopg бывает строка с ПДн."""
    return {"error_class": type(exc).__name__, "sqlstate": getattr(exc, "sqlstate", None)}


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


# ---- кто смотрит -----------------------------------------------------------------


@dataclass(frozen=True)
class CabinetPerson:
    telegram_user_id: int
    actor_id: str
    display_name: str
    telegram_username: str | None
    subscription: dict[str, Any] | None

    @property
    def ref_code(self) -> str | None:
        return _text((self.subscription or {}).get("ref_code"))

    @property
    def public_profile(self) -> dict[str, Any]:
        value = (self.subscription or {}).get("public_profile")
        return value if isinstance(value, dict) else {}

    @property
    def partner_paid(self) -> bool:
        return bool((self.subscription or {}).get("partner_paid"))

    @property
    def greeting_name(self) -> str | None:
        name = _text(self.public_profile.get("display_name"))
        if name:
            return name
        raw = (self.display_name or "").strip()
        if not raw or raw.startswith("Telegram user "):
            return None
        return raw


async def load_person(tenant_id: str, telegram_user_id: int) -> CabinetPerson:
    """Аккаунт человека в боте; нет строки — заводим (вход на сайт был через бота)."""
    user_id = int(telegram_user_id)
    try:
        # В личном чате id чата совпадает с id пользователя.
        actor_id = await ensure_telegram_actor(
            tenant_id, telegram_user_id=user_id, telegram_chat_id=user_id, raw_update={}
        )
    except TelegramIdentityConflictError as exc:
        raise CabinetError("identity_conflict", 409) from exc
    async with tenant_connection(tenant_id) as conn:
        actor = await fetch_one(
            conn,
            "select display_name, telegram_username from lead_actors where tenant_id = %s and actor_id = %s",
            (tenant_id, actor_id),
        )
    subscription = await resolve_partner_subscription_by_telegram_user_id(tenant_id, user_id, on_ambiguous="best")
    return CabinetPerson(
        telegram_user_id=user_id,
        actor_id=actor_id,
        display_name=str((actor or {}).get("display_name") or ""),
        telegram_username=_text((actor or {}).get("telegram_username")),
        subscription=subscription,
    )


def _days_left(paid_until: datetime | None, now: datetime) -> int | None:
    if paid_until is None:
        return None
    return max(0, (paid_until - now).days)


def site_block(person: CabinetPerson, *, bot_username: str | None, now: datetime | None = None) -> dict[str, Any] | None:
    """«Мой сайт»: адрес, статус PRO, сколько дней; продление — в боте."""
    if not person.ref_code:
        return None
    moment = now or datetime.now(timezone.utc)
    try:
        host = resolve_partner_hostname(person.ref_code, person.public_profile)
    except Exception:  # служебный или битый поддомен — сайт всё равно есть
        host = None
    paid_until = (person.subscription or {}).get("paid_until")
    return {
        "ref_code": person.ref_code,
        "host": host,
        "url": f"https://{host}/" if host else None,
        "status": str((person.subscription or {}).get("subscription_status") or "no_subscription"),
        "paid_until": _iso(paid_until),
        "days_left": _days_left(paid_until, moment),
        "renew_url": bot_link(bot_username, "renew"),
        "price_url": "/start/",
    }


def bot_link(bot_username: str | None, start: str | None = None) -> str | None:
    name = str(bot_username or "").lstrip("@").strip()
    if not name:
        return None
    return f"https://t.me/{name}" + (f"?start={start}" if start else "")


# ---- что известно о человеке ------------------------------------------------------

_ACADEMY_SQL = """
with courses as (
  select c.tenant_id, c.course_id, c.slug, c.title, c.access_rule,
         exists (
           select 1 from academy_access a
           where a.tenant_id = c.tenant_id and a.course_id = c.course_id
             and a.telegram_user_id = %(user_id)s and a.revoked_at is null
         ) as has_access
  from academy_courses c
  where c.tenant_id = %(tenant_id)s and c.status = 'published'
    and (c.slug = %(launch)s or c.access_rule = 'free')
),
lessons as (
  select l.tenant_id, l.course_id, l.position,
         min(l.position) over (partition by l.course_id) as first_position,
         (p.lesson_id is not null) as done
  from academy_lessons l
  join courses c on c.tenant_id = l.tenant_id and c.course_id = l.course_id
  left join academy_progress p
    on p.tenant_id = l.tenant_id and p.lesson_id = l.lesson_id and p.telegram_user_id = %(user_id)s
  where l.tenant_id = %(tenant_id)s and l.status = 'published'
)
select c.slug, c.title, c.access_rule, c.has_access,
       count(l.position)::int as lessons_total,
       (count(l.position) filter (where l.done))::int as lessons_done,
       coalesce(bool_or(l.done and l.position = l.first_position), false) as first_done
from courses c
left join lessons l on l.tenant_id = c.tenant_id and l.course_id = c.course_id
group by c.slug, c.title, c.access_rule, c.has_access
"""


@dataclass
class CabinetFacts:
    marks: dict[str, Any]
    requests: list[dict[str, Any]]
    courses: list[dict[str, Any]]
    leader_product: bool
    counts: dict[str, int]
    account: dict[str, Any] | None
    crm_lock: str | None
    crm_contacts: int | None
    crm_today: int | None


async def _crm_facts(tenant_id: str, person: CabinetPerson, *, crm_entitled: bool) -> tuple[str | None, int | None, int | None]:
    """(замок, людей в CRM, дел на сегодня). Закрыта — (причина, None, None)."""
    from app.cabinet_crm import crm_contacts_count
    from app.crm.service import crm_feature_enabled, get_or_create_account, lock_reason, today_view, viewer_from_row

    if not crm_entitled or not crm_feature_enabled():
        return "feature_disabled", None, None
    viewer = viewer_from_row(
        person.telegram_user_id, person.ref_code, person.public_profile, (person.subscription or {}).get("paid_until")
    )
    reason = lock_reason(viewer)
    if reason is not None:
        return reason, None, None
    try:
        account = await get_or_create_account(tenant_id, person.telegram_user_id)
        view = await today_view(tenant_id, account)
        today = sum(len(group.get("contacts") or []) for group in view.get("groups") or [])
        contacts = await crm_contacts_count(tenant_id, person.telegram_user_id)
    except Exception as exc:  # счётчик не должен ронять кабинет
        logger.warning("cabinet_crm_counters_failed", extra=safe_error(exc))
        return None, 0, None
    return None, contacts, today


async def collect_facts(tenant_id: str, person: CabinetPerson, *, crm_entitled: bool) -> CabinetFacts:
    async with tenant_connection(tenant_id) as conn:
        marks = await fetch_all(
            conn,
            "select step, done_at from partner_journey_marks where tenant_id = %s and telegram_user_id = %s",
            (tenant_id, person.telegram_user_id),
        )
        requests: list[dict[str, Any]] = []
        leader_product = False
        if person.ref_code:
            requests = await fetch_all(
                conn,
                """
                select request_id::text as request_id, status, changes, previous, reject_reason,
                       created_at, reviewed_at
                from partner_profile_requests
                where tenant_id = %s and ref_code = %s
                order by created_at desc
                limit %s
                """,
                (tenant_id, person.ref_code, REQUESTS_SHOWN),
            )
            leader = await fetch_one(
                conn,
                """
                select exists (
                  select 1 from partner_product_access
                  where tenant_id = %s and ref_code = %s and product_code = 'leader_cabinet'
                    and (paid_until is null or paid_until > now())
                ) as active
                """,
                (tenant_id, person.ref_code),
            )
            leader_product = bool((leader or {}).get("active"))
        courses = await fetch_all(
            conn,
            _ACADEMY_SQL,
            {"tenant_id": tenant_id, "user_id": person.telegram_user_id, "launch": LAUNCH_COURSE_SLUG},
        )
    counts = await referral_counts(tenant_id, person.actor_id)
    account = await load_site_account(tenant_id, person.telegram_user_id)
    crm_lock, crm_contacts, crm_today = await _crm_facts(tenant_id, person, crm_entitled=crm_entitled)
    return CabinetFacts(
        marks={str(row["step"]): row["done_at"] for row in marks},
        requests=[dict(row) for row in requests],
        courses=[dict(row) for row in courses],
        leader_product=leader_product,
        counts=counts,
        account=account,
        crm_lock=crm_lock,
        crm_contacts=crm_contacts,
        crm_today=crm_today,
    )


def leader_right(person: CabinetPerson, facts: CabinetFacts) -> bool:
    return facts.leader_product or person.telegram_user_id in get_settings().parsed_leader_pilot()


def club_active(facts: CabinetFacts) -> bool:
    club = (facts.account or {}).get("club") or {}
    return str(club.get("status") or "") in {"active", "grace"}


def _academy_viewer(person: CabinetPerson) -> AcademyViewer:
    return AcademyViewer(
        telegram_user_id=person.telegram_user_id,
        is_preview_admin=person.telegram_user_id in preview_admin_ids(),
        partner_paid=person.partner_paid,
    )


def academy_block(person: CabinetPerson, facts: CabinetFacts) -> dict[str, Any] | None:
    """Прогресс «Запуск WWC» для карточки Академии на Главной."""
    course = next((row for row in facts.courses if row["slug"] == LAUNCH_COURSE_SLUG), None)
    if course is None:
        return None
    total = int(course["lessons_total"] or 0)
    done = int(course["lessons_done"] or 0)
    lock = course_lock_reason(course, _academy_viewer(person), has_access_row=bool(course.get("has_access")))
    return {
        "course": LAUNCH_COURSE_SLUG,
        "title": course["title"],
        "lessons_total": total,
        "lessons_done": done,
        "percent": round(100 * done / total) if total else 0,
        "locked": lock is not None,
        "lock_reason": lock,
        "url": f"/academy/?course={LAUNCH_COURSE_SLUG}",
    }


def journey_facts(person: CabinetPerson, facts: CabinetFacts, tier: str) -> JourneyFacts:
    viewer = _academy_viewer(person)
    visible = viewer.is_preview_admin or academy_open()
    launch = next((row for row in facts.courses if row["slug"] == LAUNCH_COURSE_SLUG), None)
    free = [row for row in facts.courses if row["access_rule"] == "free" and int(row["lessons_total"] or 0) > 0]
    current = current_profile_fields(person.public_profile)
    return JourneyFacts(
        tier=tier,
        marks=frozenset(facts.marks),
        profile_complete=profile_complete(current),
        lesson1_done=bool(launch and launch["first_done"]),
        invited_count=int(facts.counts.get("invited_count") or 0),
        crm_contacts=facts.crm_contacts,
        crm_lock=facts.crm_lock,
        club_active=club_active(facts),
        has_site=bool(person.ref_code),
        free_lesson_available=visible and bool(free),
        free_lesson_done=any(int(row["lessons_done"] or 0) > 0 for row in free),
        academy_lock=None if visible else "academy_not_open",
    )


def request_out(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "request_id": str(row["request_id"]),
        "status": row["status"],
        "changes": row.get("changes") or {},
        "previous": row.get("previous") or {},
        "reject_reason": row.get("reject_reason"),
        "created_at": _iso(row.get("created_at")),
        "reviewed_at": _iso(row.get("reviewed_at")),
    }


PROFILE_LIMITS = {
    "display_name_min": DISPLAY_NAME_MIN,
    "display_name_max": DISPLAY_NAME_MAX,
    "bio_max": BIO_MAX,
    "address_max": ADDRESS_MAX,
}


def profile_block(person: CabinetPerson, facts: CabinetFacts) -> dict[str, Any] | None:
    if not person.ref_code:
        return None
    pending = next((row for row in facts.requests if row["status"] == "pending"), None)
    review = next((row for row in facts.requests if row["status"] in ("applied", "rejected")), None)
    return {
        "ref_code": person.ref_code,
        "current": current_profile_fields(person.public_profile),
        "pending": request_out(pending),
        "last_review": request_out(review),
        "limits": PROFILE_LIMITS,
    }


def account_out(account: dict[str, Any] | None) -> dict[str, Any] | None:
    """Как в /me: даты ISO."""
    if not account:
        return None
    return {
        **account,
        "pro": {**account["pro"], "paid_until": _iso(account["pro"]["paid_until"])},
        "club": {**account["club"], "paid_until": _iso(account["club"]["paid_until"])},
    }


async def settings_block(tenant_id: str, person: CabinetPerson) -> dict[str, Any]:
    from app.cabinet_crm import account_timezone
    from app.crm.service import crm_feature_enabled
    from app.telegram.consent import marketing_consent_state

    tz = None
    if crm_feature_enabled():
        try:
            tz = await account_timezone(tenant_id, person.telegram_user_id)
        except Exception as exc:
            logger.warning("cabinet_timezone_read_failed", extra=safe_error(exc))
    try:
        opted_in = await marketing_consent_state(tenant_id, person.telegram_user_id)
    except Exception as exc:
        logger.warning("cabinet_consent_read_failed", extra=safe_error(exc))
        opted_in = None
    return {"timezone": tz, "timezone_default": "Europe/Moscow", "marketing_opt_in": opted_in}


async def invite_block(tenant_id: str, person: CabinetPerson, *, bot_username: str | None) -> dict[str, Any]:
    from app.telegram.referral_bonus import invitation_text

    name = str(bot_username or "").lstrip("@").strip()
    if not name:
        return {"link": None, "text": None}
    code = await get_or_create_invite_code(tenant_id, person.actor_id)
    link = f"https://t.me/{name}?start=ref_{code}"
    return {"link": link, "text": invitation_text(link)}


async def load_overview(
    tenant_id: str, person: CabinetPerson, *, bot_username: str | None, crm_entitled: bool
) -> dict[str, Any]:
    facts = await collect_facts(tenant_id, person, crm_entitled=crm_entitled)
    tier = cabinet_tier(partner_paid=person.partner_paid, leader_right=leader_right(person, facts))
    journey = journey_steps(journey_facts(person, facts, tier))
    academy = academy_block(person, facts)
    is_club = club_active(facts)
    viewer = _academy_viewer(person)
    locks = cabinet_locks(
        tier=tier,
        has_site=bool(person.ref_code),
        club_active=is_club,
        crm_lock=facts.crm_lock,
        academy_lock=None if (viewer.is_preview_admin or academy_open()) else "academy_not_open",
    )
    return {
        "ok": True,
        "tier": tier,
        "locks": locks,
        "person": {"display_name": person.greeting_name, "telegram_username": person.telegram_username},
        "account": account_out(facts.account),
        "site": site_block(person, bot_username=bot_username),
        "profile": profile_block(person, facts),
        "journey": journey,
        "counters": {
            "invited": int(facts.counts.get("invited_count") or 0),
            "paid": int(facts.counts.get("paid_count") or 0),
            "crm_today": facts.crm_today,
            "crm_contacts": facts.crm_contacts,
            "academy": academy,
        },
        "invite": await invite_block(tenant_id, person, bot_username=bot_username),
        "links": {
            "cabinet": "/me/",
            "academy": "/academy/",
            "crm": "/crm/",
            "start": "/start/",
            "club": "/club/",
            "faq": "/otvety/",
            "bot": bot_link(bot_username),
            "support": bot_link(bot_username, "support"),
            "renew": bot_link(bot_username, "renew"),
            "club_group": get_settings().platform_club_group_url if is_club else None,
            "news_channel": NEWS_CHANNEL_URL,
        },
        "settings": await settings_block(tenant_id, person),
    }


async def load_journey(tenant_id: str, person: CabinetPerson, *, crm_entitled: bool) -> dict[str, Any]:
    facts = await collect_facts(tenant_id, person, crm_entitled=crm_entitled)
    tier = cabinet_tier(partner_paid=person.partner_paid, leader_right=leader_right(person, facts))
    return {"ok": True, "tier": tier, "journey": journey_steps(journey_facts(person, facts, tier))}


async def mark_journey_step(tenant_id: str, person: CabinetPerson, step: str) -> dict[str, Any]:
    if step not in JOURNEY_STEPS_MANUAL:
        raise CabinetError("step_not_manual", 400)
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            insert into partner_journey_marks (tenant_id, telegram_user_id, step)
            values (%s, %s, %s)
            on conflict (tenant_id, telegram_user_id, step) do update
              set done_at = partner_journey_marks.done_at
            returning done_at
            """,
            (tenant_id, person.telegram_user_id, step),
        )
    return {"ok": True, "step": step, "done": True, "done_at": _iso((row or {}).get("done_at"))}


# ---- заявка на изменение профиля ----------------------------------------------------


def _require_site(person: CabinetPerson) -> str:
    """Профиль сайта — у PRO: сайт есть и оплачен (активен или льготный срок)."""
    if not person.ref_code or not person.partner_paid:
        raise CabinetError("pro_required", 402)
    return person.ref_code


async def _ref_lock(conn: Any, tenant_id: str, ref_code: str) -> None:
    await fetch_one(
        conn, "select pg_advisory_xact_lock(hashtext(%s)) as locked", (f"cabinet_profile:{tenant_id}:{ref_code}",)
    )


async def _photo_exists(conn: Any, tenant_id: str, ref_code: str, media_id: str) -> bool:
    row = await fetch_one(
        conn,
        "select 1 as ok from partner_media where tenant_id = %s and media_id = %s::uuid and ref_code = %s",
        (tenant_id, media_id, ref_code),
    )
    return row is not None


async def _prune_media(conn: Any, tenant_id: str, ref_code: str, keep: set[str]) -> None:
    """Фото этого сайта, на которые больше ничто не ссылается, старше суток."""
    await fetch_one(
        conn,
        """
        with gone as (
          delete from partner_media
          where tenant_id = %s and ref_code = %s
            and created_at < now() - interval '1 day'
            and not (media_id::text = any(%s::text[]))
          returning 1
        )
        select count(*)::int as removed from gone
        """,
        (tenant_id, ref_code, sorted(keep)),
    )


def _referenced_media(base: str, *photo_urls: Any) -> set[str]:
    # Любой origin: удалить фото, которое показывает сайт, хуже, чем оставить лишнее.
    return {media for media in (media_id_in_url(url) for url in photo_urls) if media}


async def submit_profile_request(tenant_id: str, person: CabinetPerson, body: dict[str, Any]) -> dict[str, Any]:
    """Новая заявка: {"pending": заявка | None, "status": "pending" | "no_changes",
    "replaced_request_id": прежняя pending | None} — у прежней владелец больше не жмёт кнопки."""
    ref_code = _require_site(person)
    wanted = normalize_profile_input(body)
    base = get_settings().platform_partner_media_public_base
    async with tenant_connection(tenant_id) as conn:
        await _ref_lock(conn, tenant_id, ref_code)
        site = await fetch_one(
            conn,
            "select public_profile from referral_profiles where tenant_id = %s and ref_code = %s and enabled = true",
            (tenant_id, ref_code),
        )
        if site is None:
            raise CabinetError("site_not_found", 404)
        current = current_profile_fields(site.get("public_profile"))
        pending = await fetch_one(
            conn,
            """
            select request_id::text as request_id, changes
            from partner_profile_requests
            where tenant_id = %s and ref_code = %s and status = 'pending'
            for update
            """,
            (tenant_id, ref_code),
        )
        desired = overlay_changes((pending or {}).get("changes") or {}, wanted)
        photo = desired.get("photo_url")
        if photo and photo != current.get("photo_url"):
            media_id = media_id_from_url(photo, base)
            if media_id is None or not await _photo_exists(conn, tenant_id, ref_code, media_id):
                raise ProfileValidationError("invalid_photo", "photo_url")
        changes, previous = diff_profile(current, desired)
        if pending:
            await fetch_one(
                conn,
                """
                update partner_profile_requests
                set status = %s, updated_at = now()
                where tenant_id = %s and request_id = %s::uuid and status = 'pending'
                returning request_id
                """,
                ("replaced" if changes else "cancelled", tenant_id, pending["request_id"]),
            )
        replaced_id = pending["request_id"] if pending else None
        if not changes:
            if not pending:
                raise CabinetError("no_changes", 400)
            await _prune_media(conn, tenant_id, ref_code, _referenced_media(base, current.get("photo_url")))
            return {"pending": None, "status": "no_changes", "replaced_request_id": replaced_id}
        recent = await fetch_one(
            conn,
            """
            select count(*)::int as requests from partner_profile_requests
            where tenant_id = %s and ref_code = %s and created_at > now() - interval '1 hour'
            """,
            (tenant_id, ref_code),
        )
        if int((recent or {}).get("requests") or 0) >= REQUESTS_PER_HOUR:
            raise CabinetError("too_many_requests", 429)
        row = await fetch_one(
            conn,
            """
            insert into partner_profile_requests (
              tenant_id, ref_code, telegram_user_id, changes, previous
            ) values (%s, %s, %s, %s::jsonb, %s::jsonb)
            returning request_id::text as request_id, status, changes, previous, reject_reason,
                      created_at, reviewed_at
            """,
            (tenant_id, ref_code, person.telegram_user_id, _json(changes), _json(previous)),
        )
        await _prune_media(
            conn, tenant_id, ref_code, _referenced_media(base, current.get("photo_url"), changes.get("photo_url"))
        )
    logger.info("cabinet_profile_request_created", extra={"request_id": row["request_id"], "ref_code": ref_code})
    return {"pending": dict(row), "status": "pending", "replaced": bool(pending), "replaced_request_id": replaced_id}


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


async def get_pending_request(tenant_id: str, person: CabinetPerson) -> dict[str, Any]:
    if not person.ref_code:
        return {"pending": None, "last_review": None}
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select request_id::text as request_id, status, changes, previous, reject_reason,
                   created_at, reviewed_at
            from partner_profile_requests
            where tenant_id = %s and ref_code = %s
            order by created_at desc
            limit %s
            """,
            (tenant_id, person.ref_code, REQUESTS_SHOWN),
        )
    pending = next((row for row in rows if row["status"] == "pending"), None)
    review = next((row for row in rows if row["status"] in ("applied", "rejected")), None)
    return {"pending": request_out(pending), "last_review": request_out(review)}


async def cancel_pending_request(tenant_id: str, person: CabinetPerson) -> str | None:
    """Партнёр отозвал заявку: id отозванной или None, если отзывать нечего."""
    if not person.ref_code:
        return None
    base = get_settings().platform_partner_media_public_base
    async with tenant_connection(tenant_id) as conn:
        await _ref_lock(conn, tenant_id, person.ref_code)
        row = await fetch_one(
            conn,
            """
            update partner_profile_requests
            set status = 'cancelled', updated_at = now()
            where tenant_id = %s and ref_code = %s and status = 'pending'
            returning request_id::text as request_id
            """,
            (tenant_id, person.ref_code),
        )
        if row:
            site = await fetch_one(
                conn,
                "select public_profile from referral_profiles where tenant_id = %s and ref_code = %s",
                (tenant_id, person.ref_code),
            )
            current = current_profile_fields((site or {}).get("public_profile"))
            await _prune_media(conn, tenant_id, person.ref_code, _referenced_media(base, current.get("photo_url")))
    return str(row["request_id"]) if row else None


# ---- модерация (бот владельца) ---------------------------------------------------------


def _request_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise CabinetError("request_not_found", 404) from exc


_CARD_SQL = """
select r.request_id::text as request_id, r.ref_code, r.telegram_user_id, r.changes, r.previous,
       r.status, r.reject_reason, r.created_at, r.reviewed_at,
       rp.public_profile, la.display_name as actor_name, la.telegram_chat_id
from partner_profile_requests r
left join referral_profiles rp on rp.tenant_id = r.tenant_id and rp.ref_code = r.ref_code
left join lead_actors la on la.tenant_id = rp.tenant_id and la.actor_id = rp.owner_id
where r.tenant_id = %s and r.request_id = %s::uuid
"""


async def load_request_card(tenant_id: str, request_id: str) -> dict[str, Any] | None:
    """Заявка и что нужно для сообщений: имя партнёра, сайт, чат."""
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(conn, _CARD_SQL, (tenant_id, _request_uuid(request_id)))
    return dict(row) if row else None


async def load_owner_card(tenant_id: str, request_id: str) -> dict[str, Any] | None:
    """Где у владельца карточка заявки (чтобы снять кнопки, когда она устарела)."""
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select owner_chat_id, owner_message_id from partner_profile_requests
            where tenant_id = %s and request_id = %s::uuid
            """,
            (tenant_id, _request_uuid(request_id)),
        )
    if not row or row.get("owner_chat_id") is None or row.get("owner_message_id") is None:
        return None
    return {"chat_id": int(row["owner_chat_id"]), "message_id": int(row["owner_message_id"])}


async def load_media_body(tenant_id: str, media_id: str) -> bytes | None:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            "select body from partner_media where tenant_id = %s and media_id = %s::uuid",
            (tenant_id, media_id),
        )
    return bytes(row["body"]) if row else None


async def list_pending_requests(tenant_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select request_id::text as request_id
            from partner_profile_requests
            where tenant_id = %s and status = 'pending'
            order by created_at
            limit %s
            """,
            (tenant_id, max(1, min(int(limit), 30))),
        )
    return [dict(row) for row in rows]


async def record_owner_card(tenant_id: str, request_id: str, *, chat_id: int, message_id: int | None) -> None:
    async with tenant_connection(tenant_id) as conn:
        await fetch_one(
            conn,
            """
            update partner_profile_requests
            set owner_chat_id = %s, owner_message_id = %s, updated_at = now()
            where tenant_id = %s and request_id = %s::uuid
            returning request_id
            """,
            (int(chat_id), message_id, tenant_id, _request_uuid(request_id)),
        )


async def apply_profile_request(tenant_id: str, request_id: str, *, reviewer_id: int) -> dict[str, Any]:
    """«Применить»: изменения из заявки → referral_profiles.public_profile, profile_version + 1.

    Повторное нажатие и кнопки под старой (заменённой) заявкой ничего не меняют:
    возвращается {"applied": False, "status": …}.
    """
    request_uuid = _request_uuid(request_id)
    base = get_settings().platform_partner_media_public_base
    async with tenant_connection(tenant_id) as conn:
        head = await fetch_one(
            conn,
            "select ref_code from partner_profile_requests where tenant_id = %s and request_id = %s::uuid",
            (tenant_id, request_uuid),
        )
        if head is None:
            raise CabinetError("request_not_found", 404)
        await _ref_lock(conn, tenant_id, str(head["ref_code"]))
        request = await fetch_one(
            conn,
            """
            select request_id::text as request_id, ref_code, telegram_user_id, changes, status
            from partner_profile_requests
            where tenant_id = %s and request_id = %s::uuid
            for update
            """,
            (tenant_id, request_uuid),
        )
        if request["status"] != "pending":
            return {"applied": False, "status": request["status"], "request": dict(request)}
        site = await fetch_one(
            conn,
            """
            select public_profile from referral_profiles
            where tenant_id = %s and ref_code = %s
            for update
            """,
            (tenant_id, request["ref_code"]),
        )
        if site is None:
            raise CabinetError("site_not_found", 404)
        profile = apply_profile_changes(site.get("public_profile"), request["changes"] or {})
        updated = await fetch_one(
            conn,
            """
            update referral_profiles
            set public_profile = %s::jsonb, profile_version = profile_version + 1, updated_at = now()
            where tenant_id = %s and ref_code = %s
            returning profile_version
            """,
            (_json(profile), tenant_id, request["ref_code"]),
        )
        await fetch_one(
            conn,
            """
            update partner_profile_requests
            set status = 'applied', reviewed_at = now(), reviewed_by = %s, updated_at = now()
            where tenant_id = %s and request_id = %s::uuid
            returning request_id
            """,
            (int(reviewer_id), tenant_id, request_uuid),
        )
        await _prune_media(conn, tenant_id, request["ref_code"], _referenced_media(base, profile.get("photo_url")))
    logger.info("cabinet_profile_request_applied", extra={"request_id": request_uuid, "ref_code": request["ref_code"]})
    return {
        "applied": True,
        "status": "applied",
        "request": dict(request),
        "profile_version": int((updated or {}).get("profile_version") or 0),
    }


async def mark_reason_asked(tenant_id: str, request_id: str, *, prompt_message_id: int | None) -> dict[str, Any]:
    """Отклонение — сначала вопрос «причина?»; заявка остаётся pending до ответа."""
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update partner_profile_requests
            set reason_prompt_message_id = coalesce(%s, reason_prompt_message_id), updated_at = now()
            where tenant_id = %s and request_id = %s::uuid
            returning request_id::text as request_id, ref_code, status
            """,
            (prompt_message_id, tenant_id, _request_uuid(request_id)),
        )
    if row is None:
        raise CabinetError("request_not_found", 404)
    return dict(row)


async def request_status(tenant_id: str, request_id: str) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select request_id::text as request_id, ref_code, status
            from partner_profile_requests where tenant_id = %s and request_id = %s::uuid
            """,
            (tenant_id, _request_uuid(request_id)),
        )
    return dict(row) if row else None


async def find_pending_by_short_id(tenant_id: str, short_id: str) -> dict[str, Any] | None:
    """Заявка по «№1a2b3c4d» из вопроса о причине (первые 8 знаков id)."""
    short = str(short_id or "").strip().lower()
    if len(short) != 8 or any(ch not in "0123456789abcdef" for ch in short):
        return None
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select request_id::text as request_id, ref_code, status
            from partner_profile_requests
            where tenant_id = %s and left(request_id::text, 8) = %s
            order by created_at desc
            limit 2
            """,
            (tenant_id, short),
        )
    return dict(rows[0]) if len(rows) == 1 else None


async def reject_profile_request(
    tenant_id: str, request_id: str, *, reviewer_id: int, reason: str | None
) -> dict[str, Any]:
    request_uuid = _request_uuid(request_id)
    base = get_settings().platform_partner_media_public_base
    async with tenant_connection(tenant_id) as conn:
        head = await fetch_one(
            conn,
            "select ref_code from partner_profile_requests where tenant_id = %s and request_id = %s::uuid",
            (tenant_id, request_uuid),
        )
        if head is None:
            raise CabinetError("request_not_found", 404)
        await _ref_lock(conn, tenant_id, str(head["ref_code"]))
        request = await fetch_one(
            conn,
            """
            select request_id::text as request_id, ref_code, telegram_user_id, changes, status
            from partner_profile_requests
            where tenant_id = %s and request_id = %s::uuid
            for update
            """,
            (tenant_id, request_uuid),
        )
        if request["status"] != "pending":
            return {"rejected": False, "status": request["status"], "request": dict(request)}
        await fetch_one(
            conn,
            """
            update partner_profile_requests
            set status = 'rejected', reject_reason = %s, reviewed_at = now(), reviewed_by = %s,
                updated_at = now()
            where tenant_id = %s and request_id = %s::uuid
            returning request_id
            """,
            (reason[:500] if reason else None, int(reviewer_id), tenant_id, request_uuid),
        )
        site = await fetch_one(
            conn,
            "select public_profile from referral_profiles where tenant_id = %s and ref_code = %s",
            (tenant_id, request["ref_code"]),
        )
        current = current_profile_fields((site or {}).get("public_profile"))
        await _prune_media(conn, tenant_id, request["ref_code"], _referenced_media(base, current.get("photo_url")))
    logger.info("cabinet_profile_request_rejected", extra={"request_id": request_uuid, "ref_code": request["ref_code"]})
    return {"rejected": True, "status": "rejected", "request": dict(request), "reason": reason}


# ---- фото -----------------------------------------------------------------------------


async def store_profile_photo(tenant_id: str, person: CabinetPerson, photo: ProcessedPhoto) -> dict[str, Any]:
    ref_code = _require_site(person)
    async with tenant_connection(tenant_id) as conn:
        recent = await fetch_one(
            conn,
            """
            select count(*)::int as uploads from partner_media
            where tenant_id = %s and telegram_user_id = %s and created_at > now() - interval '1 day'
            """,
            (tenant_id, person.telegram_user_id),
        )
        if int((recent or {}).get("uploads") or 0) >= UPLOADS_PER_DAY:
            raise CabinetError("too_many_uploads", 429)
        row = await fetch_one(
            conn,
            """
            insert into partner_media (
              tenant_id, ref_code, telegram_user_id, content_type, body, width, height, sha256
            ) values (%s, %s, %s, 'image/jpeg', %s, %s, %s, %s)
            returning media_id::text as media_id
            """,
            (tenant_id, ref_code, person.telegram_user_id, photo.body, photo.width, photo.height, photo.sha256),
        )
    media_id = str(row["media_id"])
    return {
        "media_id": media_id,
        "photo_url": media_public_url(get_settings().platform_partner_media_public_base, media_id),
        "width": photo.width,
        "height": photo.height,
        "size_bytes": len(photo.body),
    }


async def load_public_media(tenant_id: str, media_id: str) -> bytes | None:
    try:
        media_uuid = str(uuid.UUID(str(media_id)))
    except (TypeError, ValueError):
        return None
    return await load_media_body(tenant_id, media_uuid)


# ---- настройки ---------------------------------------------------------------------------


async def update_settings(
    tenant_id: str,
    person: CabinetPerson,
    *,
    timezone_name: str | None = None,
    marketing_opt_in: bool | None = None,
) -> dict[str, Any]:
    """Таймзона (общая с WWC CRM, platform_accounts) и согласие на рассылку (как /news_on)."""
    if timezone_name is not None:
        from app.crm.service import CrmError, crm_feature_enabled, get_or_create_account, set_timezone

        if not crm_feature_enabled():
            raise CabinetError("feature_disabled", 409)
        try:
            account = await get_or_create_account(tenant_id, person.telegram_user_id)
            await set_timezone(tenant_id, account, timezone_name)
        except CrmError as exc:
            raise CabinetError(exc.code, exc.status) from exc
    if marketing_opt_in is not None:
        from app.telegram.consent import record_marketing_consent

        await record_marketing_consent(
            tenant_id,
            telegram_user_id=person.telegram_user_id,
            telegram_chat_id=person.telegram_user_id,
            opted_in=bool(marketing_opt_in),
            source="site",
        )
    return await settings_block(tenant_id, person)


# ---- баланс ------------------------------------------------------------------------------


def _percent(bps: Any) -> int | float | None:
    if bps is None:
        return None
    value = int(bps) / 100
    return int(value) if value.is_integer() else value


async def bonus_balance_and_rules(tenant_id: str, actor_id: str) -> dict[str, Any]:
    """Баланс WWC$ и правила начисления из referral_reward_rules (20 % / 10 % на 02.10)."""
    async with tenant_connection(tenant_id) as conn:
        balance = await fetch_one(
            conn,
            """
            select coalesce(sum(amount_minor), 0)::bigint as amount_minor
            from partner_bonus_ledger
            where tenant_id = %s and actor_id = %s and currency = 'WUSD'
            """,
            (tenant_id, actor_id),
        )
        rule = await fetch_one(
            conn,
            """
            select first_payment_bps, renewal_payment_bps
            from referral_reward_rules
            where tenant_id = %s and product_code = 'platform_subscription' and active = true
              and valid_from <= now() and (valid_until is null or valid_until > now())
            order by valid_from desc
            limit 1
            """,
            (tenant_id,),
        )
    return {
        "balance_minor": int((balance or {}).get("amount_minor") or 0),
        "rules": {
            "first_payment_percent": _percent((rule or {}).get("first_payment_bps")),
            "renewal_percent": _percent((rule or {}).get("renewal_payment_bps")),
            "cash_out": False,
            "spend_url": "/start/",
        },
    }
