"""Which ``Domain`` a session cookie should carry.

Until 18.09.2026 session cookies were host-only: a login on ``wwc.best`` did
not count on ``dev.wwc.best``, ``staging.wwc.best`` or any partner subdomain,
so the owner logged in again on every host he opened after a release (29
logins in 30 days, 19 sessions alive at once). One tenant, one login: when the
request host is a shared platform domain or a subdomain of it, the cookie is
issued for the whole family (``.wwc.best``). Any other host — localhost, a
test client, a future custom domain — keeps the host-only cookie.
"""

from __future__ import annotations

from typing import Any

from app.settings import get_settings


def _request_host(request: Any) -> str:
    headers = getattr(request, "headers", None) or {}
    raw = headers.get("x-forwarded-host") or headers.get("host") or ""
    host = str(raw).split(",", 1)[0].strip().lower()
    if host.startswith("["):  # IPv6 literal — never a shared domain
        return ""
    return host.split(":", 1)[0]


def shared_cookie_domains() -> tuple[str, ...]:
    raw = str(get_settings().platform_cookie_shared_domains or "")
    return tuple(d.strip().lower().lstrip(".") for d in raw.split(",") if d.strip())


def cookie_domain_for(request: Any) -> str | None:
    """``.wwc.best`` for ``wwc.best`` and every ``*.wwc.best``; ``None`` otherwise."""
    host = _request_host(request) if request is not None else ""
    if not host:
        return None
    for domain in shared_cookie_domains():
        if host == domain or host.endswith("." + domain):
            return "." + domain
    return None
