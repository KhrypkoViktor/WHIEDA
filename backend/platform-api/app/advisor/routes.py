from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from app.advisor.legacy_adapter import post_legacy_json
from app.advisor.shadow import compare_advisor_responses, log_shadow_comparison
from app.advisor.service import advisor_error, handle_structured_query
from app.settings import get_settings
from app.tenancy import get_request_tenant, get_trace_id, require_entitlement

router = APIRouter(tags=["advisor"])


@router.post("/v1/advisor/query")
async def advisor_query_v1(request: Request) -> dict:
    return await _advisor_query(request)


@router.post("/api/v1/advisor/query")
async def advisor_query_site_v1(request: Request) -> dict:
    return await _advisor_query(request)


@router.post("/api/advisor/query")
async def advisor_query_legacy_alias(request: Request) -> dict:
    return await _advisor_query(request)


async def _advisor_query(request: Request) -> dict:
    settings = get_settings()
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    trace_id = get_trace_id(request)

    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail={"ok": False, "error": "invalid_json"}) from exc
    if not isinstance(body, dict):
        body = {}

    if settings.core_route_advisor == "legacy":
        legacy_body = {**body, "tenant": tenant.tenant_id}
        return await post_legacy_json(request, "/webhook/wwc-advisor-public-v1", legacy_body)

    try:
        core_response = await handle_structured_query(tenant, body, trace_id)
    except HTTPException:
        raise
    except Exception:
        return advisor_error(request)

    if settings.core_route_advisor == "shadow":
        legacy_body = {**body, "tenant": tenant.tenant_id}
        legacy_response: dict | None = None
        try:
            legacy_response = await post_legacy_json(
                request,
                "/webhook/wwc-advisor-public-v1",
                legacy_body,
                timeout_sec=min(8.0, float(settings.legacy_request_timeout_sec)),
            )
            comparison = compare_advisor_responses(core_response, legacy_response)
            log_shadow_comparison(
                trace_id=trace_id,
                tenant_id=tenant.tenant_id,
                comparison=comparison,
                question_len=len(str(body.get("question") or "")),
            )
            request.state.shadow_advisor = {
                "core": core_response,
                "legacy": legacy_response,
                "comparison": comparison,
            }
        except Exception:
            request.state.shadow_advisor = {"core": core_response, "legacy_error": True}
        if legacy_response:
            return legacy_response
        return core_response

    return core_response
