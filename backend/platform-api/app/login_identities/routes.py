"""Ручки сайта: Google OAuth (start/callback), «ввести код от Виктора», способы входа в кабинете.

Два адреса у каждой ручки, как у всего content-access: бой проксирует /api/v1/…, staging
(nginx location /api/ → Core /) отрезает /api и приходит на /v1/….

Контракт (для Site-сессии):
  GET  …/login/google/start?return_to=<путь|https://host/путь>&visitor_session_id=<uuid?>
       → 302 на Google; 503 {error: google_disabled}; 400 {error: invalid_return_to}.
  GET  …/login/google/callback?state=&code=|error=
       → 302 https://<host><return_to>                       — личность уже привязана, cookie выставлена
                                                               (тот же домен cookie) или
         302 https://<host><return_to>#wwc-login=<одноразовый> — другой хост: сайт входит сам
         302 https://<host><return_to>#wwc-link=<pending>     — нужен код от Виктора
         302 https://<host><return_to>#wwc-link-error=<код>   — google_denied | google_token_invalid |
                                                               email_unverified | code_locked
       400 {error: google_state_invalid} — неизвестный или повторный state (хост неизвестен).
  POST …/login/link {pending_id, code}
       → 200 {ok, status: "authenticated", expires_at} + cookie сессии;
         400 code_invalid | 410 code_expired | 409 code_used | 429 code_locked | 409 identity_taken |
         404 pending_invalid | 410 pending_expired.
  DELETE …/login/identities/{google|max}  (нужна сессия)
       → 200 {ok, provider, revoked: n}; 400 invalid_provider | provider_not_revocable.
  GET  …/me → поле login_methods: [{provider, label, linked_at}] (в content_access/routes.py).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.content_access.cookies import set_session_cookie
from app.content_access.routes import _current_session
from app.login_identities import service
from app.login_identities.rules import LoginIdentityError
from app.tenancy import get_request_tenant, require_entitlement

router = APIRouter(tags=["login-identities"])

API = "/api/v1/content-access/login"
V1 = "/v1/content-access/login"
NO_STORE = "private, no-store"


def _http(exc: LoginIdentityError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"error": exc.code})


def _redirect(url: str) -> RedirectResponse:
    response = RedirectResponse(url=url, status_code=302)
    response.headers["Cache-Control"] = NO_STORE
    return response


class LinkBody(BaseModel):
    pending_id: str
    code: str


# ---- Google ---------------------------------------------------------------------------------------


async def _google_start(request: Request, return_to: str | None, visitor_session_id: str | None) -> Response:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    try:
        url = await service.google_start(
            tenant.tenant_id,
            host=service.request_host(request.headers),
            return_to=return_to or "/",
            visitor_session_id=visitor_session_id,
        )
    except LoginIdentityError as exc:
        raise _http(exc) from exc
    return _redirect(url)


@router.get(f"{API}/google/start")
@router.get(f"{V1}/google/start")
async def google_start(
    request: Request, return_to: str | None = None, visitor_session_id: str | None = None
) -> Response:
    return await _google_start(request, return_to, visitor_session_id)


async def _google_callback(request: Request, state: str | None, code: str | None, error: str | None) -> Response:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    try:
        result = await service.google_callback(
            tenant.tenant_id, state=state or "", code=code, oauth_error=error
        )
    except LoginIdentityError as exc:
        raise _http(exc) from exc
    return_to = str(result["return_to"])
    if result["outcome"] == "error":
        return _redirect(service.link_error_url(return_to, str(result["error"])))
    if result["outcome"] == "link_required":
        return _redirect(service.link_url(return_to, str(result["link_token"])))
    telegram_user_id = int(result["telegram_user_id"])
    callback_host = service.request_host(request.headers)
    if service.same_cookie_family(callback_host, str(result["host"])):
        # Та же семья cookie (.wwc.best): ставим сессию прямо на редиректе, как после Telegram.
        _, raw_session = await service.session_for(
            tenant.tenant_id,
            telegram_user_id=telegram_user_id,
            visitor_session_id=result.get("visitor_session_id"),
            source="google_login",
        )
        response = _redirect(return_to)
        set_session_cookie(response, raw_session, request)
        return response
    # Другой хост (витрина Академии): одноразовая ссылка входа — cookie встанет там, где нажали «Войти».
    url = await service.browser_login_url(tenant.tenant_id, telegram_user_id=telegram_user_id, return_to=return_to)
    return _redirect(url or return_to)


@router.get(f"{API}/google/callback")
@router.get(f"{V1}/google/callback")
async def google_callback(
    request: Request, state: str | None = None, code: str | None = None, error: str | None = None
) -> Response:
    return await _google_callback(request, state, code, error)


# ---- код после Google -----------------------------------------------------------------------------


async def _link(body: LinkBody, request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = NO_STORE
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    try:
        linked = await service.link_pending(tenant.tenant_id, link_token=body.pending_id, code=body.code)
    except LoginIdentityError as exc:
        raise _http(exc) from exc
    payload, raw_session = await service.session_for(
        tenant.tenant_id,
        telegram_user_id=int(linked["telegram_user_id"]),
        visitor_session_id=linked.get("visitor_session_id"),
        source="google_login",
    )
    set_session_cookie(response, raw_session, request)
    return payload


@router.post(f"{API}/link")
@router.post(f"{V1}/link")
async def link(body: LinkBody, request: Request, response: Response) -> dict[str, Any]:
    return await _link(body, request, response)


# ---- кабинет: отвязать ----------------------------------------------------------------------------


async def _revoke(provider: str, request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = NO_STORE
    session = await _current_session(request)
    tenant = get_request_tenant(request)
    telegram_user_id = session.get("telegram_user_id")
    if telegram_user_id is None:
        raise HTTPException(status_code=401, detail={"error": "content_session_invalid"})
    try:
        revoked = await service.revoke_identity(
            tenant.tenant_id,
            telegram_user_id=int(telegram_user_id),
            provider=provider,
            revoked_by=f"self:{int(telegram_user_id)}",
        )
    except LoginIdentityError as exc:
        raise _http(exc) from exc
    return {"ok": True, "provider": provider.lower(), "revoked": revoked}


@router.delete(f"{API}/identities/{{provider}}")
@router.delete(f"{V1}/identities/{{provider}}")
async def revoke(provider: str, request: Request, response: Response) -> dict[str, Any]:
    return await _revoke(provider, request, response)
