"""Коды владельца, привязки Google/Max и ожидающие личности — таблицы V34.

Все запросы — внутри ``tenant_connection`` (RLS по tenant_id). В логах нет e-mail и кодов: только
provider, identity_id, telegram_user_id и причина отказа.

Защита (ревью 15.10.2026):
  * код берётся атомарно (``update … where used_at is null returning``) — два одновременных redeem одним
    кодом не проходят оба; привязка — в той же транзакции, identity_taken откатывает захват;
  * попытки ввода кода считаются под advisory-блокировкой на личность (provider, external_id) и
    фиксируются отдельной транзакцией до проверки кода — параллельные запросы лимит не обходят;
  * Google: state и pending привязаны к браузеру, который начал вход (хеш cookie wwc_oauth или ключа
    сайта): чужую ссылку callback подсунуть нельзя (login-CSRF / привязка чужого Google к жертве).
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import psycopg
from fastapi import HTTPException

from app.academy.showcases import warm_hosts
from app.content_access.service import (
    BOT_LOGIN_FRAGMENT,
    create_bot_login,
    create_session_for_user,
    sanitize_return_to,
)
from app.cookie_domain import shared_cookie_domains
from app.db import fetch_all, fetch_one, get_pool, tenant_connection
from app.login_identities import google as google_oauth
from app.login_identities.rules import (
    BROWSER_LOGIN_TTL_MINUTES,
    LINK_ERROR_FRAGMENT,
    LINK_FRAGMENT,
    MAX_CODE_ATTEMPTS,
    REVOCABLE_PROVIDERS,
    LoginIdentityError,
    browser_key_matches,
    check_id_token_claims,
    code_expires_at,
    code_hash,
    decode_jwt_payload,
    error,
    format_code,
    generate_code,
    login_methods_payload,
    mask_email,
    new_browser_key,
    normalize_browser_key,
    normalize_code,
    normalize_provider,
    pending_expires_at,
    pkce_pair,
    sha256_hex,
    utcnow,
)
from app.settings import get_settings

logger = logging.getLogger("whieda.login_identities")

# Просроченные строки pending/кодов старше суток удаляет worker (login_cleanup_step).
PURGE_AFTER = "1 day"
_CODE_GENERATION_TRIES = 5


# ---- кто может выдавать коды ----------------------------------------------------------------------


def owner_allowed(telegram_user_id: int) -> bool:
    """Владелец (PLATFORM_BILLING_OWNER_TELEGRAM_ID) и super-admins."""
    settings = get_settings()
    owner = settings.platform_billing_owner_telegram_id
    if owner is not None and int(telegram_user_id) == int(owner):
        return True
    return int(telegram_user_id) in settings.parsed_super_admin_telegram_ids()


def _code_hash(tenant_id: str, code: str) -> str:
    """HMAC с PLATFORM_LOGIN_CODE_SECRET, иначе с PLATFORM_MEDIA_SIGNING_SECRET, иначе sha256 (разработка)."""
    settings = get_settings()
    secret = str(settings.platform_login_code_secret or settings.platform_media_signing_secret or "").strip()
    return code_hash(tenant_id, code, secret or None)


# ---- поиск человека по ref / имени / id -----------------------------------------------------------

_PARTNER_BY_REF_SQL = """
select rp.ref_code, la.telegram_user_id,
       coalesce(nullif(rp.public_profile->>'display_name', ''), la.display_name) as display_name
from referral_profiles rp
join lead_actors la on la.actor_id = rp.owner_id and la.tenant_id = rp.tenant_id
where rp.tenant_id = %s and rp.ref_code = %s and la.telegram_user_id is not null
limit 1
"""

_PARTNER_BY_NAME_SQL = """
select rp.ref_code, la.telegram_user_id,
       coalesce(nullif(rp.public_profile->>'display_name', ''), la.display_name) as display_name
from referral_profiles rp
join lead_actors la on la.actor_id = rp.owner_id and la.tenant_id = rp.tenant_id
where rp.tenant_id = %s and la.telegram_user_id is not null
  and position(lower(%s) in lower(coalesce(nullif(rp.public_profile->>'display_name', ''), la.display_name))) > 0
order by rp.ref_code
limit 10
"""

_ACTOR_BY_NAME_SQL = """
select null::text as ref_code, la.telegram_user_id, la.display_name
from lead_actors la
where la.tenant_id = %s and la.telegram_user_id is not null and la.active = true
  and position(lower(%s) in lower(la.display_name)) > 0
order by la.display_name
limit 10
"""

_ACTOR_BY_TELEGRAM_SQL = """
select (
         select rp.ref_code from referral_profiles rp
          where rp.tenant_id = la.tenant_id and rp.owner_id = la.actor_id and rp.enabled = true
          order by rp.ref_code limit 1
       ) as ref_code,
       la.telegram_user_id, la.display_name
