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
  GET  …/login/yandex/start | /callback, …/login/vk/start | /callback — тот же поток, что у Google
       (ошибки: yandex_disabled | yandex_denied | yandex_token_invalid, vk_disabled | vk_denied | vk_token_invalid).
       VK в callback присылает device_id — он нужен при обмене кода.
  GET  …/login/methods (без сессии) → {passkey: true, google|yandex|vk: {enabled}} — какие кнопки рисовать гостю.
  Ключ доступа (WebAuthn), префикс …/login/passkey/:
       POST register/options | register/verify (под сессией), POST auth/options | auth/verify (без сессии:
       → {ok, return_to} + cookie сессии), GET …/login/passkeys и DELETE …/login/passkeys/{id} (под сессией).
  GET  …/me → поле login_methods: [{provider, label, linked_at}] (в content_access/routes.py).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.content_access import devices
from app.content_access.cookies import read_session_cookie, set_session_cookie
from app.content_access.routes import _current_session
from app.cookie_domain import cookie_domain_for
from app.content_access.service import sanitize_return_to, validate_content_session
from app.login_identities import passkeys, service
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


# ---- Google, Яндекс ID, VK ID (один поток: start → callback → #wwc-login | #wwc-link) -----------------


async def _session_user(request: Request, tenant_id: str) -> int | None:
    """Человек с действующей сессией сайта (привязка из кабинета без кода владельца) или None."""
    raw = read_session_cookie(request)
    if not raw:
        return None
    try:
        session = await validate_content_session(tenant_id, raw)
    except Exception:
        return None
    user_id = (session or {}).get("telegram_user_id")
    return int(user_id) if user_id is not None else None


async def _oauth_start(
    provider: str, request: Request, return_to: str | None, visitor_session_id: str | None, browser_key: str | None
) -> Response:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    host = service.request_host(request.headers)
    try:
        url, cookie_key = await getattr(service, f"{provider}_start")(
            tenant.tenant_id,
            host=host,
            return_to=return_to or "/",
            visitor_session_id=visitor_session_id,
            site_key=browser_key,
            session_user_id=await _session_user(request, tenant.tenant_id),
        )
    except LoginIdentityError as exc:
        if exc.code not in {f"{provider}_disabled", "invalid_return_to", "browser_key_required"}:
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


async def _oauth_callback(
    provider: str,
    request: Request,
    state: str | None,
    code: str | None,
    error: str | None,
    extra: dict[str, str] | None = None,
) -> Response:
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    kwargs: dict[str, Any] = {}
    if extra:
        kwargs["extra"] = extra
    try:
        result = await getattr(service, f"{provider}_callback")(
            tenant.tenant_id,
            state=state or "",
            code=code,
            oauth_error=error,
            cookie_key=_oauth_cookie(request),
            **kwargs,
        )
    except LoginIdentityError as exc:
        raise _http(exc) from exc
    return_to = str(result["return_to"])
    if result["outcome"] == "error":
        return _redirect(service.link_error_url(return_to, str(result["error"])))
    if result["outcome"] == "link_required":
        return _redirect(service.link_url(return_to, str(result["link_token"])))
    if result["outcome"] == "linked":
        # Привязка у вошедшего: сессия уже есть, входить заново не нужно — просто вернуть на страницу.
        response = _redirect(return_to)
        _clear_oauth_cookie(response, request)
        return response
    telegram_user_id = int(result["telegram_user_id"])
    # Только #wwc-login=<одноразовый>: сайту нужен явный сигнал входа, сессию создаёт он сам, погасив
    # ссылку (BotLogin.astro). Cookie сессии здесь не ставится — одна сессия на вход, не две.
    url = await service.browser_login_url(tenant.tenant_id, telegram_user_id=telegram_user_id, return_to=return_to)
    response = _redirect(url or return_to)
    _clear_oauth_cookie(response, request)
    return response


