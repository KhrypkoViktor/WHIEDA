"""Site API of WWC CRM: /api/v1/content-access/crm/...

Mounted under the content-access prefix like the Academy: the site nginx
already proxies it to Core, no server change is needed. The person is the
content-access session (Telegram sign-in on the site). Access: tenant
entitlement ``crm`` → session → pilot list → paid PRO (preview admins pass).
Every response, errors included, is ``Cache-Control: private, no-store``.

CRM v2 (TASK.md раздел 5) adds cursors and server search to /contacts, the
pipeline, a card's history, «call / message» logging, «Перенести», «Сделано»,
tags and priority, message templates, soft delete with «Вернуть» and the
install-banner flag. v1 answers keep their keys: the v1 site keeps working
until the v2 app ships (GET /contacts without limit/cursor/sort/tag returns
up to 300 people and the old ``contacts`` key too).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Annotated, Any, Callable

import psycopg
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.content_access.routes import _current_session
from app.crm.paging import DEFAULT_LIMIT, LEGACY_LIMIT
from app.crm.queries import list_activities, list_contacts, list_tags, pipeline, pipeline_column
from app.crm.service import (
    BULK_LIMIT,
    CrmError,
    add_lead_card_for_public_id,
    add_note,
    bulk_create_contacts,
    create_contact,
    delete_contact,
    delete_note,
    done_contact,
    export_csv,
    get_contact,
    get_or_create_account,
    load_viewer,
    lock_reason,
    log_contact,
    restore_contact,
    safe_error,
    set_timezone,
    snooze_contact,
    today_view,
    update_account,
    update_contact,
    viewer_display_name,
)
from app.crm.templates import create_template, delete_template, list_templates, update_template
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


class _NoQueryInAccessLog(logging.Filter):
    """uvicorn's access log prints the request line with its query string, and a
    CRM search (``?q=``) is a person's name or phone. CRM lines keep the path only."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            path = args[2]
            if isinstance(path, str) and path.startswith(PREFIX) and "?" in path:
                record.args = (*args[:2], path.split("?", 1)[0] + "?[redacted]", *args[3:])
        return True


def install_access_log_filter() -> None:
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, _NoQueryInAccessLog) for item in access.filters):
        access.addFilter(_NoQueryInAccessLog())


install_access_log_filter()


@dataclass(frozen=True)
class CrmContext:
    tenant_id: str
    account: dict[str, Any]
    telegram_user_id: int
    my_name: str = ""


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
    return CrmContext(
        tenant_id=tenant.tenant_id,
        account=account,
        telegram_user_id=int(telegram_user_id),
        my_name=viewer_display_name(viewer),
    )


Tag = Annotated[str, Field(max_length=200)]


class MeBody(BaseModel):
    timezone: str | None = None
    install_hint_dismissed: bool | None = None


class ContactCreateBody(BaseModel):
    name: str = Field(max_length=500)
    phone: str | None = Field(default=None, max_length=200)
    source: str | None = Field(default=None, max_length=500)
    tags: list[Tag] | None = Field(default=None, max_length=50)
    priority: int | None = None


class ContactsBulkBody(BaseModel):
    contacts: list[ContactCreateBody]


class ContactSearchBody(BaseModel):
    q: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, max_length=20)
    tag: str | None = Field(default=None, max_length=200)
    cursor: str | None = Field(default=None, max_length=2000)
    limit: int | None = None
    sort: str | None = Field(default=None, max_length=20)


class ContactPatchBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, max_length=500)
    phone: str | None = Field(default=None, max_length=200)
    source: str | None = Field(default=None, max_length=500)
    status: str | None = None
    next_step: str | None = None
    next_at: date | None = None
    meeting_at: datetime | None = None
    tags: list[Tag] | None = Field(default=None, max_length=50)
    priority: int | None = None


class NoteBody(BaseModel):
    body: str = Field(max_length=8000)


class LogBody(BaseModel):
    kind: str = Field(max_length=20)
    channel: str | None = Field(default=None, max_length=20)
    template_id: str | None = Field(default=None, max_length=64)


class SnoozeBody(BaseModel):
    days: int | None = None
    until: date | None = Field(default=None, validation_alias="date")


class DoneBody(BaseModel):
    meeting_at: datetime | None = None
    next_at: date | None = None


class TemplateCreateBody(BaseModel):
    title: str = Field(max_length=200)
    body: str = Field(max_length=4000)
    position: int | None = None


class TemplatePatchBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = Field(default=None, max_length=200)
    body: str | None = Field(default=None, max_length=4000)
    position: int | None = None


def _me(ctx: CrmContext) -> dict[str, Any]:
    return {
        "ok": True,
        "account_id": ctx.account["account_id"],
        "timezone": ctx.account["timezone"],
        "today": ctx.account["today"].isoformat(),
        "pilot": get_settings().parsed_crm_pilot() is not None,
        "install_hint_dismissed": bool(ctx.account.get("install_hint_dismissed")),
        # «{мое_имя}» in templates: the partner's public name, '' when the profile has none.
        "my_name": ctx.my_name,
    }


@router.get(f"{PREFIX}/me")
async def crm_me(request: Request) -> dict[str, Any]:
    return _me(await _context(request))


@router.patch(f"{PREFIX}/me")
async def crm_me_update(body: MeBody, request: Request) -> dict[str, Any]:
    ctx = await _context(request)
    try:
        if body.install_hint_dismissed is None:
            # v1 sends only {timezone}: the same checks and errors as before.
            account = await set_timezone(ctx.tenant_id, ctx.account, body.timezone)
        else:
            account = await update_account(
                ctx.tenant_id,
                ctx.account,
                timezone_name=body.timezone,
                install_hint_dismissed=body.install_hint_dismissed,
            )
    except CrmError as exc:
        _raise(exc)
    return _me(CrmContext(ctx.tenant_id, account, ctx.telegram_user_id, ctx.my_name))


@router.get(f"{PREFIX}/today")
async def crm_today(request: Request) -> dict[str, Any]:
    ctx = await _context(request)
    return {"ok": True, **await today_view(ctx.tenant_id, ctx.account)}


@router.get(f"{PREFIX}/contacts")
async def crm_contacts(
    request: Request,
    q: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    cursor: str | None = None,
    limit: int | None = None,
    sort: str | None = None,
) -> dict[str, Any]:
    """``{items, next_cursor, total}``; ``q`` — ILIKE on name, source and phone."""
    ctx = await _context(request)
    legacy = cursor is None and limit is None and sort is None and tag is None
    try:
        result = await list_contacts(
            ctx.tenant_id,
            ctx.account,
            q=q,
            status=status,
            tag=tag,
            cursor=cursor,
            limit=limit,
            sort=sort,
            default_limit=LEGACY_LIMIT if legacy else DEFAULT_LIMIT,
        )
    except CrmError as exc:
        _raise(exc)
    body = {"ok": True, **result}
    if legacy:
        body["contacts"] = result["items"]
    return body


@router.post(f"{PREFIX}/contacts/search")
async def crm_contacts_search(body: ContactSearchBody, request: Request) -> dict[str, Any]:
    """GET /contacts with the query in the body: a typed name or phone stays out of
    the URL, so out of the site's nginx access log too. Always paged (50 by default)."""
    ctx = await _context(request)
    try:
        result = await list_contacts(
            ctx.tenant_id,
            ctx.account,
            q=body.q,
            status=body.status,
            tag=body.tag,
            cursor=body.cursor,
            limit=body.limit,
            sort=body.sort,
        )
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, **result}


@router.post(f"{PREFIX}/contacts", status_code=201)
async def crm_contact_create(body: ContactCreateBody, request: Request) -> dict[str, Any]:
    ctx = await _context(request)
    extra: dict[str, Any] = {}
    if body.tags is not None:
        extra["tags"] = body.tags
    if body.priority is not None:
        extra["priority"] = body.priority
    try:
        contact = await create_contact(
            ctx.tenant_id, ctx.account, name=body.name, phone=body.phone, source=body.source, **extra
        )
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
        result = await bulk_create_contacts(
            ctx.tenant_id, ctx.account, [item.model_dump(include={"name", "phone", "source"}) for item in body.contacts]
        )
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


@router.get(f"{PREFIX}/pipeline")
async def crm_pipeline(request: Request) -> dict[str, Any]:
    """Columns by status: count + the first 20 cards each."""
    ctx = await _context(request)
    return {"ok": True, **await pipeline(ctx.tenant_id, ctx.account)}


