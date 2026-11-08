"""VK ID (OAuth 2.1, authorization code + обязательный PKCE) — адрес входа, обмен кода, профиль.

Настройки: PLATFORM_VK_ID_CLIENT_ID / _REDIRECT обязательны, _SECRET — если приложение его выдало
(для VK ID с PKCE обмен кода идёт без секрета). Пустые id или redirect → вход выключен (``vk_disabled``).
В callback VK присылает ``device_id`` — он нужен при обмене кода. Токены никуда не логируются.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from app.login_identities.rules import VK_AUTH_URL, VK_SCOPE, VK_TOKEN_URL, VK_USER_INFO_URL, error
from app.settings import get_settings

logger = logging.getLogger("whieda.login_identities.vk")

EXCHANGE_TIMEOUT_SEC = 10.0


@dataclass(frozen=True)
class VkOAuthConfig:
    client_id: str
    client_secret: str = field(default="", repr=False)
    redirect_uri: str = ""


def vk_config() -> VkOAuthConfig | None:
    settings = get_settings()
    client_id = str(settings.platform_vk_id_client_id or "").strip()
    secret = str(settings.platform_vk_id_client_secret or "").strip()
    redirect = str(settings.platform_vk_id_redirect or "").strip()
    if not (client_id and redirect):
        return None
    return VkOAuthConfig(client_id=client_id, client_secret=secret, redirect_uri=redirect)


def require_vk_config() -> VkOAuthConfig:
    config = vk_config()
    if config is None:
        raise error("vk_disabled")
    return config


def build_auth_url(config: VkOAuthConfig, *, state: str, code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "scope": VK_SCOPE,
    }
    return f"{VK_AUTH_URL}?{urlencode(params)}"


async def exchange_code(
    config: VkOAuthConfig, *, code: str, code_verifier: str, device_id: str, state: str
) -> dict[str, Any]:
    """Ответ token endpoint ({access_token, user_id, …}) или LoginIdentityError('vk_token_invalid')."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": code_verifier,
        "client_id": config.client_id,
        "device_id": device_id,
        "redirect_uri": config.redirect_uri,
        "state": state,
    }
    if config.client_secret:
        form["client_secret"] = config.client_secret
    try:
        async with httpx.AsyncClient(timeout=EXCHANGE_TIMEOUT_SEC) as client:
            response = await client.post(VK_TOKEN_URL, data=form)
    except httpx.HTTPError as exc:
        logger.warning("vk_token_exchange_failed", extra={"reason": type(exc).__name__})
        raise error("vk_token_invalid", reason="network") from exc
    if response.status_code != 200:
        logger.warning("vk_token_exchange_rejected", extra={"status_code": response.status_code})
        raise error("vk_token_invalid", reason="status")
    try:
        payload = response.json()
    except ValueError as exc:
        raise error("vk_token_invalid", reason="json") from exc
    if not isinstance(payload, dict) or not payload.get("access_token") or payload.get("error"):
        raise error("vk_token_invalid", reason="access_token")
    return payload


async def fetch_profile(config: VkOAuthConfig, tokens: dict[str, Any]) -> dict[str, Any]:
    """{sub, email, name}: user_info → user.user_id (запасной — user_id из ответа на обмен), email если дан."""
    try:
        async with httpx.AsyncClient(timeout=EXCHANGE_TIMEOUT_SEC) as client:
            response = await client.post(
                VK_USER_INFO_URL, data={"client_id": config.client_id, "access_token": str(tokens["access_token"])}
            )
    except httpx.HTTPError as exc:
        logger.warning("vk_profile_failed", extra={"reason": type(exc).__name__})
        raise error("vk_token_invalid", reason="profile_network") from exc
    if response.status_code != 200:
        logger.warning("vk_profile_rejected", extra={"status_code": response.status_code})
        raise error("vk_token_invalid", reason="profile_status")
    try:
        body = response.json()
    except ValueError as exc:
        raise error("vk_token_invalid", reason="profile_json") from exc
    user = body.get("user") if isinstance(body, dict) else None
    if not isinstance(user, dict):
        raise error("vk_token_invalid", reason="profile_shape")
    sub = str(user.get("user_id") or tokens.get("user_id") or "").strip()
    if not sub:
        raise error("vk_token_invalid", reason="profile_id")
    email = str(user.get("email") or "").strip().lower() or None
    first = str(user.get("first_name") or "").strip()
    last = str(user.get("last_name") or "").strip()
    name = " ".join(part for part in (first, last) if part) or None
    return {"sub": sub, "email": email, "name": name}
