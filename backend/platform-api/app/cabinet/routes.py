"""Сайт: личный кабинет партнёра — /api/v1/content-access/me/… (ТЗ 02.10.2026, §4).

Под префиксом content-access, как WWC CRM и Академия: nginx сайта уже
проксирует его в Core. Каждый маршрут есть и без «/api» — staging-nginx
(location /api/ → Core /) отрезает префикс. Человек — сессия content-access
(вход через Telegram на сайте); без неё 401. Ответы и ошибки — ``private,
no-store``; исключение — публичное фото ``/partner-media/<id>.jpg``.

Контракт полей — в PROCESS/wwc-cabinet-v1-20261002/STATE_core.md («Контракт»).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

import psycopg
from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.datastructures import UploadFile

from app.cabinet.photos import MAX_UPLOAD_BYTES, PhotoError, process_profile_photo
from app.cabinet.profile import ProfileValidationError
from app.cabinet.service import (
    CabinetError,
    CabinetPerson,
    cancel_pending_request,
    check_upload_allowed,
    get_pending_request,
    load_journey,
    load_overview,
    load_own_media,
    load_person,
    load_public_media,
    mark_journey_step,
    request_out,
    safe_error,
    store_profile_photo,
    submit_profile_request,
    update_settings,
)
from app.content_access.routes import _current_session
from app.errors import http_exception_handler
from app.referral_bonus.service import (
    InvalidPageCursorError,
    bonus_entry_label,
    list_bonus_ledger,
    list_referrals,
    referral_counts,
    referral_site_state,
    referral_status_label,
)
from app.settings import get_settings
from app.tenancy import TenantContext, get_request_tenant, require_entitlement

logger = logging.getLogger(__name__)

API = "/api/v1/content-access/me"
V1 = "/v1/content-access/me"
NO_STORE = "private, no-store"
MEDIA_CACHE = "public, max-age=31536000, immutable"
# multipart поверх 20 МБ файла: заголовки частей и граница
UPLOAD_OVERHEAD = 64 * 1024
# Разбор фото — до ~100 МБ памяти на файл: одновременно не больше двух на процесс
# (тот же контейнер обслуживает бота).
_PHOTO_SLOTS = asyncio.Semaphore(2)


class _PrivateRoute(APIRoute):
    """Ошибки в зависимостях и обработчиках — тоже no-store; отказ базы на
    данные (CHECK, длина) — 400, а не 500 с текстом psycopg в трейсе."""

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                response = await original(request)
            except HTTPException as exc:
                response = await http_exception_handler(request, exc)
            except RequestValidationError as exc:
                response = await request_validation_exception_handler(request, exc)
            except (psycopg.errors.IntegrityError, psycopg.errors.DataError) as exc:
                logger.warning("cabinet_invalid_input", extra={"path": self.path, **safe_error(exc)})
                response = await http_exception_handler(
                    request, HTTPException(status_code=400, detail={"error": "invalid_input"})
                )
            response.headers["Cache-Control"] = NO_STORE
            return response

        return handler


router = APIRouter(tags=["cabinet"], route_class=_PrivateRoute)
media_router = APIRouter(tags=["cabinet"])


def _raise(exc: Exception) -> None:
    if isinstance(exc, CabinetError):
        raise HTTPException(status_code=exc.status, detail={"error": exc.code, **exc.extra}) from exc
    if isinstance(exc, ProfileValidationError):
        detail = {"error": exc.code}
        if exc.field:
            detail["field"] = exc.field
        raise HTTPException(status_code=400, detail=detail) from exc
    if isinstance(exc, PhotoError):
        raise HTTPException(status_code=exc.status, detail={"error": exc.code}) from exc
    if isinstance(exc, InvalidPageCursorError):
        raise HTTPException(status_code=400, detail={"error": "invalid_cursor"}) from exc
    raise exc


async def _context(request: Request) -> tuple[TenantContext, CabinetPerson]:
    tenant = get_request_tenant(request)
    session = await _current_session(request)
    telegram_user_id = session.get("telegram_user_id")
    if telegram_user_id is None:
        raise HTTPException(status_code=401, detail={"error": "telegram_session_required"})
    try:
        person = await load_person(tenant.tenant_id, int(telegram_user_id))
    except CabinetError as exc:
        _raise(exc)
    return tenant, person


def _bot_username() -> str | None:
    return get_settings().telegram_bot_username


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


# ---- главная, путь ---------------------------------------------------------------------


@router.get(f"{API}/overview")
@router.get(f"{V1}/overview")
async def cabinet_overview(request: Request) -> dict[str, Any]:
    tenant, person = await _context(request)
    return await load_overview(
        tenant.tenant_id,
        person,
        bot_username=_bot_username(),
        crm_entitled=bool(tenant.entitlements.get("crm")),
    )


@router.get(f"{API}/journey")
@router.get(f"{V1}/journey")
async def cabinet_journey(request: Request) -> dict[str, Any]:
    tenant, person = await _context(request)
    return await load_journey(tenant.tenant_id, person, crm_entitled=bool(tenant.entitlements.get("crm")))


@router.post(f"{API}/journey/{{step}}/done")
@router.post(f"{V1}/journey/{{step}}/done")
async def cabinet_journey_done(step: str, request: Request) -> dict[str, Any]:
    tenant, person = await _context(request)
    try:
        return await mark_journey_step(tenant.tenant_id, person, step)
    except CabinetError as exc:
        _raise(exc)


@router.post(f"{API}/invite/share")
@router.post(f"{V1}/invite/share")
async def cabinet_invite_shared(request: Request) -> dict[str, Any]:
    """Скопировал или поделился приглашением — шаг «первое приглашение»."""
    tenant, person = await _context(request)
    return await mark_journey_step(tenant.tenant_id, person, "invite_sent")


# ---- партнёры и баланс -------------------------------------------------------------------


def _referral_out(entry: dict[str, Any]) -> dict[str, Any]:
    username = str(entry.get("telegram_username") or "").strip().lstrip("@") or None
    return {
        "display_name": entry.get("display_name"),
        "telegram_username": username,
        "attributed_at": _iso(entry.get("attributed_at")),
        "site_state": referral_site_state(entry),
        "status_label": referral_status_label(entry),
        "subscription_status": entry.get("subscription_status"),
    }


@router.get(f"{API}/referrals")
@router.get(f"{V1}/referrals")
async def cabinet_referrals(request: Request, cursor: str | None = None, limit: int = 20) -> dict[str, Any]:
    tenant, person = await _context(request)
    try:
        page = await list_referrals(tenant.tenant_id, person.actor_id, limit=limit, cursor=cursor)
    except InvalidPageCursorError as exc:
        _raise(exc)
    counts = await referral_counts(tenant.tenant_id, person.actor_id)
    return {
        "ok": True,
        "items": [_referral_out(entry) for entry in page["items"]],
        "next_cursor": page["next_cursor"],
        "counts": {"invited": counts["invited_count"], "paid": counts["paid_count"]},
    }


def _ledger_out(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_id": str(entry["entry_id"]),
        "created_at": _iso(entry.get("created_at")),
        "amount_minor": int(entry["amount_minor"]),
        "currency": "WWC$",
        "entry_type": entry.get("entry_type"),
        "label": bonus_entry_label(entry),
    }


@router.get(f"{API}/bonus-ledger")
@router.get(f"{V1}/bonus-ledger")
async def cabinet_bonus_ledger(request: Request, cursor: str | None = None, limit: int = 20) -> dict[str, Any]:
    tenant, person = await _context(request)
    try:
        page = await list_bonus_ledger(tenant.tenant_id, person.actor_id, limit=limit, cursor=cursor)
    except InvalidPageCursorError as exc:
        _raise(exc)
    from app.cabinet.service import bonus_balance_and_rules

    summary = await bonus_balance_and_rules(tenant.tenant_id, person.actor_id)
    return {
        "ok": True,
        "balance": {"currency": "WWC$", "amount_minor": summary["balance_minor"]},
        "rules": summary["rules"],
        "items": [_ledger_out(entry) for entry in page["items"]],
        "next_cursor": page["next_cursor"],
    }


# ---- профиль сайта -----------------------------------------------------------------------


@router.post(f"{API}/profile")
@router.post(f"{V1}/profile")
async def cabinet_profile_submit(
    request: Request, background: BackgroundTasks, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    """Заявка «На проверке у Виктора». Карточка владельцу уходит в фоне, после
    ответа: медленный Telegram не держит сохранение на сайте."""
    from app.telegram.cabinet_profile import notify_owner_about_profile_request, retire_owner_card

    tenant, person = await _context(request)
    try:
        result = await submit_profile_request(tenant.tenant_id, person, body)
    except (CabinetError, ProfileValidationError) as exc:
        _raise(exc)
    pending = result.get("pending")
    replaced = result.get("replaced_request_id")
    if pending:
        background.add_task(
            notify_owner_about_profile_request, tenant.tenant_id, pending["request_id"], replaced_request_id=replaced
        )
    elif replaced:
        background.add_task(retire_owner_card, tenant.tenant_id, replaced)
    return {"ok": True, "status": result["status"], "pending": request_out(pending)}


@router.get(f"{API}/profile/pending")
@router.get(f"{V1}/profile/pending")
async def cabinet_profile_pending(request: Request) -> dict[str, Any]:
    tenant, person = await _context(request)
    return {"ok": True, **await get_pending_request(tenant.tenant_id, person)}


@router.delete(f"{API}/profile/pending")
@router.delete(f"{V1}/profile/pending")
async def cabinet_profile_cancel(request: Request, background: BackgroundTasks) -> dict[str, Any]:
    from app.telegram.cabinet_profile import retire_owner_card

    tenant, person = await _context(request)
    cancelled = await cancel_pending_request(tenant.tenant_id, person)
    if cancelled:
        background.add_task(retire_owner_card, tenant.tenant_id, cancelled)
    return {"ok": True, "cancelled": cancelled is not None}


async def _read_upload(request: Request) -> bytes:
    """Файл из multipart (поле photo или file) или тело image/* целиком; не больше 20 МБ."""
    content_type = str(request.headers.get("content-type") or "").lower()
    length = request.headers.get("content-length")
    if length is not None:
        try:
            declared = int(length)
        except ValueError as exc:
            raise PhotoError("photo_unreadable", 400) from exc
        if declared > MAX_UPLOAD_BYTES + UPLOAD_OVERHEAD:
            raise PhotoError("photo_too_large", 413)
    if content_type.startswith("multipart/form-data"):
        if length is None:  # без длины Starlette сложил бы на диск всё, что пришлют
            raise PhotoError("length_required", 411)
        form = await request.form(max_files=1, max_fields=4)
        try:
            upload = form.get("photo") or form.get("file")
            if not isinstance(upload, UploadFile):
                raise PhotoError("photo_missing", 400)
            data = await upload.read(MAX_UPLOAD_BYTES + 1)
        finally:
            await form.close()
    elif content_type.startswith("image/") or content_type.startswith("application/octet-stream"):
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise PhotoError("photo_too_large", 413)
            chunks.append(chunk)
        data = b"".join(chunks)
    else:
        raise PhotoError("photo_type_unsupported", 415)
    if len(data) > MAX_UPLOAD_BYTES:
        raise PhotoError("photo_too_large", 413)
    return data


@router.post(f"{API}/profile/photo")
@router.post(f"{V1}/profile/photo")
async def cabinet_profile_photo(request: Request) -> dict[str, Any]:
    """Фото для заявки: уменьшенный JPEG без EXIF; адрес идёт в POST /me/profile."""
    tenant, person = await _context(request)
    try:
        await check_upload_allowed(tenant.tenant_id, person)  # до чтения и разбора файла
        data = await _read_upload(request)
        async with _PHOTO_SLOTS:
            photo = await run_in_threadpool(process_profile_photo, data)
        stored = await store_profile_photo(tenant.tenant_id, person, photo)
    except (CabinetError, PhotoError) as exc:
        _raise(exc)
    return {"ok": True, **stored}


@router.get(f"{API}/profile/photo/{{name}}")
@router.get(f"{V1}/profile/photo/{{name}}")
async def cabinet_profile_photo_preview(name: str, request: Request) -> Response:
    """Своё фото до «Применить» — только самому партнёру (сессия), без кэша."""
    tenant, person = await _context(request)
    media_id = name[:-4] if name.endswith(".jpg") else ""
    body = await load_own_media(tenant.tenant_id, person, media_id) if media_id else None
    if body is None:
        raise HTTPException(status_code=404, detail={"error": "media_not_found"})
    return Response(content=body, media_type="image/jpeg", headers={"X-Content-Type-Options": "nosniff"})


# ---- настройки ---------------------------------------------------------------------------


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str | None = Field(default=None, max_length=64)
    marketing_opt_in: bool | None = None


@router.patch(f"{API}/settings")
@router.patch(f"{V1}/settings")
async def cabinet_settings(body: SettingsBody, request: Request) -> dict[str, Any]:
    tenant, person = await _context(request)
    try:
        settings = await update_settings(
            tenant.tenant_id, person, timezone_name=body.timezone, marketing_opt_in=body.marketing_opt_in
        )
    except CabinetError as exc:
        _raise(exc)
    return {"ok": True, "settings": settings}


# ---- публичное фото ------------------------------------------------------------------------


@media_router.get("/api/v1/content-access/partner-media/{name}")
@media_router.get("/v1/content-access/partner-media/{name}")
async def partner_media(name: str, request: Request) -> Response:
    """Фото профиля, которое стоит на сайте (после «Применить»). Адрес
    неугадываемый (uuid) и неизменный — кэш на год."""
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    media_id = name[:-4] if name.endswith(".jpg") else ""
    body = await load_public_media(tenant.tenant_id, media_id) if media_id else None
    if body is None:
        raise HTTPException(status_code=404, detail={"error": "media_not_found"})
    return Response(
        content=body,
        media_type="image/jpeg",
        headers={"Cache-Control": MEDIA_CACHE, "X-Content-Type-Options": "nosniff"},
    )
