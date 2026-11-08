"""Устройства человека: подпись из User-Agent, префикс IP, лимит двух активных сессий (V35, 08.11.2026).

Без библиотек и без базы в разборе: подпись нужна только человеку в списке «Устройства»
(«iPhone · Safari»), а префикс IP — владельцу при разборе («вход с чужого устройства»), полный адрес
не хранится. Лимит: после создания сессии лишние (самые давно не виденные) отзываются —
``revoked_by = 'device_limit'``.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Any

from app.db import fetch_all, fetch_one

logger = logging.getLogger("whieda.content_access.devices")

MAX_ACTIVE_SESSIONS = 2
REVOKED_BY_LIMIT = "device_limit"
REVOKED_BY_OTHERS = "self:revoke_others"
SHORT_ID_LEN = 8
LABEL_MAX_LEN = 60


def device_label(user_agent: str | None) -> str:
    """«iPhone · Safari», «Android · Chrome», «Windows · Chrome»; неизвестное → «Устройство»."""
    ua = str(user_agent or "")
    low = ua.lower()
    if not low.strip():
        return "Устройство"
    if "iphone" in low:
        system = "iPhone"
    elif "ipad" in low:
        system = "iPad"
    elif "android" in low:
        system = "Android"
    elif "windows" in low:
        system = "Windows"
    elif "mac os x" in low or "macintosh" in low:
        system = "Mac"
    elif "cros" in low:
        system = "Chromebook"
    elif "linux" in low or "x11" in low:
        system = "Linux"
    else:
        system = ""
    # Порядок важен: Edge, Opera, Yandex и Samsung содержат в UA слово Chrome, Chrome — слово Safari.
    if "edg/" in low or "edga/" in low or "edgios/" in low:
        browser = "Edge"
    elif "opr/" in low or "opera" in low:
        browser = "Opera"
    elif "yabrowser" in low:
        browser = "Яндекс Браузер"
    elif "samsungbrowser" in low:
        browser = "Samsung Internet"
    elif "firefox/" in low or "fxios/" in low:
        browser = "Firefox"
    elif "chrome/" in low or "crios/" in low:
        browser = "Chrome"
    elif "safari/" in low:
        browser = "Safari"
    else:
        browser = ""
    label = " · ".join(part for part in (system, browser) if part) or "Устройство"
    return label[:LABEL_MAX_LEN]


def ip_hint(ip: str | None) -> str | None:
    """Первые два октета IPv4 («95.84.*.*») или первые 32 бита IPv6 («2a02:6b8::/32»); полный адрес не хранится."""
    raw = str(ip or "").strip()
    if not raw:
        return None
    try:
        address = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv4Address):
        parts = str(address).split(".")
        return f"{parts[0]}.{parts[1]}.*.*"
    mapped = address.ipv4_mapped
    if mapped is not None:
        parts = str(mapped).split(".")
        return f"{parts[0]}.{parts[1]}.*.*"
    network = ipaddress.ip_network(f"{address}/32", strict=False)
    return f"{network.network_address.compressed}/32"


def client_ip(headers: Any, peer: str | None) -> str | None:
    """Адрес клиента за прокси (nginx кладёт X-Forwarded-For / X-Real-IP), иначе адрес соединения."""
    if headers:
        forwarded = str(headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
        real = str(headers.get("x-real-ip") or "").strip()
        if real:
            return real
    return peer


async def enforce_device_limit(
    conn: Any, tenant_id: str, telegram_user_id: int, *, keep: int = MAX_ACTIVE_SESSIONS
) -> list[str]:
    """Оставить ``keep`` самых свежих по last_seen_at активных сессий человека, остальные отозвать.

    Возвращает session_hash отозванных (вызывающий выбрасывает их из кэша проверки). Вызывается в той же транзакции, что и вставка новой сессии;
    advisory-блокировка на человека не даёт двум одновременным входам обойти лимит."""
    await conn.execute(
        "select pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"device_limit:{tenant_id}:{int(telegram_user_id)}",)
    )
    rows = await fetch_all(
        conn,
        """
        update content_access_sessions set revoked_at = now(), revoked_by = %s
        where session_id in (
          select session_id from content_access_sessions
          where tenant_id = %s and telegram_user_id = %s and revoked_at is null and expires_at > now()
          order by last_seen_at desc, created_at desc
          offset %s
        )
        returning session_id, session_hash
        """,
        (REVOKED_BY_LIMIT, tenant_id, int(telegram_user_id), int(keep)),
    )
    if rows:
        logger.info(
            "device_limit_revoked",
            extra={"tenant_id": tenant_id, "telegram_user_id": int(telegram_user_id), "revoked": len(rows)},
        )
    return [str(r["session_hash"]) for r in rows]


def short_id(session_id: Any) -> str:
    return str(session_id).replace("-", "")[:SHORT_ID_LEN]


async def list_active_sessions(
    conn: Any, tenant_id: str, telegram_user_id: int, *, current_session_id: str | None
) -> list[dict[str, Any]]:
    rows = await fetch_all(
        conn,
        """
        select session_id, device_label, created_at, last_seen_at
        from content_access_sessions
        where tenant_id = %s and telegram_user_id = %s and revoked_at is null and expires_at > now()
        order by last_seen_at desc, created_at desc
        """,
        (tenant_id, int(telegram_user_id)),
    )
    current = str(current_session_id or "")
    result = []
    for row in rows:
        result.append(
            {
                "id": short_id(row["session_id"]),
                "device_label": str(row.get("device_label") or "") or "Устройство",
                "created_at": row["created_at"].isoformat(),
                "last_seen_at": row["last_seen_at"].isoformat(),
                "current": bool(current) and str(row["session_id"]) == current,
            }
        )
    return result


async def revoke_other_sessions(
    conn: Any, tenant_id: str, telegram_user_id: int, *, current_session_id: str
) -> list[str]:
    """Отозвать все активные сессии человека, кроме текущей; возвращает session_hash отозванных (для кэша)."""
    rows = await fetch_all(
        conn,
        """
        update content_access_sessions set revoked_at = now(), revoked_by = %s
        where tenant_id = %s and telegram_user_id = %s and revoked_at is null and session_id <> %s::uuid
        returning session_hash
        """,
        (REVOKED_BY_OTHERS, tenant_id, int(telegram_user_id), str(current_session_id)),
    )
    return [str(r["session_hash"]) for r in rows]


async def session_exists(conn: Any, tenant_id: str, session_id: str) -> bool:
    return bool(
        await fetch_one(
            conn,
            "select 1 as present from content_access_sessions where tenant_id = %s and session_id = %s::uuid and revoked_at is null",
            (tenant_id, session_id),
        )
    )