from lead_actors la
where la.tenant_id = %s and la.telegram_user_id = %s
limit 1
"""


async def find_people(conn: Any, tenant_id: str, target: str) -> list[dict[str, Any]]:
    """Кандидаты: точный ref → id Telegram → имя профиля сайта → имя в lead_actors."""
    value = str(target or "").strip()
    if not value:
        return []
    row = await fetch_one(conn, _PARTNER_BY_REF_SQL, (tenant_id, value.lower()))
    if row:
        return [dict(row)]
    if value.isdigit():
        row = await fetch_one(conn, _ACTOR_BY_TELEGRAM_SQL, (tenant_id, int(value)))
        if row:
            return [dict(row)]
    rows = await fetch_all(conn, _PARTNER_BY_NAME_SQL, (tenant_id, value))
    if rows:
        return [dict(r) for r in rows]
    rows = await fetch_all(conn, _ACTOR_BY_NAME_SQL, (tenant_id, value))
    return [dict(r) for r in rows]


def person_title(person: dict[str, Any]) -> str:
    name = str(person.get("display_name") or "").strip() or f"Telegram {person.get('telegram_user_id')}"
    ref = str(person.get("ref_code") or "").strip()
    return f"{name} ({ref})" if ref else name


async def resolve_person(conn: Any, tenant_id: str, target: str) -> dict[str, Any]:
    people = await find_people(conn, tenant_id, target)
    if not people:
        raise error("partner_not_found", target=target)
    if len(people) > 1:
        raise error("partner_ambiguous", target=target, candidates=people)
    return people[0]


# ---- коды владельца -------------------------------------------------------------------------------


async def issue_code(tenant_id: str, *, issued_by: int, target: str) -> dict[str, Any]:
    """Код входа партнёру: 6 цифр, 24 часа, один раз; прежний активный код гасится.

    Возвращает {code, code_display, person, expires_at}; код показывается владельцу один раз
    и в логе не появляется. Совпадение с чужим активным кодом — новая генерация (до 5 раз), иначе
    ``code_unavailable``.
    """
    if not owner_allowed(issued_by):
        raise error("forbidden")
    async with tenant_connection(tenant_id) as conn:
        person = await resolve_person(conn, tenant_id, target)
        telegram_user_id = int(person["telegram_user_id"])
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update login_link_codes set revoked_at = now()
                where tenant_id = %s and telegram_user_id = %s and used_at is null and revoked_at is null
                """,
                (tenant_id, telegram_user_id),
            )
        code = ""
        for _ in range(_CODE_GENERATION_TRIES):
            candidate = generate_code()
            clash = await fetch_one(
                conn,
                """
                select code_id from login_link_codes
                where tenant_id = %s and code_hash = %s and used_at is null and revoked_at is null
                limit 1
                """,
                (tenant_id, _code_hash(tenant_id, candidate)),
            )
            if not clash:
                code = candidate
                break
        if not code:
            raise error("code_unavailable")
        expires_at = code_expires_at()
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    insert into login_link_codes (tenant_id, code_hash, telegram_user_id, issued_by, expires_at)
                    values (%s, %s, %s, %s, %s)
                    """,
                    (tenant_id, _code_hash(tenant_id, code), telegram_user_id, int(issued_by), expires_at),
                )
        except psycopg.errors.UniqueViolation as exc:
            # Одновременная выдача тому же человеку или тот же код у соседа — транзакция откатится.
            raise error("code_unavailable") from exc
    logger.info(
        "login_code_issued",
        extra={"tenant_id": tenant_id, "telegram_user_id": telegram_user_id, "issued_by": int(issued_by)},
    )
    return {"code": code, "code_display": format_code(code), "person": person, "expires_at": expires_at}


async def _claim_code(conn: Any, tenant_id: str, code: str, provider: str) -> dict[str, Any]:
    """Атомарно забрать код: только активный (частичный индекс), иначе причина отказа.

    LoginIdentityError: code_invalid | code_used | code_expired.
    """
    hashed = _code_hash(tenant_id, code)
    row = await fetch_one(
        conn,
        """
        update login_link_codes set used_at = now(), used_provider = %s
        where tenant_id = %s and code_hash = %s and used_at is null and revoked_at is null
          and expires_at > now()
        returning code_id, telegram_user_id, issued_by
        """,
        (provider, tenant_id, hashed),
    )
    if row:
        return row
    state = await fetch_one(
        conn,
        """
        select used_at, expires_at from login_link_codes
        where tenant_id = %s and code_hash = %s and revoked_at is null
        order by created_at desc
        limit 1
        """,
        (tenant_id, hashed),
    )
    if not state:
        raise error("code_invalid")
    if state.get("used_at"):
        raise error("code_used")
    raise error("code_expired")


async def _active_identity(conn: Any, tenant_id: str, provider: str, external_id: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select identity_id, telegram_user_id, email, display_name, linked_at
        from account_identities
        where tenant_id = %s and provider = %s and external_id = %s and revoked_at is null
        limit 1
        """,
        (tenant_id, provider, str(external_id)),
    )


