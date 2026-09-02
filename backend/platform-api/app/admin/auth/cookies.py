from __future__ import annotations

from fastapi import Response

from app.settings import get_settings


def set_session_cookie(response: Response, raw_session: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.platform_admin_cookie_name,
        value=raw_session,
        httponly=True,
        secure=settings.platform_admin_cookie_secure,
        samesite=settings.platform_admin_cookie_samesite,
        max_age=settings.platform_admin_session_ttl_minutes * 60,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.platform_admin_cookie_name,
        path="/",
        httponly=True,
        secure=settings.platform_admin_cookie_secure,
        samesite=settings.platform_admin_cookie_samesite,
    )


def read_session_cookie(request) -> str | None:
    settings = get_settings()
    value = request.cookies.get(settings.platform_admin_cookie_name)
    if not value:
        return None
    return str(value).strip() or None
