from __future__ import annotations

from fastapi import Request, Response

from app.cookie_domain import cookie_domain_for
from app.settings import get_settings


def set_session_cookie(response: Response, raw_session: str, request: Request | None = None) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.platform_content_cookie_name,
        value=raw_session,
        httponly=True,
        secure=settings.platform_content_cookie_secure,
        samesite=settings.platform_content_cookie_samesite,
        max_age=settings.platform_content_session_ttl_days * 24 * 60 * 60,
        path="/",
        domain=cookie_domain_for(request),
    )


def clear_session_cookie(response: Response, request: Request | None = None) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.platform_content_cookie_name,
        path="/",
        httponly=True,
        secure=settings.platform_content_cookie_secure,
        samesite=settings.platform_content_cookie_samesite,
        domain=cookie_domain_for(request),
    )


def read_session_cookie(request: Request) -> str | None:
    settings = get_settings()
    value = request.cookies.get(settings.platform_content_cookie_name)
    if not value:
        return None
    return str(value).strip() or None
