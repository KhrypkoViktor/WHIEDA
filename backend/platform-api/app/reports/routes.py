from __future__ import annotations

from fastapi import APIRouter, Request

from app.reports.service import build_leader_digest
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["reports"])


@router.get("/v1/reports/leader-digest")
async def leader_digest(request: Request, days: int = 7) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    return await build_leader_digest(tenant.tenant_id, days=days)
