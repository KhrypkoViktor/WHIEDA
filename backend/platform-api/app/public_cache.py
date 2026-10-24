"""Публичные ответы, одинаковые для всех посетителей хоста: кэш в памяти и заголовки (24.10.2026).

``/public/ref/{ref}`` и ``/theme-access/public`` не зависят ни от cookie, ни от входа: ответ определяют
хост (арендатор) и параметр запроса. Поэтому:

* строка из базы держится в памяти процесса ``PUBLIC_CACHE_TTL_SEC`` секунд — ключ (арендатор, …);
* успешный ответ уходит с ``Cache-Control: public, max-age=120``, чтобы браузер не просил его заново;
* любая ошибка (404, 403, 501) уходит с ``Cache-Control: no-store`` и в кэш не попадает.

Кто меняет публичный профиль в этом процессе (сохранение темы), вызывает ``forget_public_profiles()``;
изменения из других процессов (синхронизация реестра партнёров, кабинет в другом контейнере) видны через
срок жизни записи.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.errors import http_exception_handler
from app.ttl_cache import TtlCache

PUBLIC_CACHE_TTL_SEC = 60.0
PUBLIC_CACHE_MAX_ENTRIES = 1024
PUBLIC_CACHE_CONTROL = "public, max-age=120"
NO_STORE = "no-store"

# (арендатор, "ref" | "subdomain", нормализованный код) → строка referral_profiles
public_ref_cache: TtlCache[dict[str, Any]] = TtlCache(max_entries=PUBLIC_CACHE_MAX_ENTRIES)
# (арендатор, нормализованный site_id) → строка сайта для темы
theme_site_cache: TtlCache[dict[str, Any]] = TtlCache(max_entries=PUBLIC_CACHE_MAX_ENTRIES)


def forget_public_profiles() -> None:
    """Публичный профиль или тема изменились: следующий запрос прочитает базу заново."""
    public_ref_cache.clear()
    theme_site_cache.clear()


def mark_public(response: Response) -> None:
    response.headers["Cache-Control"] = PUBLIC_CACHE_CONTROL


async def no_store_error(request: Request, exc: HTTPException) -> JSONResponse:
    """Тот же ответ об ошибке, что и у приложения (``app.errors``), но с ``Cache-Control: no-store``.

    Общий обработчик HTTPException заголовки исключения не передаёт, поэтому, как в кабинете и CRM,
    ответ собирается здесь."""
    response = await http_exception_handler(request, exc)
    response.headers["Cache-Control"] = NO_STORE
    return response