async def _any_identity_exists(conn: Any, tenant_id: str, provider: str, external_id: str) -> bool:
    row = await fetch_one(
        conn,
        "select 1 as present from account_identities where tenant_id = %s and provider = %s and external_id = %s limit 1",
        (tenant_id, provider, str(external_id)),
    )
    return bool(row)


async def _insert_identity(
    conn: Any,
    tenant_id: str,
    *,
    telegram_user_id: int,
    provider: str,
    external_id: str,
    email: str | None,
    display_name: str | None,
    linked_by: str,
) -> str:
    try:
        row = await fetch_one(
            conn,
            """
            insert into account_identities
              (tenant_id, telegram_user_id, provider, external_id, email, display_name, linked_by)
            values (%s, %s, %s, %s, %s, %s, %s)
            returning identity_id
            """,
            (tenant_id, int(telegram_user_id), provider, str(external_id), email, display_name, linked_by),
        )
    except psycopg.errors.UniqueViolation as exc:
        # Параллельная привязка той же личности другим кодом: частичный уникальный индекс не пустил.
        raise error("identity_taken") from exc
    identity_id = str(row["identity_id"])
    logger.info(
        "login_identity_linked",
        extra={
            "tenant_id": tenant_id,
            "provider": provider,
            "identity_id": identity_id,
            "telegram_user_id": int(telegram_user_id),
            "linked_by": linked_by.split(":", 1)[0],
        },
    )
    return identity_id


async def redeem_code_on(
    conn: Any,
    tenant_id: str,
    *,
    code: str,
    provider: str,
    external_id: str,
    email: str | None,
    display_name: str | None,
) -> dict[str, Any]:
    """Забрать код и привязать личность — на открытом соединении (одна транзакция с вызывающим).

    Ошибки: code_invalid, code_expired, code_used, identity_taken (external_id уже у другого человека —
    транзакция откатывается, код остаётся действующим для своего владельца).
    Та же личность у того же человека — не ошибка: код гасится, возвращается прежняя привязка.
    """
    provider = normalize_provider(provider)
    normalized = normalize_code(code)
    if not normalized:
        _log_code_failed(tenant_id, provider, "code_invalid")
        raise error("code_invalid")
    try:
        row = await _claim_code(conn, tenant_id, normalized, provider)
    except LoginIdentityError as exc:
        _log_code_failed(tenant_id, provider, exc.code)
        raise
    telegram_user_id = int(row["telegram_user_id"])
    existing = await _active_identity(conn, tenant_id, provider, external_id)
    if existing and int(existing["telegram_user_id"]) != telegram_user_id:
        _log_code_failed(tenant_id, provider, "identity_taken")
        raise error("identity_taken")
    if existing:
        return {"telegram_user_id": telegram_user_id, "identity_id": str(existing["identity_id"]), "already_linked": True}
    identity_id = await _insert_identity(
        conn,
        tenant_id,
        telegram_user_id=telegram_user_id,
        provider=provider,
        external_id=external_id,
        email=email,
        display_name=display_name,
        linked_by=f"code:{row.get('issued_by') or ''}".rstrip(":"),
    )
    return {"telegram_user_id": telegram_user_id, "identity_id": identity_id, "already_linked": False}


def _log_code_failed(tenant_id: str, provider: str, reason: str) -> None:
    logger.info("login_code_failed", extra={"tenant_id": tenant_id, "provider": provider, "reason": reason})


