"""Публичный список ближайших встреч для сайта (V43, 30.10.2026).

``GET /api/v1/public/meetings?kind=live&limit=2`` →
``{"meetings": [{"id", "title", "starts_at", "duration_min", "hosts"}]}``.

Без входа и без cookie: ответ определяют хост (арендатор) и параметры запроса. ``starts_at`` — ISO 8601
со смещением Москвы (``2026-11-05T19:30:00+03:00``). Ссылка входа (``join_url``), запись и заметки наружу
не отдаются никогда (``app.meetings.service.public_meeting``). ``limit`` от 1 до 10 (больше — берётся 10).
Успех — ``Cache-Control: public, max-age=60``; ошибка — ``no-store``.

Маршруты на ``/api/v1/…`` (боевой nginx проксирует его) и ``/v1/…`` (staging-nginx срезает ``/api``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from app.meetings.service import KINDS, PUBLIC_LIMIT_MAX, load_public_meetings
from app.public_cache import no_store_error
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["meetings"])

PUBLIC_CACHE = "public, max-age=60"
DEFAULT_LIMIT = 3


async def public_meetings(
    request: Request, response: Response, kind: str = "live", limit: int = DEFAULT_LIMIT
) -> Any:
    try:
        tenant = get_request_tenant(request)
        require_entitlement(tenant, "structure_basic")
        # Публично — только живые встречи; консультации (kind=consult) на сайте не показываем (лид, 30.10.2026).
        if kind not in KINDS or kind != "live":
            raise HTTPException(status_code=422, detail={"ok": False, "error": "bad_kind"})
        meetings = await load_public_meetings(tenant.tenant_id, kind)
    except HTTPException as exc:
        return await no_store_error(request, exc)
    response.headers["Cache-Control"] = PUBLIC_CACHE
    return {"meetings": meetings[: max(1, min(int(limit), PUBLIC_LIMIT_MAX))]}


for _path in ("/api/v1/public/meetings", "/v1/public/meetings"):
    router.add_api_route(_path, public_meetings, methods=["GET"])
