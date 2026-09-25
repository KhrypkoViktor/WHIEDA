"""Access keys for Academy courses — the «полка» model (owner, 25.09.2026).

An author pays for a place on the shelf (``academy_shelf``); students pay the
author directly, and the author hands out keys. A key is a 12-character code
from an alphabet without look-alikes (no o/0/l/1); the student opens
``t.me/<bot>?start=course_<code>`` and the course opens on the site and in the bot.

Who issues keys: the course author while the shelf is paid and active, or the
owner / a preview admin (``preview_admin_ids``). A lapsed shelf stops new keys
(``shelf_expired``); students who already have access keep learning.

Redeeming is one transaction: the key row is locked (``for update``), then
``used_count + 1`` and the ``academy_access`` row (source ``key``) are written
together. The same person redeeming again gets «уже открыт» and spends nothing.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.db import fetch_all, fetch_one, tenant_connection

KEY_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # a-z и 2-9 без o/0/l/1
KEY_LENGTH = 12
MAX_KEYS_PER_BATCH = 100
COURSE_START_PREFIX = "course_"


class AcademyKeyError(Exception):
    """A key could not be issued or redeemed; ``code`` is machine-readable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


KEY_ERROR_TEXT = {
    "course_not_found": "Курса с таким адресом нет. Проверьте slug в «мои курсы».",
    "course_not_published": "Курс ещё не опубликован — ключи можно выдать после публикации.",
    "not_author": "Выдавать ключи к этому курсу может только его автор.",
    "shelf_expired": "Полка Академии не оплачена — новые ключи не выдаются. Ученики с доступом продолжают учиться. Продлите полку в кабинете.",
    "bad_count": f"Количество ключей — от 1 до {MAX_KEYS_PER_BATCH} за раз.",
    "key_not_found": "Ключ не найден. Проверьте ссылку или попросите у автора новую.",
    "key_revoked": "Этот ключ отозван автором. Попросите у автора новый.",
    "key_expired": "Срок действия ключа истёк. Попросите у автора новый.",
    "key_exhausted": "Этот ключ уже использован. Попросите у автора свой ключ.",
    "course_unavailable": "Курс сейчас недоступен. Напишите автору курса.",
}


def generate_key_code() -> str:
    return "".join(secrets.choice(KEY_ALPHABET) for _ in range(KEY_LENGTH))


def is_valid_key_code(code: str) -> bool:
    value = str(code or "")
    return len(value) == KEY_LENGTH and all(ch in KEY_ALPHABET for ch in value)


def parse_course_start_token(token: str) -> str | None:
    """Key code behind ``course_<code>``; "" when malformed, None when not ours."""
    raw = str(token or "").strip()
    if not raw.lower().startswith(COURSE_START_PREFIX):
        return None
    code = raw[len(COURSE_START_PREFIX):].strip().lower()
    return code if is_valid_key_code(code) else ""


def course_start_link(bot_username: str, code: str) -> str:
    return f"https://t.me/{str(bot_username or '').lstrip('@')}?start={COURSE_START_PREFIX}{code}"


@dataclass(frozen=True)
class IssuedKeys:
    course_slug: str
    course_title: str
    codes: list[str] = field(default_factory=list)
    max_uses: int = 1
    expires_at: datetime | None = None


@dataclass(frozen=True)
class RedeemResult:
    status: str  # 'opened' | 'already_open'
    course_slug: str
    course_title: str


async def shelf_active(conn: Any, tenant_id: str, actor_id: str) -> bool:
    row = await fetch_one(
        conn,
        """
        select 1 as ok from academy_shelf
        where tenant_id = %s and actor_id = %s and status = 'active'
          and paid_until is not null and paid_until > now()
        """,
        (tenant_id, actor_id),
    )
    return bool(row)


async def actor_ids_for_telegram(conn: Any, tenant_id: str, telegram_user_id: int) -> list[str]:
    """All actor rows of one Telegram person (a partner may own two rows by history)."""
    rows = await fetch_all(
        conn,
        """
        select actor_id from lead_actors
        where tenant_id = %s and telegram_user_id = %s and active = true
        order by actor_id
        """,
        (tenant_id, int(telegram_user_id)),
    )
    return [str(row["actor_id"]) for row in rows]


