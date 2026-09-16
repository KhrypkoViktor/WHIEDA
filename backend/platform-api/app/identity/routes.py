from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.identity.service import create_telegram_link_token, exchange_telegram_link_token
from app.settings import get_settings
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["identity"])


def _verify_exchange_secret(provided: str | None) -> None:
    """telegram_user_id in the exchange body is caller-supplied and never verified
    against a real Telegram update, so this route must only accept server-to-server
    callers holding PLATFORM_IDENTITY_EXCHANGE_SECRET. Unset secret = route disabled,
    not open -- there is no known legitimate public caller for it today."""
    expected = get_settings().platform_identity_exchange_secret
    if not expected or not provided or provided != expected:
        raise HTTPException(status_code=403, detail={"ok": False, "error": "exchange_forbidden"})


class TelegramLinkTokenCreateBody(BaseModel):
    visitor_session_id: str | None = None
    ref: str | None = None
    journey_type: str | None = Field(default="organic")
    campaign: str | None = None
    source: str | None = None
    content: str | None = None
    consent_scope: str | None = None
    context: dict | None = None
    idempotency_key: str | None = None


class TelegramLinkTokenExchangeBody(BaseModel):
    token: str
    telegram_user_id: int
    telegram_chat_id: int | None = None


@router.post("/v1/telegram-link-tokens")
async def create_link_token_v1(body: TelegramLinkTokenCreateBody, request: Request) -> dict:
    return await _create_link_token(body, request)


@router.post("/api/v1/telegram-link-tokens")
async def create_link_token_site(body: TelegramLinkTokenCreateBody, request: Request) -> dict:
    return await _create_link_token(body, request)


@router.post("/v1/telegram-link-tokens/exchange")
async def exchange_link_token_v1(
    body: TelegramLinkTokenExchangeBody,
    request: Request,
    x_platform_identity_secret: Annotated[str | None, Header(alias="X-Platform-Identity-Secret")] = None,
) -> dict:
    _verify_exchange_secret(x_platform_identity_secret)
    return await _exchange_link_token(body, request)


async def _create_link_token(body: TelegramLinkTokenCreateBody, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    settings = get_settings()

    result = await create_telegram_link_token(
        tenant.tenant_id,
        body.model_dump(exclude_none=True),
        bot_username=settings.telegram_bot_username,
    )
    return {
        "ok": True,
        "token_id": result.token_id,
        "visitor_session_id": result.visitor_session_id,
        "link_token": result.link_token,
        "deep_link": result.deep_link,
        "expires_at": result.expires_at,
        "first_ref": result.first_ref,
        "mentor_display_name": result.mentor_display_name,
    }


async def _exchange_link_token(body: TelegramLinkTokenExchangeBody, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")

    result = await exchange_telegram_link_token(
        tenant.tenant_id,
        body.token,
        telegram_user_id=body.telegram_user_id,
        telegram_chat_id=body.telegram_chat_id,
    )
    return {
        "ok": True,
        "link_id": result.link_id,
        "visitor_session_id": result.visitor_session_id,
        "first_ref": result.first_ref,
        "mentor_display_name": result.mentor_display_name,
        "journey_type": result.journey_type,
        "context": result.context,
    }
