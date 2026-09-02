from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request, Response
from starlette import status

from app.leads.service import parse_lead_body, save_lead, validate_lead_shadow
from app.settings import get_settings
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["leads"])


@router.post("/v1/leads", status_code=status.HTTP_201_CREATED)
async def create_lead_v1(request: Request) -> dict:
    return await _create_lead(request)


@router.post("/api/v1/leads", status_code=status.HTTP_201_CREATED)
async def create_lead_site_v1(request: Request) -> dict:
    return await _create_lead(request)


@router.post("/api/lead", status_code=status.HTTP_201_CREATED)
async def create_lead_legacy_alias(request: Request) -> dict:
    return await _create_lead(request)


async def _create_lead(request: Request) -> dict:
    settings = get_settings()
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "partner_leads")

    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_json"}) from exc
    if not isinstance(body, dict):
        body = {}

    lead = parse_lead_body(body, tenant.tenant_id)

    if settings.core_route_leads == "shadow":
        shadow = await validate_lead_shadow(lead)
        request.state.shadow_lead = shadow
        return {
            "ok": True,
            "message": "Заявка принята. Мы скоро свяжемся с вами.",
            "shadow": True,
        }

    if settings.core_route_leads == "legacy":
        from app.advisor.legacy_adapter import post_legacy_json

        return await post_legacy_json(
            request,
            "/webhook/wwc-website-lead-v1",
            body,
        )

    await save_lead(lead)
    return {
        "ok": True,
        "message": "Заявка принята. Мы скоро свяжемся с вами.",
    }