async def issue_keys(
    tenant_id: str,
    course_slug: str,
    by_actor_id: str,
    count: int,
    max_uses: int = 1,
    expires_at: datetime | None = None,
    *,
    as_admin: bool = False,
) -> IssuedKeys:
    """Create ``count`` keys to a published course.

    ``as_admin`` — the owner or a preview admin: no author or shelf check.
    Otherwise ``by_actor_id`` must be the course author with an active shelf."""
    count = int(count)
    if count < 1 or count > MAX_KEYS_PER_BATCH:
        raise AcademyKeyError("bad_count")
    if int(max_uses) < 1:
        raise AcademyKeyError("bad_count")
    async with tenant_connection(tenant_id) as conn:
        course = await fetch_one(
            conn,
            """
            select course_id::text as course_id, slug, title, status, author_actor_id
            from academy_courses where tenant_id = %s and slug = %s
            """,
            (tenant_id, str(course_slug or "").strip().lower()),
        )
        if not course or course["status"] == "archived":
            raise AcademyKeyError("course_not_found")
        if not as_admin:
            if not course.get("author_actor_id") or str(course["author_actor_id"]) != str(by_actor_id):
                raise AcademyKeyError("not_author")
            if not await shelf_active(conn, tenant_id, str(by_actor_id)):
                raise AcademyKeyError("shelf_expired")
        if course["status"] != "published":
            raise AcademyKeyError("course_not_published")
        codes: list[str] = []
        attempts = 0
        while len(codes) < count:
            attempts += 1
            if attempts > count * 5:  # 32^12 codes: a repeat is practically impossible
                raise RuntimeError("could not generate unique academy keys")
            row = await fetch_one(
                conn,
                """
                insert into academy_access_keys (tenant_id, course_id, code, created_by_actor_id, max_uses, expires_at)
                values (%s, %s::uuid, %s, %s, %s, %s)
                on conflict (tenant_id, code) do nothing
                returning code
                """,
                (tenant_id, course["course_id"], generate_key_code(), str(by_actor_id), int(max_uses), expires_at),
            )
            if row:
                codes.append(str(row["code"]))
    return IssuedKeys(
        course_slug=str(course["slug"]), course_title=str(course["title"]), codes=codes,
        max_uses=int(max_uses), expires_at=expires_at,
    )


async def author_actor_ids(tenant_id: str, telegram_user_id: int) -> list[str]:
    async with tenant_connection(tenant_id) as conn:
        return await actor_ids_for_telegram(conn, tenant_id, telegram_user_id)


async def issue_keys_for_telegram(
    tenant_id: str, course_slug: str, telegram_user_id: int, count: int, *, is_admin: bool
) -> IssuedKeys:
    """The bot command «ключи <slug> <N>»: the person is the author (one of their
    actor rows authored the course) or the owner / a preview admin."""
    actor_ids = await author_actor_ids(tenant_id, telegram_user_id)
    async with tenant_connection(tenant_id) as conn:
        course = await fetch_one(
            conn,
            "select author_actor_id from academy_courses where tenant_id = %s and slug = %s",
            (tenant_id, str(course_slug or "").strip().lower()),
        )
    if not course:
        raise AcademyKeyError("course_not_found")
    author = str(course.get("author_actor_id") or "")
    if author and author in actor_ids:
        by_actor_id = author
    else:
        by_actor_id = actor_ids[0] if actor_ids else f"telegram:{int(telegram_user_id)}"
    return await issue_keys(tenant_id, course_slug, by_actor_id, count, as_admin=is_admin)