@router.get(f"{PREFIX}/pipeline/{{status}}")
async def crm_pipeline_column(
    status: str, request: Request, cursor: str | None = None, limit: int | None = None
) -> dict[str, Any]:
    ctx = await _context(request)
    try:
        result = await pipeline_column(ctx.tenant_id, ctx.account, status, cursor=cursor, limit=limit)
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, **result}


@router.get(f"{PREFIX}/tags")
async def crm_tags(request: Request) -> dict[str, Any]:
    ctx = await _context(request)
    return {"ok": True, "items": await list_tags(ctx.tenant_id, ctx.account)}


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
    """Soft delete: «Удалено · Вернуть» on the site calls …/restore within 24 hours."""
    ctx = await _context(request)
    try:
        await delete_contact(ctx.tenant_id, ctx.account, contact_id)
    except CrmError as exc:
        _raise(exc)
    return Response(status_code=204)


@router.post(f"{PREFIX}/contacts/{{contact_id}}/restore")
async def crm_contact_restore(contact_id: str, request: Request) -> dict[str, Any]:
    ctx = await _context(request)
    try:
        contact = await restore_contact(ctx.tenant_id, ctx.account, contact_id)
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, "contact": contact}


@router.get(f"{PREFIX}/contacts/{{contact_id}}/activities")
async def crm_contact_activities(
    contact_id: str, request: Request, cursor: str | None = None, limit: int | None = None
) -> dict[str, Any]:
    ctx = await _context(request)
    try:
        result = await list_activities(ctx.tenant_id, ctx.account, contact_id, cursor=cursor, limit=limit)
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, **result}


@router.post(f"{PREFIX}/contacts/{{contact_id}}/log", status_code=201)
async def crm_contact_log(contact_id: str, body: LogBody, request: Request) -> dict[str, Any]:
    """A tap on «Позвонить» / a messenger (maybe with a template): history + last_touch_at."""
    ctx = await _context(request)
    try:
        result = await log_contact(
            ctx.tenant_id, ctx.account, contact_id, kind=body.kind, channel=body.channel, template_id=body.template_id
        )
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, **result}


@router.post(f"{PREFIX}/contacts/{{contact_id}}/snooze")
async def crm_contact_snooze(contact_id: str, body: SnoozeBody, request: Request) -> dict[str, Any]:
    """«Перенести»: ``{days}`` or ``{date}`` — the same step on another day."""
    ctx = await _context(request)
    try:
        contact = await snooze_contact(ctx.tenant_id, ctx.account, contact_id, days=body.days, on=body.until)
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, "contact": contact}


@router.post(f"{PREFIX}/contacts/{{contact_id}}/done")
async def crm_contact_done(contact_id: str, body: DoneBody, request: Request) -> dict[str, Any]:
    """«Сделано»: the next step by the rules; «Пригласить» needs ``meeting_at``
    (400 meeting_at_required) unless a future meeting is already on the card."""
    ctx = await _context(request)
    try:
        contact = await done_contact(
            ctx.tenant_id,
            ctx.account,
            contact_id,
            meeting_at=body.meeting_at,
            next_at=body.next_at,
            next_at_given="next_at" in body.model_fields_set,
        )
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, "contact": contact}


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


@router.get(f"{PREFIX}/templates")
async def crm_templates(request: Request) -> dict[str, Any]:
    """The partner's templates; the six defaults appear on the first call."""
    ctx = await _context(request)
    return {"ok": True, "items": await list_templates(ctx.tenant_id, ctx.account)}


@router.post(f"{PREFIX}/templates", status_code=201)
async def crm_template_create(body: TemplateCreateBody, request: Request) -> dict[str, Any]:
    ctx = await _context(request)
    try:
        template = await create_template(
            ctx.tenant_id, ctx.account, title=body.title, body=body.body, position=body.position
        )
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, "template": template}


@router.patch(f"{PREFIX}/templates/{{template_id}}")
async def crm_template_patch(template_id: str, body: TemplatePatchBody, request: Request) -> dict[str, Any]:
    ctx = await _context(request)
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    try:
        template = await update_template(ctx.tenant_id, ctx.account, template_id, changes)
    except CrmError as exc:
        _raise(exc)
    return {"ok": True, "template": template}


@router.delete(f"{PREFIX}/templates/{{template_id}}", status_code=204)
async def crm_template_delete(template_id: str, request: Request) -> Response:
    ctx = await _context(request)
    try:
        await delete_template(ctx.tenant_id, ctx.account, template_id)
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