@router.get(f"{API}/google/start")
@router.get(f"{V1}/google/start")
async def google_start(
    request: Request,
    return_to: str | None = None,
    visitor_session_id: str | None = None,
    browser_key: str | None = None,
) -> Response:
    return await _oauth_start("google", request, return_to, visitor_session_id, browser_key)


@router.get(f"{API}/google/callback")
@router.get(f"{V1}/google/callback")
async def google_callback(
    request: Request, state: str | None = None, code: str | None = None, error: str | None = None
) -> Response:
    return await _oauth_callback("google", request, state, code, error)


@router.get(f"{API}/yandex/start")
@router.get(f"{V1}/yandex/start")
async def yandex_start(
    request: Request,
    return_to: str | None = None,
    visitor_session_id: str | None = None,
    browser_key: str | None = None,
) -> Response:
    return await _oauth_start("yandex", request, return_to, visitor_session_id, browser_key)


@router.get(f"{API}/yandex/callback")
@router.get(f"{V1}/yandex/callback")
async def yandex_callback(
    request: Request, state: str | None = None, code: str | None = None, error: str | None = None
) -> Response:
    return await _oauth_callback("yandex", request, state, code, error)


@router.get(f"{API}/vk/start")
@router.get(f"{V1}/vk/start")
async def vk_start(
    request: Request,
    return_to: str | None = None,
    visitor_session_id: str | None = None,
    browser_key: str | None = None,
) -> Response:
    return await _oauth_start("vk", request, return_to, visitor_session_id, browser_key)


@router.get(f"{API}/vk/callback")
@router.get(f"{V1}/vk/callback")
async def vk_callback(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
    device_id: str | None = None,
) -> Response:
    # device_id VK присылает в callback вместе с code: без него обмен кода не пройдёт.
    return await _oauth_callback("vk", request, state, code, error, extra={"device_id": device_id or ""})


# ---- какие способы входа включены (публично, для гостя) ----------------------------------------------


