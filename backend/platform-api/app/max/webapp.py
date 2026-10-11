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
Сессии сайта по-прежнему на telegram_user_id: Max-пользователь находится через lead_actor_channels(max)
→ lead_actors.telegram_user_id.
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
    confirm_content_from_telegram,
    create_bot_login,
    sanitize_return_to,
)
from app.content_access.webapp_login import verify_init_data
from app.db import fetch_one, tenant_connection
from app.settings import get_settings

logger = logging.getLogger("whieda.max.webapp")

LOGIN_PREFIX = MAX_LOGIN_PREFIX
LINK_PREFIX = "link_"
LINK_CODE_TTL = timedelta(hours=24)
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
    row = await fetch_one(
        conn,
        """
        select la.telegram_user_id
        from lead_actor_channels c join lead_actors la on la.actor_id = c.actor_id
        where c.tenant_id = %s and c.channel = 'max' and c.channel_user_id = %s
        limit 1
        """,
        (tenant_id, str(max_user_id)),
    )
    value = (row or {}).get("telegram_user_id")
    return int(value) if value else None


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
    logger.info("max_linked", extra={"tenant_id": tenant_id, "actor_id": actor_id})
    return "linked"


async def _in_app_url(tenant_id: str, telegram_user_id: int, return_to: str) -> str:
    safe = sanitize_return_to(return_to)
    if not safe.startswith("https://"):
        return ""
    parts = urlparse(safe)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    token = await create_bot_login(tenant_id, telegram_user_id=telegram_user_id, return_to=path)
    return f"https://{parts.netloc}{path}#{BOT_LOGIN_FRAGMENT}={token}"


async def max_webapp(tenant_id: str, *, init_data: str, return_to: str) -> dict[str, Any]:
    """Ответ странице /max-app/.

    status: login_confirmed | linked | signed_in | not_linked | link_expired | link_used | link_not_found |
            login_expired | login_used | login_not_found
    url — вход прямо здесь, внутри Max (когда человек известен).
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
    if telegram_user_id is None:
        return {"ok": True, "status": status if status.startswith("link_") else "not_linked", "url": ""}
    if start_param.startswith(LOGIN_PREFIX):
        try:
            await confirm_content_from_telegram(
                tenant_id=tenant_id,
                challenge_token=start_param[len(LOGIN_PREFIX):],
                telegram_user_id=telegram_user_id,
            )
            status = "login_confirmed"
        except HTTPException as exc:
            error = (exc.detail or {}).get("error", "") if isinstance(exc.detail, dict) else ""
            status = {
                "challenge_expired": "login_expired",
                "challenge_already_used": "login_used",
                "challenge_not_pending": "login_used",
            }.get(error, "login_not_found")
    url = await _in_app_url(tenant_id, telegram_user_id, return_to)
    return {"ok": True, "status": status, "url": url}
