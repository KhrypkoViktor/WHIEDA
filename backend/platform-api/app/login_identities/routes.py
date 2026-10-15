"""Ручки сайта: Google OAuth (start/callback), «ввести код от Виктора», способы входа в кабинете.

Два адреса у каждой ручки, как у всего content-access: бой проксирует /api/v1/…, staging
(nginx location /api/ → Core /) отрезает /api и приходит на /v1/….

Контракт (для Site-сессии; подробно — PROCESS/wwc-login-redesign-20261015/STATE_core.md):
  GET  …/login/google/start?return_to=<путь|https://host/путь>[&visitor_session_id=<uuid>][&browser_key=<ключ>]
       Хост входа в семье cookie (.wwc.best): → 302 на Google + cookie wwc_oauth с СЕРВЕРНЫМ ключом
         (HttpOnly, Lax, 15 мин); browser_key из query игнорируется.
       Хост вне семьи (витрины): browser_key обязателен (ключ сайта из sessionStorage) → 302 на Google, cookie нет;
         без ключа → 302 …#wwc-link-error=browser_key_required.
       Google выключен → 302 https://<host><return_to>#wwc-link-error=google_disabled;
       негодный путь → 302 https://<host>/#wwc-link-error=invalid_return_to; негодный хост → 400 JSON.
  GET  …/login/google/callback?state=&code=|error=  (ходит только Google)
       → 302 https://<host><return_to>#wwc-login=<одноразовый>  — личность привязана, хост в семье
                                                                 (только ссылка, cookie сессии не ставится);
         302 https://<host><return_to>#wwc-link=<pending>       — нужен код от Виктора; для витрины — всегда
                                                                 (браузер подтверждается ключом сайта в /login/link);
         302 https://<host><return_to>#wwc-link-error=<код>     — google_denied | google_token_invalid |
                                                                 email_unverified | code_locked;
       400 {error: google_state_invalid}; 400 {error: oauth_browser_mismatch} — хост в семье, а cookie wwc_oauth
       нет или чужая (чужая ссылка). Для витрины cookie не смотрится.
  GET  …/login/pending/{pending} → {ok, provider, email_masked, display_name, status: awaiting_code|awaiting_claim};
       404 pending_invalid | 410 pending_expired.
  POST …/login/link {pending_id, code?, browser_key?}
       Хост в семье — проверяется ТОЛЬКО cookie wwc_oauth (browser_key из body не помогает);
       витрина — ТОЛЬКО browser_key из body (cookie не помогает).
       → 200 {ok, status: "authenticated", expires_at} + cookie сессии;
         400 oauth_browser_mismatch | 400 code_required | 400 code_invalid | 410 code_expired | 409 code_used |
         429 code_locked | 409 identity_taken | 404 pending_invalid | 410 pending_expired.
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
from app.cookie_domain import cookie_domain_for
from app.login_identities import service
from app.login_identities.rules import OAUTH_COOKIE_NAME, OAUTH_COOKIE_TTL_SEC, LoginIdentityError
from app.settings import get_settings
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


def _set_oauth_cookie(response: Response, request: Request, key: str) -> None:
    response.set_cookie(
        key=OAUTH_COOKIE_NAME,
        value=key,
        max_age=OAUTH_COOKIE_TTL_SEC,
        path="/",
        httponly=True,
        secure=get_settings().platform_content_cookie_secure,
        samesite="lax",
        domain=cookie_domain_for(request),
    )


def _clear_oauth_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=OAUTH_COOKIE_NAME,
        path="/",
        httponly=True,
        secure=get_settings().platform_content_cookie_secure,
        samesite="lax",
        domain=cookie_domain_for(request),
    )


def _oauth_cookie(request: Request) -> str | None:
    value = request.cookies.get(OAUTH_COOKIE_NAME)
    return str(value).strip() or None if value else None


class LinkBody(BaseModel):
    pending_id: str
    code: str | None = None
    browser_key: str | None = None


# ---- Google ---------------------------------------------------------------------------------------


async def _google_start(
    request: Request, return_to: str | None, visitor_session_id: str | None, browser_key: str | None
) -> Response:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    host = service.request_host(request.headers)
    try:
        url, cookie_key = await service.google_start(
            tenant.tenant_id,
            host=host,
            return_to=return_to or "/",
            visitor_session_id=visitor_session_id,
            site_key=browser_key,
        )
    except LoginIdentityError as exc:
        if exc.code not in {"google_disabled", "invalid_return_to", "browser_key_required"}:
            raise _http(exc) from exc
        # Сюда приходит браузер, не XHR: JSON человек увидел бы текстом — возвращаем на страницу с кодом ошибки.
        try:
            target = await service.return_target(tenant.tenant_id, host=host, return_to=return_to or "/")
        except LoginIdentityError as inner:
            raise _http(inner) from inner
        return _redirect(service.link_error_url(target, exc.code))
    response = _redirect(url)
    if cookie_key:
        # Ключ cookie — только серверный; ключ сайта из query (витрины) в cookie не попадает никогда.
        _set_oauth_cookie(response, request, cookie_key)
    return response


@router.get(f"{API}/google/start")
@router.get(f"{V1}/google/start")
async def google_start(
    request: Request,
    return_to: str | None = None,
    visitor_session_id: str | None = None,
    browser_key: str | None = None,
) -> Response:
    return await _google_start(request, return_to, visitor_session_id, browser_key)


async def _google_callback(request: Request, state: str | None, code: str | None, error: str | None) -> Response:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    try:
        result = await service.google_callback(
            tenant.tenant_id,
            state=state or "",
            code=code,
            oauth_error=error,
            cookie_key=_oauth_cookie(request),
        )
    except LoginIdentityError as exc:
        raise _http(exc) from exc
    return_to = str(result["return_to"])
    if result["outcome"] == "error":
        return _redirect(service.link_error_url(return_to, str(result["error"])))
    if result["outcome"] == "link_required":
        return _redirect(service.link_url(return_to, str(result["link_token"])))
    telegram_user_id = int(result["telegram_user_id"])
    # Только #wwc-login=<одноразовый>: сайту нужен явный сигнал входа, сессию создаёт он сам, погасив
    # ссылку (BotLogin.astro). Cookie сессии здесь не ставится — одна сессия на вход, не две.
    url = await service.browser_login_url(tenant.tenant_id, telegram_user_id=telegram_user_id, return_to=return_to)
    response = _redirect(url or return_to)
    _clear_oauth_cookie(response, request)
    return response


@router.get(f"{API}/google/callback")
@router.get(f"{V1}/google/callback")
async def google_callback(
    request: Request, state: str | None = None, code: str | None = None, error: str | None = None
) -> Response:
    return await _google_callback(request, state, code, error)


# ---- экран кода после Google --------------------------------------------------------------------------


async def _pending(pending: str, request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = NO_STORE
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    try:
        info = await service.pending_info(tenant.tenant_id, link_token=pending)
    except LoginIdentityError as exc:
        raise _http(exc) from exc
    return {"ok": True, **info}


@router.get(f"{API}/pending/{{pending}}")
@router.get(f"{V1}/pending/{{pending}}")
async def pending(pending: str, request: Request, response: Response) -> dict[str, Any]:
    return await _pending(pending, request, response)


async def _link(body: LinkBody, request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = NO_STORE
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    try:
        # Cookie и ключ из body — раздельно: какой из них считается, решает строка pending (без подмены).
        linked = await service.link_pending(
            tenant.tenant_id,
            link_token=body.pending_id,
            code=body.code,
            cookie_key=_oauth_cookie(request),
            body_key=(body.browser_key or "").strip() or None,
        )
    except LoginIdentityError as exc:
        raise _http(exc) from exc
    payload, raw_session = await service.session_for(
        tenant.tenant_id,
        telegram_user_id=int(linked["telegram_user_id"]),
        visitor_session_id=linked.get("visitor_session_id"),
        source="google_login",
    )
    set_session_cookie(response, raw_session, request)
    _clear_oauth_cookie(response, request)
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
