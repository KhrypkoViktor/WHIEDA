from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.memory.service import list_memory_facts, upsert_memory_fact
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["memory"])


class MemoryFactBody(BaseModel):
    subject_type: Literal["visitor_session", "telegram_user"]
    subject_id: str
    fact_key: str = Field(max_length=64)
    fact_value: dict
    consent_scope: str | None = None


@router.get("/v1/memory-facts/{subject_type}/{subject_id}")
async def list_facts(subject_type: str, subject_id: str, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    facts = await list_memory_facts(tenant.tenant_id, subject_type=subject_type, subject_id=subject_id)
    return {"ok": True, "facts": facts}


@router.put("/v1/memory-facts")
async def put_fact(body: MemoryFactBody, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    return await upsert_memory_fact(
        tenant.tenant_id,
        subject_type=body.subject_type,
        subject_id=body.subject_id,
        fact_key=body.fact_key,
        fact_value=body.fact_value,
        consent_scope=body.consent_scope,
    )