async def redeem_code(
    tenant_id: str,
    *,
    code: str,
    provider: str,
    external_id: str,
    email: str | None = None,
    display_name: str | None = None,
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        return await redeem_code_on(
            conn,
            tenant_id,
            code=code,
            provider=provider,
            external_id=external_id,
            email=email,
            display_name=display_name,
        )


# ---- личность → человек ---------------------------------------------------------------------------


async def identity_state(conn: Any, tenant_id: str, provider: str, external_id: str) -> tuple[str, int | None]:
    """('active', telegram_user_id) | ('revoked', None) — была привязка, отозвана | ('none', None)."""
    active = await _active_identity(conn, tenant_id, provider, external_id)
    if active:
        return "active", int(active["telegram_user_id"])
    if await _any_identity_exists(conn, tenant_id, provider, external_id):
        return "revoked", None
    return "none", None


async def ensure_identity(
    conn: Any,
    tenant_id: str,
    *,
    telegram_user_id: int,
    provider: str,
    external_id: str,
    display_name: str | None,
    linked_by: str,
) -> None:
    """Привязка, созданная не кодом, а Telegram-ботом (/max, V30/V31): записать, если активной ещё нет."""
    existing = await _active_identity(conn, tenant_id, provider, external_id)
    if existing:
        return
    await _insert_identity(
        conn,
        tenant_id,
        telegram_user_id=int(telegram_user_id),
        provider=provider,
        external_id=external_id,
        email=None,
        display_name=display_name,
        linked_by=linked_by,
    )


async def list_identities(conn: Any, tenant_id: str, telegram_user_id: int) -> list[dict[str, Any]]:
    rows = await fetch_all(
        conn,
        """
        select identity_id, provider, external_id, email, display_name, linked_at, linked_by
        from account_identities
        where tenant_id = %s and telegram_user_id = %s and revoked_at is null
        order by linked_at, provider
        """,
        (tenant_id, int(telegram_user_id)),
    )
    return [dict(r) for r in rows]


async def list_login_methods(tenant_id: str, telegram_user_id: int, *, conn: Any = None) -> list[dict[str, Any]]:
    """Для кабинета /me: [{provider, label, linked_at}] — Telegram всегда первый.

    ``conn`` — открытое ``tenant_connection`` того же арендатора; без него открывается своё."""
    if conn is not None:
        identities = await list_identities(conn, tenant_id, telegram_user_id)
    else:
        async with tenant_connection(tenant_id) as own:
            identities = await list_identities(own, tenant_id, telegram_user_id)
    return login_methods_payload(identities)


async def revoke_identity(tenant_id: str, *, telegram_user_id: int, provider: str, revoked_by: str) -> int:
    """Отвязать Google или Max у человека; Telegram отвязать нельзя. Возвращает число отозванных."""
    provider = normalize_provider(provider)
    if provider not in REVOCABLE_PROVIDERS:
        raise error("provider_not_revocable")
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            update account_identities set revoked_at = now(), revoked_by = %s
            where tenant_id = %s and telegram_user_id = %s and provider = %s and revoked_at is null
            returning identity_id
            """,
            (revoked_by, tenant_id, int(telegram_user_id), provider),
        )
    for row in rows:
        logger.info(
            "login_identity_revoked",
            extra={
                "tenant_id": tenant_id,
                "provider": provider,
                "identity_id": str(row["identity_id"]),
                "telegram_user_id": int(telegram_user_id),
                "revoked_by": revoked_by.split(":", 1)[0],
            },
        )
    return len(rows)


async def active_code_info(conn: Any, tenant_id: str, telegram_user_id: int) -> datetime | None:
    """Когда истекает действующий код человека (для «способы входа» владельцу); None — кода нет."""
    row = await fetch_one(
        conn,
        """
        select expires_at from login_link_codes
        where tenant_id = %s and telegram_user_id = %s and used_at is null and revoked_at is null
          and expires_at > now()
        order by created_at desc limit 1
        """,
        (tenant_id, int(telegram_user_id)),
    )
    return row["expires_at"] if row else None


# ---- попытки ввода кода (login_pending) -----------------------------------------------------------


async def _lock_subject(conn: Any, tenant_id: str, provider: str, external_id: str) -> None:
    """Advisory-блокировка на личность до конца транзакции: параллельные попытки идут по одной."""
    async with conn.cursor() as cur:
        await cur.execute(
            "select pg_advisory_xact_lock(hashtext(%s))",
            (f"login_attempt:{tenant_id}:{provider}:{external_id}",),
        )


async def _take_attempt(conn: Any, tenant_id: str, *, provider: str, external_id: str, pending_id: str) -> None:
    """Одна попытка на личность (provider, external_id) в окне PENDING_TTL; всего MAX_CODE_ATTEMPTS.

    Считается по всем строкам личности в окне (несколько входов через Google — один счётчик), под
    блокировкой ``_lock_subject``; само увеличение — условный update с ``returning``, чтобы и без
    блокировки нельзя было проскочить. Закрытая строка (failed) держит замок до конца окна.
    """
    used = await _attempts_used(conn, tenant_id, provider, external_id)
    if used >= MAX_CODE_ATTEMPTS:
        raise error("code_locked")
    updated = await fetch_one(
        conn,
        """
        update login_pending
        set attempts = attempts + 1,
            status = case when %s + 1 >= %s then 'failed' else status end
        where pending_id = %s::uuid and attempts < %s
        returning attempts
        """,
        (used, MAX_CODE_ATTEMPTS, pending_id, MAX_CODE_ATTEMPTS),
    )
    if not updated:
        raise error("code_locked")


async def _attempts_used(conn: Any, tenant_id: str, provider: str, external_id: str) -> int:
    """Сколько попыток кода на эту личность по всем её строкам, окно которых ещё не истекло.

    Только по счётчику: статус failed ставится и сгоревшему state, и строке после чужого браузера —
    это не замок (иначе одна чужая ссылка запирала бы человеку его же Google)."""
    row = await fetch_one(
        conn,
        """
        select coalesce(sum(attempts), 0)::int as used
        from login_pending
        where tenant_id = %s and provider = %s and external_id = %s and expires_at > now()
        """,
        (tenant_id, provider, str(external_id)),
    )
    return int((row or {}).get("used") or 0)


async def _locked(conn: Any, tenant_id: str, provider: str, external_id: str) -> bool:
    """Исчерпаны ли MAX_CODE_ATTEMPTS на эту личность в текущем окне."""
    return await _attempts_used(conn, tenant_id, provider, external_id) >= MAX_CODE_ATTEMPTS


async def _fail_pending(conn: Any, pending_id: str) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "update login_pending set status = 'failed' where pending_id = %s::uuid and status <> 'linked'",
            (pending_id,),
        )


async def _fail_pending_committed(tenant_id: str, pending_id: str) -> None:
    """Закрыть pending своей транзакцией — до того, как вызывающий поднимет ошибку (иначе откат)."""
    async with tenant_connection(tenant_id) as conn:
        await _fail_pending(conn, pending_id)


async def _mark_pending_linked(conn: Any, pending_id: str) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            "update login_pending set status = 'linked', used_at = now() where pending_id = %s::uuid",
            (pending_id,),
        )


# ---- Max: код из мини-приложения -----------------------------------------------------------------


async def redeem_max_code(
    tenant_id: str, *, max_user_id: int, display_name: str | None, code: str
) -> dict[str, Any]:
    """Незнакомый Max вводит код владельца. Попытки считаются на (max, id) в окне 15 минут.

    Ошибки: code_invalid, code_expired, code_used, code_locked, identity_taken.
    """
    external_id = str(int(max_user_id))
    # Попытка — отдельной транзакцией: неверный код откатывает транзакцию проверки, а счётчик должен остаться.
    async with tenant_connection(tenant_id) as conn:
        await _lock_subject(conn, tenant_id, "max", external_id)
        if await _locked(conn, tenant_id, "max", external_id):
            _log_code_failed(tenant_id, "max", "code_locked")
            raise error("code_locked")
        pending = await fetch_one(
            conn,
            """
            select pending_id from login_pending
            where tenant_id = %s and provider = 'max' and external_id = %s
              and status = 'awaiting_code' and expires_at > now()
            order by created_at desc limit 1
            """,
            (tenant_id, external_id),
        )
        if not pending:
            pending = await fetch_one(
                conn,
                """
                insert into login_pending (tenant_id, provider, status, external_id, display_name, expires_at)
                values (%s, 'max', 'awaiting_code', %s, %s, %s)
                returning pending_id
                """,
                (tenant_id, external_id, display_name, pending_expires_at()),
            )
        pending_id = str(pending["pending_id"])
        try:
            await _take_attempt(conn, tenant_id, provider="max", external_id=external_id, pending_id=pending_id)
        except LoginIdentityError as exc:
            _log_code_failed(tenant_id, "max", exc.code)
            raise
    async with tenant_connection(tenant_id) as conn:
        result = await redeem_code_on(
            conn,
            tenant_id,
            code=code,
            provider="max",
            external_id=external_id,
            email=None,
            display_name=display_name,
        )
        await _mark_pending_linked(conn, pending_id)
    return result


# ---- Google: start → callback → код ---------------------------------------------------------------


def _absolute_return_to(host: str, return_to: str) -> str:
    """Абсолютный https-адрес страницы, куда вернуть человека; хост проверяется как у Telegram-входа."""
    value = str(return_to or "").strip() or "/"
    if "://" not in value:
        value = f"https://{str(host or '').strip().lower()}{value if value.startswith('/') else '/' + value}"
    try:
        safe = sanitize_return_to(value)
    except HTTPException as exc:
        raise error("invalid_return_to") from exc
    if not safe.startswith("https://"):
        raise error("invalid_return_to")
    return safe


async def return_target(tenant_id: str, *, host: str, return_to: str) -> str:
    """Куда вернуть браузер с ошибкой: страница из return_to, при негодном пути — корень того же хоста.

    Негодный хост (не семья и не витрина) — LoginIdentityError(invalid_return_to): редиректить некуда.
    """
    await warm_hosts(tenant_id)
    try:
        return _absolute_return_to(host, return_to)
    except LoginIdentityError:
        return _absolute_return_to(host, "/")


def request_host(headers: Any) -> str:
    raw = (headers.get("x-forwarded-host") or headers.get("host") or "") if headers else ""
    host = str(raw).split(",", 1)[0].strip().lower()
    return host.split(":", 1)[0]


def cookie_mode(config: google_oauth.GoogleOAuthConfig, target_host: str) -> bool:
    """Хост входа в семье cookie callback-хоста (redirect из настроек): браузер подтверждает cookie
    wwc_oauth. Иначе (витрина на своём домене) cookie до callback не дойдёт — подтверждает ключ сайта."""
    callback_host = (urlparse(config.redirect_uri).hostname or "").lower()
    return same_cookie_family(callback_host, target_host)


async def google_start(
    tenant_id: str,
    *,
    host: str,
    return_to: str,
    visitor_session_id: str | None,
    site_key: str | None = None,
) -> tuple[str, str | None]:
    """(адрес Google, ключ для cookie wwc_oauth или None). Состояние — строка login_pending.

    Хост входа в семье cookie: ключ генерирует сервер, он уходит в cookie; ``site_key`` от клиента
    игнорируется (иначе чужая страница навязала бы браузеру жертвы известный ей ключ).
    Хост вне семьи: нужен ``site_key`` (сайт держит его в sessionStorage), хранится отдельно
    (site_key_hash), cookie не ставится. Нет или негодный — ``browser_key_required``.
    """
    config = google_oauth.require_google_config()
    await warm_hosts(tenant_id)
    target = _absolute_return_to(host, return_to)
    target_host = urlparse(target).hostname or ""
    cookie_key: str | None = None
    browser_key_hash = site_key_hash = None
    if cookie_mode(config, target_host):
        cookie_key = new_browser_key()
        browser_key_hash = sha256_hex(cookie_key)
    else:
        key = normalize_browser_key(site_key)
        if not key:
            raise error("browser_key_required")
        site_key_hash = sha256_hex(key)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier, challenge = pkce_pair()
    session_raw = str(visitor_session_id or "").strip() or None
    async with tenant_connection(tenant_id) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                insert into login_pending
                  (tenant_id, provider, status, state_hash, nonce_hash, code_verifier, host, return_to,
                   visitor_session_id, browser_key_hash, site_key_hash, expires_at)
                values (%s, 'google', 'started', %s, %s, %s, %s, %s, %s::uuid, %s, %s, %s)
                """,
                (
                    tenant_id, sha256_hex(state), sha256_hex(nonce), verifier,
                    urlparse(target).netloc, target, _uuid_or_none(session_raw), browser_key_hash,
                    site_key_hash, pending_expires_at(),
                ),
            )
    return google_oauth.build_auth_url(config, state=state, nonce=nonce, code_challenge=challenge), cookie_key


