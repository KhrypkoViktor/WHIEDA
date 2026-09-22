from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from app.admin.auth.cookies import clear_session_cookie, read_session_cookie, set_session_cookie
from app.admin.auth.dependencies import (
    AdminSession,
    optional_host_tenant,
    require_admin_roles,
    require_admin_session,
    resolve_effective_tenant,
)
from app.admin.auth.service import (
    confirm_login_from_telegram,
    create_login_challenge,
    format_me_payload,
    poll_login_challenge,
    revoke_session,
)
from app.admin.services.leads import build_lead_detail, build_leads_list
from app.admin.services.markets import (
    build_markets_payload,
    build_prices_payload,
    build_service_centers_payload,
    build_sync_status_payload,
)
from app.admin.services.overview import build_overview_payload
from app.admin.services.referrals import build_referrals_list
from app.admin.gap_review.service import (
    build_detail as build_gap_detail,
    build_export as build_gap_export,
    build_list as build_gap_list,
    build_summary as build_gap_summary,
    patch_item as patch_gap_item,
)
from app.settings import get_settings

router = APIRouter(prefix="/v1/admin", tags=["admin"])

READ_ROLES = ("super_admin", "admin", "viewer")
WRITE_ROLES = ("super_admin", "admin")


def _verify_confirm_secret(provided: str | None) -> None:
    settings = get_settings()
    expected = settings.platform_admin_confirm_secret
    if not expected or not provided or provided != expected:
        raise HTTPException(status_code=403, detail={"ok": False, "error": "confirm_forbidden"})


@router.post("/auth/challenge")
async def admin_auth_challenge(request: Request, body: dict) -> dict:
    browser_nonce = str(body.get("browser_nonce") or "").strip()
    return await create_login_challenge(browser_nonce=browser_nonce)


@router.post("/auth/telegram-confirm")
async def admin_auth_telegram_confirm(
    body: dict,
    x_platform_admin_secret: Annotated[str | None, Header(alias="X-Platform-Admin-Secret")] = None,
) -> dict:
    _verify_confirm_secret(x_platform_admin_secret)
    challenge_token = str(body.get("challenge_token") or body.get("token") or "").strip()
    telegram_user_id = body.get("telegram_user_id")
    if not challenge_token or telegram_user_id is None:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid_confirm_payload"})
    return await confirm_login_from_telegram(
        challenge_token=challenge_token,
        telegram_user_id=int(telegram_user_id),
    )


@router.get("/auth/challenge/{challenge_id}")
async def admin_auth_challenge_poll(
    challenge_id: str,
    request: Request,
    response: Response,
    x_browser_nonce: Annotated[str | None, Header(alias="X-Browser-Nonce")] = None,
) -> dict:
    if not x_browser_nonce:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "browser_nonce_required"})
    payload, raw_session = await poll_login_challenge(
        challenge_id=challenge_id,
        browser_nonce=x_browser_nonce,
    )
    if raw_session:
        set_session_cookie(response, raw_session, request)
    return payload


@router.post("/auth/logout")
async def admin_auth_logout(request: Request, response: Response) -> dict:
    raw = read_session_cookie(request)
    if raw:
        await revoke_session(raw)
    clear_session_cookie(response, request)
    return {"ok": True, "logged_out": True}


@router.get("/me")
async def admin_me(
    request: Request,
    session: AdminSession = Depends(require_admin_session),
    tenant_id: str | None = Query(default=None),
) -> dict:
    effective = await resolve_effective_tenant(
        request,
        session,
        requested_tenant_id=tenant_id,
        audit_action="admin_me_view",
    )
    from app.admin.auth.service import load_principal

    principal = await load_principal(session.principal_id)
    if not principal:
        raise HTTPException(status_code=401, detail={"error": "admin_session_invalid"})
    return format_me_payload(principal, effective_tenant_id=effective)


@router.get("/leads")
async def admin_leads_list(
    request: Request,
    session: AdminSession = Depends(require_admin_session),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    ref: str | None = Query(default=None),
    country_code: str | None = Query(default=None),
    assignee: str | None = Query(default=None),
    product_sku: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
) -> dict:
    require_admin_roles(session, *READ_ROLES)
    effective = await resolve_effective_tenant(request, session, requested_tenant_id=tenant_id)
    return await build_leads_list(
        effective,
        limit=limit,
        offset=offset,
        status=status,
        ref_code=ref,
        country_code=country_code,
        assignee_id=assignee,
        product_sku=product_sku,
    )


@router.get("/leads/{lead_id}")
async def admin_lead_detail(
    lead_id: str,
    request: Request,
    session: AdminSession = Depends(require_admin_session),
    tenant_id: str | None = Query(default=None),
) -> dict:
    require_admin_roles(session, *READ_ROLES)
    effective = await resolve_effective_tenant(request, session, requested_tenant_id=tenant_id)
    result = await build_lead_detail(effective, lead_id, role=session.role)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result


