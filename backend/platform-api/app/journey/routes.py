from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.journey.events import parse_event_body, record_interaction_event
from app.journey.sessions import get_visitor_session, upsert_visitor_session
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["journey"])


class InteractionEventBody(BaseModel):
    event_type: str
    idempotency_key: str
    visitor_session_id: str | None = None
    payload: dict | None = None


class VisitorSessionUpsertBody(BaseModel):
    visitor_session_id: str | None = None
    ref: str | None = None
    current_ref: str | None = None
    journey_type: str | None = Field(default="organic")
    campaign: str | None = None
    source: str | None = None
    content: str | None = None
    consent_scope: str | None = None
    context: dict | None = None


@router.post("/v1/interaction-events")
async def post_event_v1(body: InteractionEventBody, request: Request) -> dict:
    return await _post_event(body, request)


@router.post("/api/v1/interaction-events")
async def post_event_site(body: InteractionEventBody, request: Request) -> dict:
    return await _post_event(body, request)


@router.get("/v1/visitor-sessions/{session_id}")
async def get_session_v1(session_id: str, request: Request) -> dict:
    return await _get_session(session_id, request)


@router.get("/api/v1/visitor-sessions/{session_id}")
async def get_session_site(session_id: str, request: Request) -> dict:
    return await _get_session(session_id, request)


@router.put("/v1/visitor-sessions")
async def upsert_session_v1(body: VisitorSessionUpsertBody, request: Request) -> dict:
    return await _upsert_session(body, request)


@router.put("/api/v1/visitor-sessions")
async def upsert_session_site(body: VisitorSessionUpsertBody, request: Request) -> dict:
    return await _upsert_session(body, request)


async def _post_event(body: InteractionEventBody, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    parsed = parse_event_body(body.model_dump(exclude_none=True))
    return await record_interaction_event(tenant.tenant_id, parsed)


async def _get_session(session_id: str, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    return await get_visitor_session(tenant.tenant_id, session_id)


async def _upsert_session(body: VisitorSessionUpsertBody, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    return await upsert_visitor_session(tenant.tenant_id, body.model_dump(exclude_none=True))