def presented_key(pending: dict[str, Any], *, cookie_key: str | None, body_key: str | None) -> tuple[str | None, str | None]:
    """(что предъявлено, с каким хешем сверять) — строго по режиму строки, без подмены одного другим.

    Строка с browser_key_hash (хост в семье) — только cookie; с site_key_hash (витрина) — только body.
    Ни того ни другого — (None, None): сверка не пройдёт."""
    if pending.get("browser_key_hash"):
        return cookie_key, str(pending["browser_key_hash"])
    if pending.get("site_key_hash"):
        return body_key, str(pending["site_key_hash"])
    return None, None


def _uuid_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


async def _new_awaiting(
    conn: Any,
    tenant_id: str,
    *,
    status: str,
    pending: dict[str, Any],
    claims: dict[str, Any],
) -> str:
    """Строка «ждёт код» / «ждёт подтверждения браузера» с тем же ключом браузера; возвращает link token."""
    link_token = secrets.token_urlsafe(24)
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into login_pending
              (tenant_id, provider, status, host, return_to, visitor_session_id, browser_key_hash,
               site_key_hash, external_id, email, display_name, link_token_hash, expires_at)
            values (%s, 'google', %s, %s, %s, %s::uuid, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant_id, status, str(pending["host"]), str(pending["return_to"]),
                _uuid_or_none(str(pending.get("visitor_session_id") or "")), pending.get("browser_key_hash"),
                pending.get("site_key_hash"), claims["sub"], claims["email"], claims["name"],
                sha256_hex(link_token), pending_expires_at(),
            ),
        )
    return link_token


