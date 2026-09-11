from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel

from app.content_access.cookies import (
    clear_session_cookie,
    read_session_cookie,
    set_session_cookie,
)
from app.content_access.service import (
    create_content_challenge,
    format_me_payload,
    load_material,
    poll_content_challenge,
    revoke_content_session,
    validate_content_session,
)
from app.subscriptions.repeat_prices import load_repeat_price_catalog
from app.subscriptions.service import resolve_partner_subscription_by_telegram_user_id
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["content-access"])


class ChallengeCreateBody(BaseModel):
    browser_nonce: str
    return_to: str
    scope: str | None = "telegram_verified"
    visitor_session_id: str | None = None
    ref: str | None = None
    context: dict[str, Any] | None = None


def _require_session(request: Request) -> str:
    raw = read_session_cookie(request)
    if not raw:
        raise HTTPException(status_code=401, detail={"error": "content_session_required"})
    return raw


async def _current_session(request: Request) -> dict[str, Any]:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    raw = _require_session(request)
    session = await validate_content_session(tenant.tenant_id, raw)
    if not session:
        raise HTTPException(status_code=401, detail={"error": "content_session_invalid"})
    return session


async def _create(body: ChallengeCreateBody, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    return await create_content_challenge(
        tenant.tenant_id,
        browser_nonce=body.browser_nonce,
        return_to=body.return_to,
        scope=body.scope,
        visitor_session_id=body.visitor_session_id,
        ref=body.ref,
        context=body.context,
    )


@router.post("/v1/content-access/challenges")
async def create_challenge_v1(body: ChallengeCreateBody, request: Request) -> dict:
    return await _create(body, request)


@router.post("/api/v1/content-access/challenges")
async def create_challenge_site(body: ChallengeCreateBody, request: Request) -> dict:
    return await _create(body, request)


async def _poll(
    challenge_id: str,
    response: Response,
    request: Request,
    x_browser_nonce: str | None,
) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    if not x_browser_nonce:
        raise HTTPException(status_code=400, detail={"error": "browser_nonce_required"})
    payload, raw_session = await poll_content_challenge(
        tenant.tenant_id,
        challenge_id=challenge_id,
        browser_nonce=x_browser_nonce,
    )
    if raw_session:
        set_session_cookie(response, raw_session)
    return payload


@router.get("/v1/content-access/challenges/{challenge_id}")
async def poll_challenge_v1(
    challenge_id: str,
    response: Response,
    request: Request,
    x_browser_nonce: Annotated[str | None, Header(alias="X-Browser-Nonce")] = None,
) -> dict:
    return await _poll(challenge_id, response, request, x_browser_nonce)


@router.get("/api/v1/content-access/challenges/{challenge_id}")
async def poll_challenge_site(
    challenge_id: str,
    response: Response,
    request: Request,
    x_browser_nonce: Annotated[str | None, Header(alias="X-Browser-Nonce")] = None,
) -> dict:
    return await _poll(challenge_id, response, request, x_browser_nonce)


async def _me(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    session = await _current_session(request)
    tenant = get_request_tenant(request)
    telegram_user_id = session.get("telegram_user_id")
    subscription = None
    if telegram_user_id is not None:
        subscription = await resolve_partner_subscription_by_telegram_user_id(
            tenant.tenant_id,
            int(telegram_user_id),
        )
    return format_me_payload(session, partner_subscription=subscription)


@router.get("/v1/content-access/me")
async def me_v1(request: Request, response: Response) -> dict:
    return await _me(request, response)


@router.get("/api/v1/content-access/me")
async def me_site(request: Request, response: Response) -> dict:
    return await _me(request, response)


async def _repeat_prices(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    session = await _current_session(request)
    tenant = get_request_tenant(request)
    telegram_user_id = session.get("telegram_user_id")
    subscription = None
    if telegram_user_id is not None:
        subscription = await resolve_partner_subscription_by_telegram_user_id(
            tenant.tenant_id,
            int(telegram_user_id),
        )
    if not subscription or not subscription.get("partner_paid"):
        raise HTTPException(status_code=403, detail={"error": "partner_paid_required"})
    return {"ok": True, "catalog": load_repeat_price_catalog()}


@router.get("/v1/content-access/repeat-prices")
async def repeat_prices_v1(request: Request, response: Response) -> dict:
    return await _repeat_prices(request, response)


@router.get("/api/v1/content-access/repeat-prices")
async def repeat_prices_site(request: Request, response: Response) -> dict:
    return await _repeat_prices(request, response)


async def _logout(request: Request, response: Response) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    raw = read_session_cookie(request)
    if raw:
        await revoke_content_session(tenant.tenant_id, raw)
    clear_session_cookie(response)
    return {"ok": True}


@router.post("/v1/content-access/logout")
async def logout_v1(request: Request, response: Response) -> dict:
    return await _logout(request, response)


@router.post("/api/v1/content-access/logout")
async def logout_site(request: Request, response: Response) -> dict:
    return await _logout(request, response)


async def _materials(content_key: str, request: Request) -> dict:
    session = await _current_session(request)
    tenant = get_request_tenant(request)
    return await load_material(
        tenant.tenant_id,
        content_key,
        scope=str(session.get("scope") or "telegram_verified"),
    )


@router.get("/v1/content-access/materials/{content_key:path}")
async def materials_v1(content_key: str, request: Request) -> dict:
    return await _materials(content_key, request)


@router.get("/api/v1/content-access/materials/{content_key:path}")
async def materials_site(content_key: str, request: Request) -> dict:
    return await _materials(content_key, request)
