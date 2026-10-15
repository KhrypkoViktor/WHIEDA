"""Google OAuth 2.0 (authorization code + PKCE) — адрес входа и обмен кода на id_token.

Настройки: PLATFORM_GOOGLE_OAUTH_CLIENT_ID / _SECRET / _REDIRECT. Все три пустые → Google-вход
выключен (``google_disabled``). Секрет никуда не логируется и не печатается.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.login_identities.rules import GOOGLE_AUTH_URL, GOOGLE_SCOPE, GOOGLE_TOKEN_URL, error
from app.settings import get_settings

logger = logging.getLogger("whieda.login_identities.google")

EXCHANGE_TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class GoogleOAuthConfig:
    client_id: str
    client_secret: str
    redirect_uri: str


def google_config() -> GoogleOAuthConfig | None:
    settings = get_settings()
    client_id = str(settings.platform_google_oauth_client_id or "").strip()
    secret = str(settings.platform_google_oauth_client_secret or "").strip()
    redirect = str(settings.platform_google_oauth_redirect or "").strip()
    if not (client_id and secret and redirect):
        return None
    return GoogleOAuthConfig(client_id=client_id, client_secret=secret, redirect_uri=redirect)


def require_google_config() -> GoogleOAuthConfig:
    config = google_config()
    if config is None:
        raise error("google_disabled")
    return config


def build_auth_url(config: GoogleOAuthConfig, *, state: str, nonce: str, code_challenge: str) -> str:
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": GOOGLE_SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # Человек выбирает аккаунт сам: на общем компьютере иначе входит чужой Google.
        "prompt": "select_account",
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code(config: GoogleOAuthConfig, *, code: str, code_verifier: str) -> dict[str, Any]:
    """Ответ token endpoint ({id_token, access_token, …}) или LoginIdentityError('google_token_invalid')."""
    form = {
        "code": code,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "redirect_uri": config.redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
    }
    try:
        async with httpx.AsyncClient(timeout=EXCHANGE_TIMEOUT_SEC) as client:
            response = await client.post(GOOGLE_TOKEN_URL, data=form)
    except httpx.HTTPError as exc:
        logger.warning("google_token_exchange_failed", extra={"reason": type(exc).__name__})
        raise error("google_token_invalid", reason="network") from exc
    if response.status_code != 200:
        logger.warning("google_token_exchange_rejected", extra={"status_code": response.status_code})
        raise error("google_token_invalid", reason="status")
    try:
        payload = response.json()
    except ValueError as exc:
        raise error("google_token_invalid", reason="json") from exc
    if not isinstance(payload, dict) or not payload.get("id_token"):
        raise error("google_token_invalid", reason="id_token")
    return payload