async def google_callback(
    tenant_id: str,
    *,
    state: str,
    code: str | None,
    oauth_error: str | None,
    cookie_key: str | None,
) -> dict[str, Any]:
    """Результат callback для ручки.

    {"outcome": "signed_in", telegram_user_id, host, return_to, visitor_session_id}
    {"outcome": "link_required", link_token, claim: bool, host, return_to}
        claim=True — Google уже привязан, но хост вне семьи cookie: сайт подтверждает браузер ключом
        через POST /login/link без кода.
    {"outcome": "error", error, host, return_to}
    LoginIdentityError: google_state_invalid (неизвестный/просроченный/повторный state),
    oauth_browser_mismatch (callback открыт не в том браузере, где начали вход) — хоста для возврата нет.

    Хост в семье cookie: ``cookie_key`` (cookie wwc_oauth) обязан совпасть. Хост вне семьи: cookie не
    смотрится вовсе — браузер подтверждается ключом сайта на шаге /login/link.
    """
    config = google_oauth.require_google_config()
    state_hash = sha256_hex(str(state or "").strip())
    async with tenant_connection(tenant_id) as conn:
        pending = await fetch_one(
            conn,
            """
            select pending_id, status, nonce_hash, code_verifier, host, return_to, visitor_session_id,
                   browser_key_hash, site_key_hash, expires_at
            from login_pending
            where tenant_id = %s and provider = 'google' and state_hash = %s
            limit 1
            """,
            (tenant_id, state_hash),
        )
        if not pending or pending.get("status") != "started":
            raise error("google_state_invalid")
        if pending["expires_at"] < utcnow():
            raise error("google_state_invalid", reason="expired")
        # state одноразовый: повторный callback с тем же state — ошибка, а не второй вход.
        await _fail_pending(conn, str(pending["pending_id"]))
    host, return_to = str(pending["host"]), str(pending["return_to"])
    base = {"host": host, "return_to": return_to}
    # Браузер — по режиму, выбранному на start: cookie (хост в семье) сверяется здесь; ключ сайта (витрина)
    # — на шаге /login/link, поэтому такой вход всегда идёт через #wwc-link (deferred).
    if pending.get("browser_key_hash"):
        if not browser_key_matches(cookie_key, pending["browser_key_hash"]):
            reason = "cookie" if cookie_key else "no_cookie"
            logger.info("login_google_browser_mismatch", extra={"tenant_id": tenant_id, "reason": reason})
            raise error("oauth_browser_mismatch")
        deferred = False
    elif pending.get("site_key_hash"):
        deferred = True
    else:
        raise error("oauth_browser_mismatch")
    if oauth_error or not code:
        logger.info("login_google_denied", extra={"tenant_id": tenant_id, "reason": str(oauth_error or "no_code")[:40]})
        return {"outcome": "error", "error": "google_denied", **base}
    try:
        tokens = await google_oauth.exchange_code(config, code=str(code), code_verifier=str(pending["code_verifier"]))
        claims = check_id_token_claims(
            decode_jwt_payload(str(tokens.get("id_token") or "")),
            client_id=config.client_id,
            nonce_hash=str(pending["nonce_hash"]),
        )
    except LoginIdentityError as exc:
        logger.info("login_google_failed", extra={"tenant_id": tenant_id, "reason": exc.code, **exc.details})
        return {"outcome": "error", "error": exc.code, **base}
    async with tenant_connection(tenant_id) as conn:
        state_name, telegram_user_id = await identity_state(conn, tenant_id, "google", claims["sub"])
        if state_name == "active" and telegram_user_id is not None:
            if not deferred:
                return {
                    "outcome": "signed_in",
                    "telegram_user_id": telegram_user_id,
                    "visitor_session_id": str(pending.get("visitor_session_id") or "") or None,
                    **base,
                }
            link_token = await _new_awaiting(conn, tenant_id, status="awaiting_claim", pending=pending, claims=claims)
            return {"outcome": "link_required", "link_token": link_token, "claim": True, **base}
        if await _locked(conn, tenant_id, "google", claims["sub"]):
            # Пять неверных кодов подряд: новый вход через Google в том же окне попыток не добавляет.
            _log_code_failed(tenant_id, "google", "code_locked")
            return {"outcome": "error", "error": "code_locked", **base}
        link_token = await _new_awaiting(conn, tenant_id, status="awaiting_code", pending=pending, claims=claims)
    return {"outcome": "link_required", "link_token": link_token, "claim": False, **base}