@router.get("/referrals")
async def admin_referrals_list(
    request: Request,
    session: AdminSession = Depends(require_admin_session),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    q: str | None = Query(default=None),
    tenant_id: str | None = Query(default=None),
) -> dict:
    require_admin_roles(session, *READ_ROLES)
    effective = await resolve_effective_tenant(request, session, requested_tenant_id=tenant_id)
    return await build_referrals_list(effective, limit=limit, offset=offset, search=q)


@router.get("/markets")
async def admin_markets(
    request: Request,
    session: AdminSession = Depends(require_admin_session),
    tenant_id: str | None = Query(default=None),
) -> dict:
    require_admin_roles(session, *READ_ROLES)
    effective = await resolve_effective_tenant(request, session, requested_tenant_id=tenant_id)
    return await build_markets_payload(effective)


@router.get("/prices")
async def admin_prices(
    request: Request,
    session: AdminSession = Depends(require_admin_session),
    market_id: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    tenant_id: str | None = Query(default=None),
) -> dict:
    require_admin_roles(session, *READ_ROLES)
    effective = await resolve_effective_tenant(request, session, requested_tenant_id=tenant_id)
    return await build_prices_payload(effective, market_id=market_id, limit=limit, offset=offset)


@router.get("/service-centers")
async def admin_service_centers(
    request: Request,
    session: AdminSession = Depends(require_admin_session),
    tenant_id: str | None = Query(default=None),
) -> dict:
    require_admin_roles(session, *READ_ROLES)
    effective = await resolve_effective_tenant(request, session, requested_tenant_id=tenant_id)
    return await build_service_centers_payload(effective)


@router.get("/sync-status")
async def admin_sync_status(
    request: Request,
    session: AdminSession = Depends(require_admin_session),
    tenant_id: str | None = Query(default=None),
) -> dict:
    require_admin_roles(session, *READ_ROLES)
    effective = await resolve_effective_tenant(request, session, requested_tenant_id=tenant_id)
    return await build_sync_status_payload(effective)


@router.get("/overview")
async def admin_overview(
    request: Request,
    session: AdminSession = Depends(require_admin_session),
    tenant_id: str | None = Query(default=None),
) -> dict:
    require_admin_roles(session, *READ_ROLES)
    effective = await resolve_effective_tenant(request, session, requested_tenant_id=tenant_id)
    return await build_overview_payload(effective)


@router.get("/advisor-gaps/summary")
async def admin_advisor_gaps_summary(
    request: Request,
    session: AdminSession = Depends(require_admin_session),
    tenant_id: str | None = Query(default=None),
) -> dict:
    require_admin_roles(session, *READ_ROLES)
    effective = await resolve_effective_tenant(
        request,
        session,
        requested_tenant_id=tenant_id,
        audit_action="admin_advisor_gaps_summary",
    )
    return await build_gap_summary(effective)


@router.get("/advisor-gaps/export")
async def admin_advisor_gaps_export(
    request: Request,
    session: AdminSession = Depends(require_admin_session),
    tenant_id: str | None = Query(default=None),
    format: str = Query(default="md", alias="format"),
) -> dict:
    require_admin_roles(session, *READ_ROLES)
    effective = await resolve_effective_tenant(
        request,
        session,
        requested_tenant_id=tenant_id,
        audit_action="admin_advisor_gaps_export",
    )
    return await build_gap_export(effective, fmt=format)


@router.get("/advisor-gaps")
async def admin_advisor_gaps_list(
    request: Request,
    session: AdminSession = Depends(require_admin_session),
    tenant_id: str | None = Query(default=None),
    gap_kind: str | None = Query(default=None),
    status: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    from_ts: str | None = Query(default=None, alias="from"),
    to_ts: str | None = Query(default=None, alias="to"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    require_admin_roles(session, *READ_ROLES)
    effective = await resolve_effective_tenant(
        request,
        session,
        requested_tenant_id=tenant_id,
        audit_action="admin_advisor_gaps_list",
    )
    return await build_gap_list(
        effective,
        gap_kind=gap_kind,
        status=status,
        priority=priority,
        from_ts=from_ts,
        to_ts=to_ts,
        limit=limit,
        offset=offset,
    )


@router.get("/advisor-gaps/{item_id}")
async def admin_advisor_gap_detail(
    item_id: str,
    request: Request,
    session: AdminSession = Depends(require_admin_session),
    tenant_id: str | None = Query(default=None),
) -> dict:
    require_admin_roles(session, *READ_ROLES)
    effective = await resolve_effective_tenant(
        request,
        session,
        requested_tenant_id=tenant_id,
        audit_action="admin_advisor_gaps_detail",
    )
    result = await build_gap_detail(effective, item_id)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result


@router.patch("/advisor-gaps/{item_id}")
async def admin_advisor_gap_patch(
    item_id: str,
    body: dict,
    request: Request,
    session: AdminSession = Depends(require_admin_session),
    tenant_id: str | None = Query(default=None),
) -> dict:
    require_admin_roles(session, *WRITE_ROLES)
    effective = await resolve_effective_tenant(
        request,
        session,
        requested_tenant_id=tenant_id,
        audit_action="admin_advisor_gaps_patch",
    )
    result = await patch_gap_item(
        effective,
        item_id,
        body,
        actor_principal_id=session.principal_id,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result)
    return result