async def redeem_key(tenant_id: str, code: str, telegram_user_id: int) -> RedeemResult:
    """Open the key's course for this person; one transaction, the key row locked."""
    normalized = str(code or "").strip().lower()
    if not is_valid_key_code(normalized):
        raise AcademyKeyError("key_not_found")
    async with tenant_connection(tenant_id) as conn:
        key = await fetch_one(
            conn,
            """
            select k.key_id::text as key_id, k.course_id::text as course_id, k.max_uses, k.used_count,
                   (k.revoked_at is not null) as revoked,
                   (k.expires_at is not null and k.expires_at <= now()) as expired,
                   c.slug, c.title, c.status
            from academy_access_keys k
            join academy_courses c on c.tenant_id = k.tenant_id and c.course_id = k.course_id
            where k.tenant_id = %s and k.code = %s
            for update of k
            """,
            (tenant_id, normalized),
        )
        if not key:
            raise AcademyKeyError("key_not_found")
        # Already open for this person (same key again, or bought earlier): the key
        # is not spent — it stays for the next student.
        existing = await fetch_one(
            conn,
            """
            select 1 as ok from academy_access
            where tenant_id = %s and course_id = %s::uuid and telegram_user_id = %s and revoked_at is null
            """,
            (tenant_id, key["course_id"], int(telegram_user_id)),
        )
        if existing:
            return RedeemResult("already_open", str(key["slug"]), str(key["title"]))
        if key["revoked"]:
            raise AcademyKeyError("key_revoked")
        if key["expired"]:
            raise AcademyKeyError("key_expired")
        if int(key["used_count"]) >= int(key["max_uses"]):
            raise AcademyKeyError("key_exhausted")
        if key["status"] != "published":
            raise AcademyKeyError("course_unavailable")
        async with conn.cursor() as cur:
            await cur.execute(
                "update academy_access_keys set used_count = used_count + 1 where tenant_id = %s and key_id = %s::uuid",
                (tenant_id, key["key_id"]),
            )
            await cur.execute(
                """
                insert into academy_access (tenant_id, course_id, telegram_user_id, source, payment_ref)
                values (%s, %s::uuid, %s, 'key', %s)
                on conflict (tenant_id, course_id, telegram_user_id)
                do update set revoked_at = null, source = 'key', payment_ref = excluded.payment_ref, granted_at = now()
                """,
                (tenant_id, key["course_id"], int(telegram_user_id), normalized),
            )
    return RedeemResult("opened", str(key["slug"]), str(key["title"]))


async def author_course_stats(tenant_id: str, actor_ids: list[str] | None) -> list[dict[str, Any]]:
    """«Мои курсы»: per course — status, keys issued/redeemed, students. No names.

    ``actor_ids`` None — every course of the tenant (the owner's view)."""
    async with tenant_connection(tenant_id) as conn:
        return await fetch_all(
            conn,
            """
            select c.slug, c.title, c.status, c.access_rule, c.author_actor_id,
                   coalesce(k.keys_total, 0) as keys_total,
                   coalesce(k.capacity, 0) as keys_capacity,
                   coalesce(k.used, 0) as keys_used,
                   coalesce(a.students, 0) as students
            from academy_courses c
            left join lateral (
              select count(*) as keys_total, sum(max_uses) as capacity, sum(used_count) as used
              from academy_access_keys k
              where k.tenant_id = c.tenant_id and k.course_id = c.course_id and k.revoked_at is null
            ) k on true
            left join lateral (
              select count(*) as students from academy_access a
              where a.tenant_id = c.tenant_id and a.course_id = c.course_id and a.revoked_at is null
            ) a on true
            where c.tenant_id = %s and c.status <> 'archived'
              and (%s::text[] is null or c.author_actor_id = any(%s::text[]))
            order by c.sort_order, c.title
            """,
            (tenant_id, actor_ids, actor_ids),
        )


async def shelf_paid_until(tenant_id: str, actor_ids: list[str]) -> datetime | None:
    if not actor_ids:
        return None
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select max(paid_until) as paid_until from academy_shelf
            where tenant_id = %s and actor_id = any(%s::text[]) and status = 'active'
            """,
            (tenant_id, actor_ids),
        )
    return row.get("paid_until") if row else None