async def _awaiting_row(conn: Any, tenant_id: str, link_token: str) -> dict[str, Any]:
    pending = await fetch_one(
        conn,
        """
        select pending_id, status, provider, external_id, email, display_name, visitor_session_id,
               browser_key_hash, site_key_hash, expires_at, attempts
        from login_pending
        where tenant_id = %s and link_token_hash = %s
        limit 1
        """,
        (tenant_id, sha256_hex(str(link_token or "").strip())),
    )
    if not pending or pending.get("status") not in {"awaiting_code", "awaiting_claim"}:
        raise error("pending_invalid")
    if pending["expires_at"] < utcnow():
        raise error("pending_expired")
    return pending


async def pending_info(tenant_id: str, *, link_token: str) -> dict[str, Any]:
    """Экран кода: {provider, email_masked, display_name, status} — без самого e-mail и без ключа."""
    async with tenant_connection(tenant_id) as conn:
        pending = await _awaiting_row(conn, tenant_id, link_token)
    return {
        "provider": str(pending["provider"]),
        "email_masked": mask_email(pending.get("email")),
        "display_name": str(pending.get("display_name") or ""),
        "status": str(pending["status"]),
    }


async def link_pending(
    tenant_id: str,
    *,
    link_token: str,
    code: str | None,
    cookie_key: str | None,
    body_key: str | None,
) -> dict[str, Any]:
    """«Введите код от Виктора» после Google (или подтверждение браузера, когда Google уже привязан).

    Браузер — строго по режиму строки (``presented_key``): хост в семье — только cookie wwc_oauth,
    витрина — только ключ сайта из body; одно другим не заменяется.
    Ошибки: pending_invalid, pending_expired, oauth_browser_mismatch (ключ не того браузера — pending
    закрывается), code_required, code_locked, code_invalid, code_expired, code_used, identity_taken.
    """
    async with tenant_connection(tenant_id) as conn:
        pending = await _awaiting_row(conn, tenant_id, link_token)
    pending_id = str(pending["pending_id"])
    provided, stored = presented_key(pending, cookie_key=cookie_key, body_key=body_key)
    if not browser_key_matches(provided, stored):
        # Закрыть — отдельной транзакцией, потом ошибка (внутри блока откат стёр бы закрытие).
        await _fail_pending_committed(tenant_id, pending_id)
        logger.info("login_google_browser_mismatch", extra={"tenant_id": tenant_id, "reason": "link"})
        raise error("oauth_browser_mismatch")
    provider, external_id = str(pending["provider"]), str(pending["external_id"])
    if pending["status"] == "awaiting_claim":
        async with tenant_connection(tenant_id) as conn:
            state_name, telegram_user_id = await identity_state(conn, tenant_id, provider, external_id)
            if state_name == "active" and telegram_user_id is not None:
                await _mark_pending_linked(conn, pending_id)
        if state_name != "active" or telegram_user_id is None:
            await _fail_pending_committed(tenant_id, pending_id)
            raise error("pending_invalid")
        return {
            "telegram_user_id": telegram_user_id,
            "identity_id": None,
            "already_linked": True,
            "visitor_session_id": str(pending.get("visitor_session_id") or "") or None,
        }
    if not str(code or "").strip():
        raise error("code_required")
    # Попытка фиксируется до проверки кода и своей транзакцией (см. redeem_max_code).
    async with tenant_connection(tenant_id) as conn:
        await _lock_subject(conn, tenant_id, provider, external_id)
        try:
            await _take_attempt(conn, tenant_id, provider=provider, external_id=external_id, pending_id=pending_id)
        except LoginIdentityError as exc:
            _log_code_failed(tenant_id, provider, exc.code)
            raise
    async with tenant_connection(tenant_id) as conn:
        result = await redeem_code_on(
            conn,
            tenant_id,
            code=str(code),
            provider=provider,
            external_id=external_id,
            email=pending.get("email"),
            display_name=pending.get("display_name"),
        )
        await _mark_pending_linked(conn, pending_id)
    return {
        **result,
        "visitor_session_id": str(pending.get("visitor_session_id") or "") or None,
    }


