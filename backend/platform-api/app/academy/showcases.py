"""Витрины Академии (V29, 11.10.2026): одно ядро — разные адреса, бренды и боты.

Витрина — строка ``academy_showcases``. Она решает только, как Академия выглядит
и кто «свой»: адрес, бренд, бот входа и уведомлений, порядок курсов. Права доступа
(``course_lock_reason``) от неё не зависят: подменённый заголовок витрины не
открывает ни одного курса.

Витрина запроса: ``X-WWC-Showcase-Host`` (nginx ставит ``$host``) → ``X-Forwarded-Host``
→ ``Host``; не нашлась — витрина по умолчанию (wwc.best). ``?showcase=<id>`` —
только предпросмотр оформления и порядка.

Хосты витрин — граница безопасности входа: ``return_to``, ``with_site_login`` и
``/tg-open/`` принимают только wwc.best, *.wwc.best и точный список отсюда.
Список держится в памяти процесса (обновление раз в минуту): проверки адреса
синхронные, а перед ними асинхронный код зовёт ``warm_hosts``.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.db import fetch_all, fetch_one, tenant_connection
from app.settings import get_settings
from app.tenancy import normalize_host

logger = logging.getLogger(__name__)

CACHE_TTL_SEC = 60.0
DEFAULT_ID = "wwc"
SHOWCASE_HEADER = "x-wwc-showcase-host"
SHOWCASE_QUERY = "showcase"
MORE_MODES = ("none", "no_pro", "all")

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_TOKEN_RE = re.compile(r"^--[a-z0-9-]{1,40}$")
_TOKEN_VALUE_RE = re.compile(r"^[#a-zA-Z0-9(),.%\s/-]{1,80}$")
_ASSET_KEYS = ("logo_url", "logo_alt", "favicon_url", "icon_url", "theme_color")
_LINK_KEYS = ("home_url", "contact_url", "contact_label", "contact_telegram", "privacy_url", "support_url")


@dataclass(frozen=True)
class Showcase:
    tenant_id: str
    showcase_id: str
    is_default: bool
    hosts: tuple[str, ...]
    owner_actor_id: str | None
    brand_name: str
    academy_title: str
    theme: dict[str, str] = field(default_factory=dict)
    assets: dict[str, str] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)
    telegram_binding_id: str | None = None
    max_binding_id: str | None = None
    more_mode: str = "none"
    # Слать ли автору витрины уведомления о домашках (V32; у ALMIRA выключено: сама заходит).
    author_notify: bool = True

    @property
    def base_url(self) -> str:
        """Куда ведут ссылки бота и уведомлений: первый адрес витрины."""
        if self.hosts:
            return f"https://{self.hosts[0]}"
        return str(get_settings().platform_academy_site_base or "https://wwc.best").rstrip("/")


def default_showcase(tenant_id: str) -> Showcase:
    """Витрина платформы, если строки в базе нет (миграция V29 её заводит)."""
    return Showcase(
        tenant_id=tenant_id,
        showcase_id=DEFAULT_ID,
        is_default=True,
        hosts=(),
        owner_actor_id=None,
        brand_name="WWC",
        academy_title="Академия WWC",
    )


def _clean_theme(raw: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (raw or {}).items() if isinstance(raw, dict) else ():
        text = str(value or "").strip()
        if _TOKEN_RE.match(str(key)) and _TOKEN_VALUE_RE.match(text):
            out[str(key)] = text
    return out


def _clean_strings(raw: Any, keys: tuple[str, ...]) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key in keys:
        value = str(raw.get(key) or "").strip()
        if value and len(value) <= 300 and not any(ch in value for ch in "<>\"'\\"):
            out[key] = value
    return out


def _from_row(row: dict[str, Any]) -> Showcase:
    hosts = tuple(normalize_host(h) for h in (row.get("hosts") or []) if normalize_host(h))
    more = str(row.get("more_mode") or "none")
    return Showcase(
        tenant_id=str(row["tenant_id"]),
        showcase_id=str(row["showcase_id"]),
        is_default=bool(row.get("is_default")),
        hosts=hosts,
        owner_actor_id=str(row["owner_actor_id"]) if row.get("owner_actor_id") else None,
        brand_name=str(row.get("brand_name") or ""),
        academy_title=str(row.get("academy_title") or ""),
        theme=_clean_theme(row.get("theme")),
        assets=_clean_strings(row.get("assets"), _ASSET_KEYS),
        links=_clean_strings(row.get("links"), _LINK_KEYS),
        telegram_binding_id=str(row["telegram_binding_id"]) if row.get("telegram_binding_id") else None,
        max_binding_id=str(row["max_binding_id"]) if row.get("max_binding_id") else None,
        more_mode=more if more in MORE_MODES else "none",
        author_notify=bool(row.get("author_notify", True)),
    )


# tenant_id → (когда загружено, витрины). Одна запись на тенант, обновление по TTL.
_cache: dict[str, tuple[float, list[Showcase]]] = {}


def _reset_cache() -> None:
    """Тесты: заново прочитать витрины из базы."""
    _cache.clear()


async def load_showcases(tenant_id: str, *, fresh: bool = False) -> list[Showcase]:
    cached = _cache.get(tenant_id)
    if cached and not fresh and time.monotonic() - cached[0] < CACHE_TTL_SEC:
        return cached[1]
    try:
        async with tenant_connection(tenant_id) as conn:
            rows = await fetch_all(
                conn,
                """
                select tenant_id, showcase_id, is_default, hosts, owner_actor_id, brand_name, academy_title,
                       theme, assets, links, telegram_binding_id, max_binding_id, more_mode, author_notify
                from academy_showcases
                where tenant_id = %s and status = 'active'
                order by is_default desc, showcase_id
                """,
                (tenant_id,),
            )
    except Exception:
        # Витрины недоступны — прежний список (или пусто): вход на wwc.best работает и так.
        logger.warning("academy_showcases_load_failed", exc_info=True, extra={"tenant_id": tenant_id})
        return cached[1] if cached else []
    showcases = [_from_row(row) for row in rows]
    _cache[tenant_id] = (time.monotonic(), showcases)
    return showcases


async def warm_hosts(tenant_id: str) -> None:
    """Перед синхронной проверкой адреса (``is_showcase_host``)."""
    await load_showcases(tenant_id)


def is_showcase_host(host: str | None) -> bool:
    value = normalize_host(host)
    if not value:
        return False
    return any(value in showcase.hosts for _, showcases in _cache.values() for showcase in showcases)


async def showcase_default(tenant_id: str) -> Showcase:
    for showcase in await load_showcases(tenant_id):
        if showcase.is_default:
            return showcase
    return default_showcase(tenant_id)


async def showcase_by_id(tenant_id: str, showcase_id: str | None) -> Showcase | None:
    wanted = str(showcase_id or "").strip().lower()
    if not _ID_RE.match(wanted):
        return None
    return next((s for s in await load_showcases(tenant_id) if s.showcase_id == wanted), None)


async def showcase_for_host(tenant_id: str, host: str | None) -> Showcase:
    value = normalize_host(host)
    if value:
        for showcase in await load_showcases(tenant_id):
            if value in showcase.hosts:
                return showcase
    return await showcase_default(tenant_id)


def request_host(request: Any) -> str:
    headers = getattr(request, "headers", None) or {}
    raw = headers.get(SHOWCASE_HEADER) or headers.get("x-forwarded-host") or headers.get("host") or ""
    return normalize_host(str(raw).split(",", 1)[0])


async def showcase_for_request(request: Any, tenant_id: str, *, allow_preview: bool = False) -> Showcase:
    """Витрина хоста запроса. ``allow_preview`` — ещё и ``?showcase=<id>`` (только вид и порядок)."""
    if allow_preview:
        wanted = (getattr(request, "query_params", None) or {}).get(SHOWCASE_QUERY)
        preview = await showcase_by_id(tenant_id, wanted) if wanted else None
        if preview is not None:
            return preview
    return await showcase_for_host(tenant_id, request_host(request))


async def showcase_for_binding(tenant_id: str, binding_id: str | None) -> Showcase | None:
    """Витрина, чей это бот. None — бот не принадлежит ни одной витрине автора."""
    if not binding_id:
        return None
    for showcase in await load_showcases(tenant_id):
        if not showcase.is_default and showcase.telegram_binding_id == binding_id:
            return showcase
    return None


async def showcase_for_author(tenant_id: str, author_actor_id: str | None) -> Showcase:
    """Витрина автора курса; курс платформы или автора без витрины — витрина по умолчанию."""
    if author_actor_id:
        for showcase in await load_showcases(tenant_id):
            if showcase.owner_actor_id and showcase.owner_actor_id == str(author_actor_id):
                return showcase
    return await showcase_default(tenant_id)


async def showcase_for_course(tenant_id: str, course_slug: str | None) -> Showcase:
    author = None
    if course_slug:
        try:
            async with tenant_connection(tenant_id) as conn:
                row = await fetch_one(
                    conn,
                    "select author_actor_id from academy_courses where tenant_id = %s and slug = %s",
                    (tenant_id, str(course_slug)),
                )
            author = (row or {}).get("author_actor_id")
        except Exception:
            logger.warning("academy_showcase_course_lookup_failed", exc_info=True)
    return await showcase_for_author(tenant_id, author)


def owner_actor_ids(showcases: list[Showcase]) -> set[str]:
    """Авторы со своей витриной: их курсы на витрине платформы — «чужие»."""
    return {s.owner_actor_id for s in showcases if s.owner_actor_id}


def is_own_course(showcase: Showcase, author_actor_id: str | None, partner_owners: set[str]) -> bool:
    """Свой курс витрины: курс её автора; у витрины платформы — курс без своей витрины."""
    author = str(author_actor_id or "")
    if showcase.owner_actor_id:
        return author == showcase.owner_actor_id
    return author not in partner_owners


async def partner_owner(conn: Any, tenant_id: str, actor_ids: list[str]) -> bool:
    """Автор-партнёр: у человека своя активная витрина — «Академия автора» без оплаты."""
    if not actor_ids:
        return False
    row = await fetch_one(
        conn,
        """
        select 1 as ok from academy_showcases
        where tenant_id = %s and status = 'active' and owner_actor_id = any(%s::text[])
        limit 1
        """,
        (tenant_id, list(actor_ids)),
    )
    return bool(row)


async def binding_username(binding_id: str | None) -> str | None:
    """@имя бота витрины (без @) из tenant_bot_bindings; None — бот не найден."""
    if not binding_id:
        return None
    from app.db import get_pool

    try:
        pool = get_pool()
        async with pool.connection(timeout=get_settings().database_timeout_sec) as conn:
            row = await fetch_one(
                conn,
                """
                select to_jsonb(b)->>'bot_username' as bot_username
                from tenant_bot_bindings b
                where b.binding_id = %s and b.status = 'active'
                """,
                (binding_id,),
            )
    except Exception:
        logger.warning("academy_showcase_bot_lookup_failed", exc_info=True)
        return None
    username = str((row or {}).get("bot_username") or "").lstrip("@").strip()
    return username or None


async def login_bot_username(showcase: Showcase) -> str | None:
    """Бот входа на витрине: её бот, иначе бот процесса."""
    return await binding_username(showcase.telegram_binding_id) or (
        str(get_settings().telegram_bot_username or "").lstrip("@") or None
    )


async def public_payload(showcase: Showcase) -> dict[str, Any]:
    bot = await login_bot_username(showcase)
    return {
        "id": showcase.showcase_id,
        "is_default": showcase.is_default,
        "brand_name": showcase.brand_name,
        "academy_title": showcase.academy_title,
        "theme": dict(showcase.theme),
        "assets": dict(showcase.assets),
        "links": dict(showcase.links),
        "bot_username": bot,
        "bot_url": f"https://t.me/{bot}" if bot else None,
        "more_mode": showcase.more_mode,
    }
