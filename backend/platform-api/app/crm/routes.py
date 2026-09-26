"""Site API of the partner diary: /api/v1/content-access/crm/...

Mounted under the content-access prefix like the Academy: the site nginx
already proxies it to Core, no server change is needed. The person is the
content-access session (Telegram sign-in on the site). Access: tenant
entitlement ``crm`` → session → pilot list → paid PRO (preview admins pass).
Every response, errors included, is ``Cache-Control: private, no-store``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.content_access.routes import _current_session
from app.crm.service import (
    BULK_LIMIT,
    CrmError,
    add_lead_card_for_public_id,
    add_note,
    bulk_create_contacts,
    create_contact,
    delete_contact,
    delete_note,
    export_csv,
    get_contact,
    get_or_create_account,
    list_contacts,
    load_viewer,
    lock_reason,
    safe_error,
    set_timezone,
    today_view,
    update_contact,
)
from app.errors import http_exception_handler
from app.internal_auth import InternalSecretHeader, require_internal_secret
from app.settings import get_settings
from app.tenancy import get_request_tenant, require_entitlement

PREFIX = "/api/v1/content-access/crm"
NO_STORE = "private, no-store"

logger = logging.getLogger(__name__)


class _PrivateRoute(APIRoute):
    """Errors raised in dependencies and handlers get the same no-store header.

    Data the database refuses (a CHECK, a too long value) is a 400, not a 500:
    a 500 would put the psycopg message — the person's name and phone — into
    the traceback. Only the class and SQLSTATE are logged.
    """

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
                logger.warning("crm_invalid_input", extra={"path": self.path, **safe_error(exc)})
                response = await http_exception_handler(
                    request, HTTPException(status_code=400, detail={"error": "invalid_input"})
                )
            response.headers["Cache-Control"] = NO_STORE
            return response

        return handler


router = APIRouter(tags=["crm"], route_class=_PrivateRoute)


@dataclass(frozen=True)
class CrmContext:
    tenant_id: str
    account: dict[str, Any]
    telegram_user_id: int


def _raise(exc: CrmError) -> None:
    raise HTTPException(status_code=exc.status, detail={"error": exc.code, **exc.extra}) from exc


async def _context(request: Request) -> CrmContext:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "crm")
    session = await _current_session(request)
    telegram_user_id = session.get("telegram_user_id")
    if telegram_user_id is None:
        raise HTTPException(status_code=401, detail={"error": "telegram_session_required"})
    viewer = await load_viewer(tenant.tenant_id, int(telegram_user_id))
    reason = lock_reason(viewer)
    if reason == "pro_required":
        raise HTTPException(status_code=402, detail={"error": "pro_required"})
    if reason is not None:
        raise HTTPException(status_code=403, detail={"error": reason})
    try:
        account = await get_or_create_account(tenant.tenant_id, int(telegram_user_id))
    except CrmError as exc:
        _raise(exc)
    return CrmContext(tenant_id=tenant.tenant_id, account=account, telegram_user_id=int(telegram_user_id))


class TimezoneBody(BaseModel):
    timezone: str


class ContactCreateBody(BaseModel):
    name: str = Field(max_length=500)
    phone: str | None = Field(default=None, max_length=200)
    source: str | None = Field(default=None, max_length=500)


class ContactsBulkBody(BaseModel):
    contacts: list[ContactCreateBody]


class ContactPatchBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=200)
    source: str | None = Field(default=None, max_length=500)
    status: str | None = None
    next_step: str | None = None
    next_at: date | None = None
    meeting_at: datetime | None = None


class NoteBody(BaseModel):
    body: str = Field(max_length=8000)


def _me(ctx: CrmContext) -> dict[str, Any]:
    return {
        "ok": True,
        "account_id": ctx.account["account_id"],
        "timezone": ctx.account["timezone"],
        "today": ctx.account["today"].isoformat(),
        "pilot": get_settings().parsed_crm_pilot() is not None,
    }


@router.get(f"{PREFIX}/me")
async def crm_me(request: Request) -> dict[str, Any]:
    return _me(await _context(request))


@router.patch(f"{PREFIX}/me")
async def crm_me_update(body: TimezoneBody, request: Request) -> dict[str, Any]:
    ctx = await _context(request)
    try:
        account = await set_timezone(ctx.tenant_id, ctx.account, body.timezone)
    except CrmError as exc:
        _raise(exc)
    return _me(CrmContext(ctx.tenant_id, account, ctx.telegram_user_id))


@router.get(f"{PREFIX}/today")
async def crm_today(request: Request) -> dict[str, Any]:
    ctx = await _context(request)
    return {"ok": True, **await today_view(ctx.tenant_id, ctx.account)}


@router.get(f"{PREFIX}/contacts")
async def crm_contacts(request: Request, q: str | None = None, status: str | None = None) -> dict[str, Any]:
    ctx = await _context(request)
    try:
        contacts = await list_contacts(ctx.tenant_id, ctx.account, q=q, status=status)
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, "contacts": contacts}


@router.post(f"{PREFIX}/contacts", status_code=201)
async def crm_contact_create(body: ContactCreateBody, request: Request) -> dict[str, Any]:
    ctx = await _context(request)
    try:
        contact = await create_contact(ctx.tenant_id, ctx.account, name=body.name, phone=body.phone, source=body.source)
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, "contact": contact}


@router.post(f"{PREFIX}/contacts/bulk", status_code=201)
async def crm_contacts_bulk(body: ContactsBulkBody, request: Request) -> dict[str, Any]:
    """Import from the phone book, a .vcf or a .csv — up to BULK_LIMIT rows in one
    transaction; duplicates by number and rows without a name come back in
    ``skipped`` with a reason, nothing is written twice."""
    ctx = await _context(request)
    if not body.contacts:
        raise HTTPException(status_code=400, detail={"error": "contacts_required"})
    if len(body.contacts) > BULK_LIMIT:
        raise HTTPException(status_code=400, detail={"error": "too_many_contacts", "limit": BULK_LIMIT})
    try:
        result = await bulk_create_contacts(ctx.tenant_id, ctx.account, [item.model_dump() for item in body.contacts])
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, **result}


@router.get(f"{PREFIX}/export.csv")
async def crm_export(request: Request) -> Response:
    ctx = await _context(request)
    body = await export_csv(ctx.tenant_id, ctx.account)
    filename = f"wwc-contacts-{ctx.account['today'].isoformat()}.csv"
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(f"{PREFIX}/contacts/{{contact_id}}")
async def crm_contact_get(contact_id: str, request: Request) -> dict[str, Any]:
    ctx = await _context(request)
    try:
        contact = await get_contact(ctx.tenant_id, ctx.account, contact_id)
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, "contact": contact}


@router.patch(f"{PREFIX}/contacts/{{contact_id}}")
async def crm_contact_patch(contact_id: str, body: ContactPatchBody, request: Request) -> dict[str, Any]:
    ctx = await _context(request)
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    try:
        contact = await update_contact(ctx.tenant_id, ctx.account, contact_id, changes)
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, "contact": contact}


@router.delete(f"{PREFIX}/contacts/{{contact_id}}", status_code=204)
async def crm_contact_delete(contact_id: str, request: Request) -> Response:
    ctx = await _context(request)
    try:
        await delete_contact(ctx.tenant_id, ctx.account, contact_id)
    except CrmError as exc:
        _raise(exc)
    return Response(status_code=204)


@router.post(f"{PREFIX}/contacts/{{contact_id}}/notes", status_code=201)
async def crm_note_create(contact_id: str, body: NoteBody, request: Request) -> dict[str, Any]:
    ctx = await _context(request)
    try:
        note = await add_note(ctx.tenant_id, ctx.account, contact_id, body.body)
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, "note": note}


@router.delete(f"{PREFIX}/contacts/{{contact_id}}/notes/{{note_id}}", status_code=204)
async def crm_note_delete(contact_id: str, note_id: str, request: Request) -> Response:
    ctx = await _context(request)
    try:
        await delete_note(ctx.tenant_id, ctx.account, contact_id, note_id)
    except CrmError as exc:
        _raise(exc)
    return Response(status_code=204)


@router.post("/api/v1/leads/{public_id}/crm-card")
async def lead_crm_card_internal(
    public_id: str,
    request: Request,
    response: Response,
    internal_secret: InternalSecretHeader = None,
) -> dict[str, Any]:
    """Сервер-сервер: n8n после записи заявки просит карточку в ежедневнике
    владельца. Публично не проксируется (на сайте location = /api/v1/leads —
    точное совпадение), доступ только с секретом PLATFORM_INTERNAL_API_SECRET;
    тенант — по X-Forwarded-Host, который n8n передаёт явно."""
    response.headers["Cache-Control"] = NO_STORE
    require_internal_secret(internal_secret)
    tenant = get_request_tenant(request)
    return await add_lead_card_for_public_id(tenant.tenant_id, public_id)