# ---- чистка (worker) ------------------------------------------------------------------------------


async def purge_expired_login_rows(tenant_ids: list[str]) -> int:
    """Удалить pending и коды, просроченные больше суток назад; один тенант не ломает остальные."""
    deleted = 0
    for tenant_id in tenant_ids:
        try:
            async with tenant_connection(tenant_id) as conn:
                for table in ("login_pending", "login_link_codes"):
                    rows = await fetch_all(
                        conn,
                        f"delete from {table} where tenant_id = %s and expires_at < now() - interval '{PURGE_AFTER}' returning 1 as n",
                        (tenant_id,),
                    )
                    deleted += len(rows)
        except Exception:
            logger.warning("login_cleanup_failed", extra={"tenant_id": tenant_id}, exc_info=True)
    return deleted


async def login_cleanup_step() -> int:
    """Шаг worker: чистка у всех активных тенантов."""
    settings = get_settings()
    async with get_pool().connection(timeout=settings.database_timeout_sec) as conn:
        rows = await fetch_all(conn, "select tenant_id from tenants where status = 'active' order by tenant_id")
    return await purge_expired_login_rows([str(r["tenant_id"]) for r in rows])


# ---- сессии и ссылки возврата ---------------------------------------------------------------------


def same_cookie_family(host_a: str, host_b: str) -> bool:
    """Cookie, выставленная на host_a, действует и на host_b (одна семья .wwc.best или тот же хост)."""
    a, b = str(host_a or "").lower().strip(), str(host_b or "").lower().strip()
    if not a or not b:
        return False
    if a == b:
        return True
    for domain in shared_cookie_domains():
        in_a = a == domain or a.endswith("." + domain)
        in_b = b == domain or b.endswith("." + domain)
        if in_a and in_b:
            return True
    return False


async def browser_login_url(tenant_id: str, *, telegram_user_id: int, return_to: str) -> str:
    """https://<host><путь>#wwc-login=<одноразовый> (10 минут) — сайт входит сам на нужном хосте."""
    await warm_hosts(tenant_id)
    safe = sanitize_return_to(return_to)
    if not safe.startswith("https://"):
        domains = shared_cookie_domains()
        if not domains:
            return ""
        safe = f"https://{domains[0]}{safe}"
    parts = urlparse(safe)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    token = await create_bot_login(
        tenant_id, telegram_user_id=int(telegram_user_id), return_to=path, ttl_minutes=BROWSER_LOGIN_TTL_MINUTES
    )
    return f"https://{parts.netloc}{path}#{BOT_LOGIN_FRAGMENT}={token}"


def link_url(return_to: str, link_token: str) -> str:
    return f"{return_to.split('#', 1)[0]}#{LINK_FRAGMENT}={link_token}"


def link_error_url(return_to: str, code: str) -> str:
    return f"{return_to.split('#', 1)[0]}#{LINK_ERROR_FRAGMENT}={code}"


async def session_for(tenant_id: str, *, telegram_user_id: int, visitor_session_id: str | None, source: str):
    return await create_session_for_user(
        tenant_id, telegram_user_id=int(telegram_user_id), visitor_session_id=visitor_session_id, source=source
    )
