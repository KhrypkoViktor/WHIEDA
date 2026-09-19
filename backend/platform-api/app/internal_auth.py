from __future__ import annotations

from typing import Annotated

from fastapi import Header, HTTPException

from app.settings import get_settings

INTERNAL_SECRET_HEADER = "X-Platform-Internal-Secret"

InternalSecretHeader = Annotated[str | None, Header(alias=INTERNAL_SECRET_HEADER)]


def require_internal_secret(provided: str | None) -> None:
    """Gate for HTTP routes that act on a client-named identity (telegram_user_id,
    memory subject_id) without any proof the caller owns it. Only server-to-server
    callers holding PLATFORM_INTERNAL_API_SECRET may use them; unset secret = route
    closed, not open (security audit 2026-09-16, F003/F021)."""
    expected = get_settings().platform_internal_api_secret
    if not expected or not provided or provided != expected:
        raise HTTPException(status_code=403, detail={"ok": False, "error": "internal_secret_required"})
