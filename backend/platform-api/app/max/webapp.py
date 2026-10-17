"""Вход на сайт через Max — мини-приложение бота Max (11.10.2026).

Владелец: людям из РФ трудно войти на сайт через Telegram — «авторизация на сайте: выбор Telegram / Max».
Правила Max (п. 1.5) запрещают боту без договора слать авторизационные сообщения, поэтому бот Max здесь
ничего не пишет: личность подтверждает сама платформа подписью initData мини-приложения
(https://dev.max.ru/docs/webapps/validation — тот же HMAC «WebAppData», что у Telegram WebApp).

Мини-приложение — страница сайта /max-app/ (адрес в настройках бота — корень wwc.best, он перекидывает
на /max-app/). По start_param:
  * ``login_<challenge>`` — сайт в браузере ждёт подтверждения заявки (как ссылка t.me/…?start=content_access_…);
  * ``link_<код>`` — привязать этот Max к человеку, которого знает Telegram-бот (/max, V30);
  * без параметра — вход прямо внутри Max (одноразовая ссылка #wwc-login=…).
Незнакомый Max (12.10.2026, владелец: «макс не хочет авторизовывать») получает кнопку
``t.me/<бот>?start=maxlink_<код>``: Telegram-бот привязывает Max к нажавшему и сразу подтверждает этот вход
(max_link_requests, V31). /max в Telegram-боте остаётся запасным путём.
Сессии сайта по-прежнему на telegram_user_id: Max-пользователь находится через account_identities
(provider max) → telegram_user_id. lead_actor_channels (переписка с ботом Max) личностью не считается.

15.10.2026 (V34, владелец): Telegram в Max больше не нужен. Незнакомый Max вводит в мини-приложении код,
который владелец выдал ему в Telegram-боте («код входа volkova»), — привязка в account_identities; дальше
мини-приложение получает ``browser_login_url`` (одноразовая ссылка входа, 10 минут) и открывает сайт в
системном браузере (openLink). Привязка через Telegram (/max, кнопка maxlink) остаётся вторым путём.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

from fastapi import HTTPException

from app.academy.showcases import warm_hosts
from app.content_access.service import (
    BOT_LOGIN_FRAGMENT,
    MAX_LOGIN_PREFIX,
    challenge_hash_of,
    confirm_content_by_hash,
    confirm_content_from_telegram,
    create_bot_login,
    sanitize_return_to,
)
from app.content_access.webapp_login import verify_init_data
from app.db import fetch_one, tenant_connection
from app.login_identities.rules import BROWSER_LOGIN_TTL_MINUTES, LoginIdentityError
from app.login_identities.service import ensure_identity, identity_state, redeem_max_code
from app.settings import get_settings

logger = logging.getLogger("whieda.max.webapp")

LOGIN_PREFIX = MAX_LOGIN_PREFIX
LINK_PREFIX = "link_"
LINK_CODE_TTL = timedelta(hours=24)
# Обратная привязка (V31): мини-приложение → кнопка t.me/<бот>?start=maxlink_<код> (Telegram start ≤ 64 символа).
MAXLINK_PREFIX = "maxlink_"
LINK_REQUEST_TTL = timedelta(minutes=30)
# initData живёт в sessionStorage мини-приложения: перезагрузка страницы шлёт ту же строку.
INIT_DATA_MAX_AGE_SEC = 60 * 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def max_app_link(payload: str) -> str:
    """https://max.ru/<бот>?startapp=<payload> — payload до 512 символов [A-Za-z0-9_-]."""
    username = str(get_settings().max_bot_username or "").lstrip("@")
    if not username:
        raise HTTPException(status_code=503, detail={"error": "max_bot_username_not_configured"})
    return f"https://max.ru/{username}?startapp={payload}"


def _normalize_init_data(raw: str) -> str:
    """Строка из #WebAppData: в одних клиентах уже «a=1&b=2», в других ещё раз закодирована целиком."""
    value = str(raw or "").strip()
    if value and "=" not in value and "%3d" in value.lower():
        value = unquote(value)
    return value


def verify_max_init_data(init_data: str, *, now: float | None = None) -> tuple[dict[str, Any], str]:
    """(user, start_param) из подписанного initData Max или HTTPException 400/401/503."""
    token = get_settings().max_bot_token
    if not token:
        raise HTTPException(status_code=503, detail={"error": "max_not_configured"})
    raw = _normalize_init_data(init_data)
    user = verify_init_data(raw, [token], now=now, max_age_sec=INIT_DATA_MAX_AGE_SEC)
    start_param = dict(parse_qsl(raw, keep_blank_values=True)).get("start_param", "")
    return user, str(start_param or "").strip()


