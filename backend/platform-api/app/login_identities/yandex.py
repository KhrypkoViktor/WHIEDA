"""Яндекс ID (OAuth 2.0, authorization code + PKCE) — адрес входа, обмен кода, профиль.

Настройки: PLATFORM_YANDEX_OAUTH_CLIENT_ID / _SECRET / _REDIRECT. Любая пустая → вход выключен
(``yandex_disabled``). Секрет и токены никуда не логируются. Яндекс отдаёт только подтверждённые адреса,
поэтому отдельной проверки email_verified нет.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from app.login_identities.rules import YANDEX_AUTH_URL, YANDEX_INFO_URL, YANDEX_SCOPE, YANDEX_TOKEN_URL, error
from app.settings import get_settings

logger = logging.getLogger("whieda.login_identities.yandex")

EXCHANGE_TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class YandexOAuthConfig:
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str = ""


def yandex_config() -> YandexOAuthConfig | None:
    settings = get_settings()
    client_id = str(settings.platform_yandex_oauth_client_id or "").strip()
    secret = str(settings.platform_yandex_oauth_client_secret or "").strip()
    redirect = str(settings.platform_yandex_oauth_redirect or "").strip()
    if not (client_id and secret and redirect):
        return None
    return YandexOAuthConfig(client_id=client_id, client_secret=secret, redirect_uri=redirect)


def require_yandex_config() -> YandexOAuthConfig:
    config = yandex_config()
    if config is None:
        raise error("yandex_disabled")
    return config


def build_auth_url(config: YandexOAuthConfig, *, state: str, code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "scope": YANDEX_SCOPE,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        # Человек выбирает аккаунт сам: на общем компьютере иначе входит чужой Яндекс.
        "force_confirm": "yes",
    }
    return f"{YANDEX_AUTH_URL}?{urlencode(params)}"


async def exchange_code(config: YandexOAuthConfig, *, code: str, code_verifier: str) -> dict[str, Any]:
    """Ответ token endpoint ({access_token, …}) или LoginIdentityError('yandex_token_invalid')."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": config.client_id,
        "client_secret": config.client_secret,
        "code_verifier": code_verifier,
    }
    try:
        async with httpx.AsyncClient(timeout=EXCHANGE_TIMEOUT_SEC) as client:
            response = await client.post(YANDEX_TOKEN_URL, data=form)
    except httpx.HTTPError as exc:
        logger.warning("yandex_token_exchange_failed", extra={"reason": type(exc).__name__})
        raise error("yandex_token_invalid", reason="network") from exc
    if response.status_code != 200:
        logger.warning("yandex_token_exchange_rejected", extra={"status_code": response.status_code})
        raise error("yandex_token_invalid", reason="status")
    try:
        payload = response.json()
    except ValueError as exc:
        raise error("yandex_token_invalid", reason="json") from exc
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise error("yandex_token_invalid", reason="access_token")
    return payload


async def fetch_profile(access_token: str) -> dict[str, Any]:
    """{sub, email, name} из login.yandex.ru/info: id → sub, default_email → email, display_name|real_name → name."""
    try:
        async with httpx.AsyncClient(timeout=EXCHANGE_TIMEOUT_SEC) as client:
            response = await client.get(
                YANDEX_INFO_URL, params={"format": "json"}, headers={"Authorization": f"OAuth {access_token}"}
            )
    except httpx.HTTPError as exc:
        logger.warning("yandex_profile_failed", extra={"reason": type(exc).__name__})
        raise error("yandex_token_invalid", reason="profile_network") from exc
    if response.status_code != 200:
        logger.warning("yandex_profile_rejected", extra={"status_code": response.status_code})
        raise error("yandex_token_invalid", reason="profile_status")
    try:
        info = response.json()
    except ValueError as exc:
        raise error("yandex_token_invalid", reason="profile_json") from exc
    if not isinstance(info, dict):
        raise error("yandex_token_invalid", reason="profile_shape")
    sub = str(info.get("id") or "").strip()
    if not sub:
        raise error("yandex_token_invalid", reason="profile_id")
    email = str(info.get("default_email") or "").strip().lower() or None
    name = str(info.get("display_name") or info.get("real_name") or "").strip() or None
    return {"sub": sub, "email": email, "name": name}
