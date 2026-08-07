from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.pilot.service import get_pilot_summary, record_outcome, refresh_daily_metrics
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["pilot"])


class OutcomeBody(BaseModel):
    outcome_type: str
    idempotency_key: str
    visitor_session_id: str | None = None
    source_route: str | None = None
    payload: dict | None = None


@router.post("/v1/pilot/outcomes")
async def post_outcome(body: OutcomeBody, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "partner_leads")
    return await record_outcome(
        tenant.tenant_id,
        outcome_type=body.outcome_type,
        idempotency_key=body.idempotency_key,
        session_id=body.visitor_session_id,
        source_route=body.source_route,
        payload=body.payload,
    )


@router.post("/v1/pilot/refresh-metrics")
async def post_refresh(request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "partner_leads")
    return await refresh_daily_metrics(tenant.tenant_id)


@router.get("/v1/pilot/summary")
async def get_summary(request: Request, days: int = 7) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "partner_leads")
    return await get_pilot_summary(tenant.tenant_id, days=days)