def _display_name(user: dict[str, Any]) -> str:
    name = " ".join(str(user.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
    return name or str(user.get("username") or "") or f"Max {user.get('id')}"


async def _telegram_id_for_max(conn: Any, tenant_id: str, max_user_id: int) -> int | None:
    """Человек за этим Max: ТОЛЬКО действующая строка account_identities (provider max, не отозванная).

    17.10.2026: запасного пути через lead_actor_channels больше нет. Эта таблица — переписка с ботом Max, а
    не доказательство личности: канал туда попадает и без подтверждения (чужой Max-пользователь с @username
    партнёра раньше «прилипал» к его actor). Подтверждённые привязки — код владельца, кнопка maxlink,
    ссылка /max (link_<код>) — все пишут account_identities; старые подтверждённые перенесены бэкфиллом V34
    (только с доказательством в max_link_requests / channel_link_codes). Отозванная привязка не воскресает.
    """
    _, telegram_user_id = await identity_state(conn, tenant_id, "max", str(max_user_id))
    return telegram_user_id


async def create_max_link(tenant_id: str, *, telegram_user_id: int) -> str:
    """Ссылка привязки Max для человека, которого знает Telegram (бот /max, кабинет сайта)."""
    async with tenant_connection(tenant_id) as conn:
        actor = await fetch_one(
            conn,
            "select actor_id from lead_actors where tenant_id = %s and telegram_user_id = %s limit 1",
            (tenant_id, int(telegram_user_id)),
        )
        if not actor:
            raise HTTPException(status_code=404, detail={"error": "actor_not_found"})
        code = secrets.token_urlsafe(18)
        await fetch_one(
            conn,
            """
            insert into channel_link_codes (code_hash, tenant_id, actor_id, channel, expires_at)
            values (%s, %s, %s, 'max', %s)
            returning code_hash
            """,
            (_hash(code), tenant_id, str(actor["actor_id"]), _utcnow() + LINK_CODE_TTL),
        )
    return max_app_link(f"{LINK_PREFIX}{code}")


async def _redeem_link(conn: Any, tenant_id: str, code: str, user: dict[str, Any]) -> str:
    """'linked' | 'expired' | 'used' | 'not_found'. Max-пользователь переходит к человеку из кода."""
    row = await fetch_one(
        conn,
        "select actor_id, expires_at, used_at, used_channel_user_id from channel_link_codes where code_hash = %s limit 1",
        (_hash(code),),
    )
    if not row:
        return "not_found"
    max_user_id = str(user["id"])
    if row.get("used_at"):
        # повторное открытие той же ссылки тем же человеком — не ошибка
        return "linked" if str(row.get("used_channel_user_id") or "") == max_user_id else "used"
    if row["expires_at"] < _utcnow():
        return "expired"
    actor_id = str(row["actor_id"])
    username = str(user.get("username") or "") or None
    await fetch_one(
        conn,
        """
        insert into lead_actor_channels (tenant_id, actor_id, channel, channel_user_id, username, display_name)
        values (%s, %s, 'max', %s, %s, %s)
        on conflict (tenant_id, channel, channel_user_id) do update
          set actor_id = excluded.actor_id,
              username = coalesce(excluded.username, lead_actor_channels.username),
              display_name = excluded.display_name,
              updated_at = now()
        returning actor_id
        """,
        (tenant_id, actor_id, max_user_id, username, _display_name(user)),
    )
    await fetch_one(
        conn,
        "update channel_link_codes set used_at = now(), used_channel_user_id = %s where code_hash = %s returning code_hash",
        (max_user_id, _hash(code)),
    )
    # V34 + 17.10.2026: вход через Max читает только account_identities — подтверждённая ссылка /max пишет сюда.
    person = await fetch_one(
        conn,
        "select telegram_user_id from lead_actors where tenant_id = %s and actor_id = %s limit 1",
        (tenant_id, actor_id),
    )
    if (person or {}).get("telegram_user_id"):
        await ensure_identity(
            conn,
            tenant_id,
            telegram_user_id=int(person["telegram_user_id"]),
            provider="max",
            external_id=max_user_id,
            display_name=_display_name(user),
            linked_by="telegram:/max",
        )
    logger.info("max_linked", extra={"tenant_id": tenant_id, "actor_id": actor_id})
    return "linked"


async def _create_link_request(tenant_id: str, user: dict[str, Any], *, login_challenge_hash: str | None) -> str:
    """Заявка со стороны Max (V31) → https://t.me/<бот>?start=maxlink_<код>. Без имени Telegram-бота — ''."""
    bot = str(get_settings().telegram_bot_username or "").lstrip("@")
    if not bot:
        return ""
    code = secrets.token_urlsafe(18)
    async with tenant_connection(tenant_id) as conn:
        await fetch_one(
            conn,
            """
            insert into max_link_requests
              (code_hash, tenant_id, max_user_id, username, display_name, login_challenge_hash, expires_at)
            values (%s, %s, %s, %s, %s, %s, %s)
            returning code_hash
            """,
            (
                _hash(code), tenant_id, str(user["id"]), str(user.get("username") or "") or None,
                _display_name(user), login_challenge_hash, _utcnow() + LINK_REQUEST_TTL,
            ),
        )
    return f"https://t.me/{bot}?start={MAXLINK_PREFIX}{code}"


async def redeem_link_request(
    tenant_id: str,
    *,
    code: str,
    telegram_user_id: int,
    telegram_chat_id: int | None,
    telegram_username: str | None,
) -> dict[str, Any]:
    """Telegram-бот: /start maxlink_<код>. Привязать Max к нажавшему и подтвердить вход, ради которого
    открывали Max.

    status: linked | expired | used | not_found; login: confirmed | expired | used | not_found | None;
    return_to — страница, где нажимали «Войти» (для кнопки «Открыть сайт»).
    """
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select max_user_id, username, display_name, login_challenge_hash, expires_at, used_at,
                   used_by_telegram_user_id
            from max_link_requests where code_hash = %s limit 1
            """,
            (_hash(code),),
        )
        if not row:
            return {"status": "not_found", "login": None, "return_to": ""}
        if row.get("used_at"):
            same = int(row.get("used_by_telegram_user_id") or 0) == int(telegram_user_id)
            return {"status": "linked" if same else "used", "login": None, "return_to": ""}
        if row["expires_at"] < _utcnow():
            return {"status": "expired", "login": None, "return_to": ""}
        actor = await fetch_one(
            conn,
            "select actor_id from lead_actors where tenant_id = %s and telegram_user_id = %s limit 1",
            (tenant_id, int(telegram_user_id)),
        )
        if actor:
            actor_id = str(actor["actor_id"])
        else:
            actor_id = f"telegram:{tenant_id}:{int(telegram_user_id)}"
            await fetch_one(
                conn,
                """
                insert into lead_actors (actor_id, tenant_id, display_name, telegram_user_id, telegram_chat_id, telegram_username)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (actor_id) do update set telegram_user_id = excluded.telegram_user_id
                returning actor_id
                """,
                (
                    actor_id, tenant_id, str(row.get("display_name") or "") or f"Telegram {telegram_user_id}",
                    int(telegram_user_id), None if telegram_chat_id is None else str(telegram_chat_id), telegram_username,
                ),
            )
        await fetch_one(
            conn,
            """
            insert into lead_actor_channels (tenant_id, actor_id, channel, channel_user_id, username, display_name)
            values (%s, %s, 'max', %s, %s, %s)
            on conflict (tenant_id, channel, channel_user_id) do update
              set actor_id = excluded.actor_id,
                  username = coalesce(excluded.username, lead_actor_channels.username),
                  display_name = excluded.display_name,
                  updated_at = now()
            returning actor_id
            """,
            (tenant_id, actor_id, str(row["max_user_id"]), row.get("username"), str(row.get("display_name") or "")),
        )
        await fetch_one(
            conn,
            """
            update max_link_requests set used_at = now(), used_by_telegram_user_id = %s
            where code_hash = %s returning code_hash
            """,
            (int(telegram_user_id), _hash(code)),
        )
        # V34: та же привязка и в account_identities — кабинет её показывает и умеет отвязать.
        await ensure_identity(
            conn,
            tenant_id,
            telegram_user_id=int(telegram_user_id),
            provider="max",
            external_id=str(row["max_user_id"]),
            display_name=str(row.get("display_name") or "") or None,
            linked_by="telegram:maxlink",
        )
    logger.info("max_linked_from_telegram", extra={"tenant_id": tenant_id, "actor_id": actor_id})
    login_hash = str(row.get("login_challenge_hash") or "")
    if not login_hash:
        return {"status": "linked", "login": None, "return_to": ""}
    try:
        confirmed = await confirm_content_by_hash(
            tenant_id=tenant_id,
            challenge_hash=login_hash,
            telegram_user_id=int(telegram_user_id),
            telegram_chat_id=telegram_chat_id,
        )
    except HTTPException as exc:
        return {"status": "linked", "login": _login_status(exc), "return_to": ""}
    return {"status": "linked", "login": "confirmed", "return_to": str(confirmed.get("return_to") or "")}


def _login_status(exc: HTTPException) -> str:
    error = (exc.detail or {}).get("error", "") if isinstance(exc.detail, dict) else ""
    return {
        "challenge_expired": "expired",
        "challenge_already_used": "used",
        "challenge_not_pending": "used",
    }.get(error, "not_found")


async def _in_app_url(tenant_id: str, telegram_user_id: int, return_to: str) -> str:
    safe = sanitize_return_to(return_to)
    if not safe.startswith("https://"):
        return ""
    parts = urlparse(safe)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    token = await create_bot_login(tenant_id, telegram_user_id=telegram_user_id, return_to=path)
    return f"https://{parts.netloc}{path}#{BOT_LOGIN_FRAGMENT}={token}"


async def _browser_login_url(tenant_id: str, telegram_user_id: int, return_to: str, fallback: str) -> str:
    """Одноразовая ссылка входа для системного браузера (V34): страница, где нажали «Войти» (из заявки),
    иначе страница мини-приложения; путь без хоста (старый клиент) — на хосте мини-приложения."""
    target = str(return_to or "").strip()
    try:
        if target and "://" not in target:
            base = sanitize_return_to(fallback)
            host = urlparse(base).netloc if base.startswith("https://") else ""
            target = f"https://{host}{target}" if host else ""
        safe = sanitize_return_to(target or fallback)
    except HTTPException:
        return ""
    if not safe.startswith("https://"):
        return ""
    parts = urlparse(safe)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    token = await create_bot_login(
        tenant_id, telegram_user_id=telegram_user_id, return_to=path, ttl_minutes=BROWSER_LOGIN_TTL_MINUTES
    )
    return f"https://{parts.netloc}{path}#{BOT_LOGIN_FRAGMENT}={token}"


async def max_webapp(
    tenant_id: str, *, init_data: str, return_to: str, code: str | None = None
) -> dict[str, Any]:
    """Ответ странице /max-app/.

    status: login_confirmed | linked | signed_in | not_linked | link_expired | link_used | link_not_found |
            login_expired | login_used | login_not_found |
            code_invalid | code_expired | code_used | code_locked | identity_taken (V34: код не подошёл)
    url — вход прямо здесь, внутри Max (когда человек известен);
    browser_login_url — одноразовая ссылка для системного браузера (WebApp.openLink), 10 минут (V34);
    telegram_url — прежняя кнопка в Telegram-бот для незнакомого Max (сайт её больше не показывает).
    ``code`` — код от владельца: незнакомый Max привязывается к человеку из кода без Telegram.
    """
    await warm_hosts(tenant_id)
    user, start_param = verify_max_init_data(init_data)
    max_user_id = int(user["id"])
    status = "signed_in"
    async with tenant_connection(tenant_id) as conn:
        if start_param.startswith(LINK_PREFIX):
            linked = await _redeem_link(conn, tenant_id, start_param[len(LINK_PREFIX):], user)
            status = linked if linked == "linked" else f"link_{linked}"
        telegram_user_id = await _telegram_id_for_max(conn, tenant_id, max_user_id)
    if telegram_user_id is None and str(code or "").strip():
        try:
            redeemed = await redeem_max_code(
                tenant_id, max_user_id=max_user_id, display_name=_display_name(user), code=str(code)
            )
        except LoginIdentityError as exc:
            return {"ok": True, "status": exc.code, "url": "", "browser_login_url": "", "telegram_url": ""}
        telegram_user_id = int(redeemed["telegram_user_id"])
        status = "linked"
    if telegram_user_id is None:
        # Незнакомый Max без кода: прежняя кнопка в Telegram-бот (V31) — сайт её больше не показывает,
        # но старая страница мини-приложения ещё может.
        login_hash = challenge_hash_of(start_param[len(LOGIN_PREFIX):]) if start_param.startswith(LOGIN_PREFIX) else None
        telegram_url = await _create_link_request(tenant_id, user, login_challenge_hash=login_hash)
        return {
            "ok": True,
            "status": status if status.startswith("link_") else "not_linked",
            "url": "",
            "browser_login_url": "",
            "telegram_url": telegram_url,
        }
    confirmed_return_to = ""
    if start_param.startswith(LOGIN_PREFIX):
        try:
            confirmed = await confirm_content_from_telegram(
                tenant_id=tenant_id,
                challenge_token=start_param[len(LOGIN_PREFIX):],
                telegram_user_id=telegram_user_id,
            )
            status = "login_confirmed"
            confirmed_return_to = str((confirmed or {}).get("return_to") or "")
        except HTTPException as exc:
            status = f"login_{_login_status(exc)}"
    url = await _in_app_url(tenant_id, telegram_user_id, return_to)
    browser_url = await _browser_login_url(tenant_id, telegram_user_id, confirmed_return_to, return_to)
    return {"ok": True, "status": status, "url": url, "browser_login_url": browser_url}
