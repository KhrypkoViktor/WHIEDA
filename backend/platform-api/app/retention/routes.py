from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.retention.service import list_retention_registry, request_export
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["retention"])


class ExportRequestBody(BaseModel):
    data_class: str
    requester_scope: str = "leader"
    idempotency_key: str


@router.get("/v1/retention/registry")
async def get_registry(request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "partner_leads")
    rows = await list_retention_registry(tenant.tenant_id)
    return {"ok": True, "registry": rows}


@router.post("/v1/retention/export-requests")
async def post_export(body: ExportRequestBody, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "partner_leads")
    return await request_export(
        tenant.tenant_id,
        data_class=body.data_class,
        requester_scope=body.requester_scope,
        idempotency_key=body.idempotency_key,
    )