async def _methods(request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = NO_STORE
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    enabled = service.providers_enabled()
    # Ключ доступа работает без ключей провайдеров: браузер сам решает, умеет ли он WebAuthn.
    return {
        "passkey": True,
        "google": {"enabled": enabled["google"]},
        "yandex": {"enabled": enabled["yandex"]},
        "vk": {"enabled": enabled["vk"]},
    }


@router.get(f"{API}/methods")
@router.get(f"{V1}/methods")
async def methods(request: Request, response: Response) -> dict[str, Any]:
    return await _methods(request, response)


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
        source=f"{linked.get('provider') or 'google'}_login",
        user_agent=request.headers.get("user-agent", ""),
        client_ip_value=devices.client_ip(request.headers, request.client.host if request.client else None),
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


# ---- ключи доступа (WebAuthn) ---------------------------------------------------------------------------


class PasskeyBody(BaseModel):
    """Ответ браузера (``credential.toJSON()``) — либо в ``credential``, либо прямо в теле."""

    model_config = {"extra": "allow"}

    credential: dict[str, Any] | None = None
    label: str | None = None
    return_to: str | None = None


def _passkey_body(body: PasskeyBody) -> dict[str, Any]:
    return body.model_dump(exclude={"return_to"}, exclude_none=True)


async def _person(request: Request) -> tuple[Any, int]:
    session = await _current_session(request)
    tenant = get_request_tenant(request)
    telegram_user_id = session.get("telegram_user_id")
    if telegram_user_id is None:
        raise HTTPException(status_code=401, detail={"error": "content_session_invalid"})
    return tenant, int(telegram_user_id)


async def _passkey_register_options(request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = NO_STORE
    tenant, user_id = await _person(request)
    try:
        return await passkeys.registration_options(
            tenant.tenant_id, telegram_user_id=user_id, host=service.request_host(request.headers)
        )
    except LoginIdentityError as exc:
        raise _http(exc) from exc


async def _passkey_register_verify(body: PasskeyBody, request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = NO_STORE
    tenant, user_id = await _person(request)
    try:
        saved = await passkeys.register(
            tenant.tenant_id,
            telegram_user_id=user_id,
            body=_passkey_body(body),
            user_agent=request.headers.get("user-agent", ""),
        )
    except LoginIdentityError as exc:
        raise _http(exc) from exc
    return {"ok": True, **saved}


async def _passkey_auth_options(request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = NO_STORE
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    return await passkeys.authentication_options(tenant.tenant_id, host=service.request_host(request.headers))


async def _passkey_auth_verify(body: PasskeyBody, request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = NO_STORE
    tenant = get_request_tenant(request)
    require_entitlement(tenant, "structure_basic")
    try:
        person = await passkeys.authenticate(tenant.tenant_id, body=_passkey_body(body))
    except LoginIdentityError as exc:
        raise _http(exc) from exc
    return_to = "/"
    if body.return_to:
        try:
            return_to = sanitize_return_to(body.return_to)
        except HTTPException:
            return_to = "/"
    _, raw_session = await service.session_for(
        tenant.tenant_id,
        telegram_user_id=int(person["telegram_user_id"]),
        visitor_session_id=None,
        source="passkey_login",
        user_agent=request.headers.get("user-agent", ""),
        client_ip_value=devices.client_ip(request.headers, request.client.host if request.client else None),
    )
    set_session_cookie(response, raw_session, request)
    return {"ok": True, "return_to": return_to}


async def _passkeys_list(request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = NO_STORE
    tenant, user_id = await _person(request)
    return {"ok": True, "passkeys": await passkeys.list_passkeys(tenant.tenant_id, user_id)}


async def _passkey_delete(passkey_id: str, request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = NO_STORE
    tenant, user_id = await _person(request)
    if not await passkeys.delete_passkey(tenant.tenant_id, telegram_user_id=user_id, passkey_id=passkey_id):
        raise HTTPException(status_code=404, detail={"error": "passkey_not_found"})
    return {"ok": True}


PK = "/passkey"


@router.post(f"{API}{PK}/register/options")
@router.post(f"{V1}{PK}/register/options")
async def passkey_register_options(request: Request, response: Response) -> dict[str, Any]:
    return await _passkey_register_options(request, response)


@router.post(f"{API}{PK}/register/verify")
@router.post(f"{V1}{PK}/register/verify")
async def passkey_register_verify(body: PasskeyBody, request: Request, response: Response) -> dict[str, Any]:
    return await _passkey_register_verify(body, request, response)


@router.post(f"{API}{PK}/auth/options")
@router.post(f"{V1}{PK}/auth/options")
async def passkey_auth_options(request: Request, response: Response) -> dict[str, Any]:
    return await _passkey_auth_options(request, response)


@router.post(f"{API}{PK}/auth/verify")
@router.post(f"{V1}{PK}/auth/verify")
async def passkey_auth_verify(body: PasskeyBody, request: Request, response: Response) -> dict[str, Any]:
    return await _passkey_auth_verify(body, request, response)


@router.get(f"{API}/passkeys")
@router.get(f"{V1}/passkeys")
@router.get(f"{API}{PK}/passkeys")
@router.get(f"{V1}{PK}/passkeys")
async def passkeys_list(request: Request, response: Response) -> dict[str, Any]:
    return await _passkeys_list(request, response)


@router.delete(f"{API}/passkeys/{{passkey_id}}")
@router.delete(f"{V1}/passkeys/{{passkey_id}}")
@router.delete(f"{API}{PK}/passkeys/{{passkey_id}}")
@router.delete(f"{V1}{PK}/passkeys/{{passkey_id}}")
async def passkey_delete(passkey_id: str, request: Request, response: Response) -> dict[str, Any]:
    return await _passkey_delete(passkey_id, request, response)
