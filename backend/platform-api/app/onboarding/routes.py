from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.internal_auth import InternalSecretHeader, require_internal_secret
from app.onboarding.service import enroll_user, handle_onboarding_text
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["onboarding"])


class OnboardingEnrollBody(BaseModel):
    telegram_user_id: int
    first_ref: str | None = None
    idempotency_key: str | None = None


class OnboardingCommandBody(BaseModel):
    telegram_user_id: int
    text: str = Field(min_length=1, max_length=500)
    first_ref: str | None = None


@router.post("/v1/onboarding/enroll")
async def enroll_v1(body: OnboardingEnrollBody, request: Request, internal_secret: InternalSecretHeader = None) -> dict:
    # telegram_user_id is caller-supplied; the bot's own onboarding runs through the
    # verified webhook path, so the HTTP variant is server-to-server only (F021).
    require_internal_secret(internal_secret)
    return await _enroll(body, request)


@router.post("/v1/onboarding/command")
async def command_v1(body: OnboardingCommandBody, request: Request, internal_secret: InternalSecretHeader = None) -> dict:
    require_internal_secret(internal_secret)
    return await _command(body, request)


async def _enroll(body: OnboardingEnrollBody, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    return await enroll_user(
        tenant.tenant_id,
        telegram_user_id=body.telegram_user_id,
        first_ref=body.first_ref,
        idempotency_key=body.idempotency_key,
    )


async def _command(body: OnboardingCommandBody, request: Request) -> dict:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    result = await handle_onboarding_text(
        tenant.tenant_id,
        telegram_user_id=body.telegram_user_id,
        text=body.text,
        first_ref=body.first_ref,
    )
    if result is None:
        return {"ok": False, "error": "not_an_onboarding_command"}
    return result
