"""F003 (security audit 2026-09-16): POST /v1/telegram-link-tokens/exchange took
telegram_user_id straight from the request body with no proof the caller controls
that Telegram account -- any site visitor could bind an arbitrary telegram_user_id
(their own known victim's) to a session they control. Telegram's own webhook path
(app.telegram.processor.handle_start_token) calls the same service function with a
verified msg.user_id and is unaffected by this gate; this file only covers the
public HTTP route.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.identity.service import LinkTokenExchangeResult
from app.settings import get_settings

EXCHANGE_PATH = "/v1/telegram-link-tokens/exchange"
HOST = {"host": "wwc.best"}


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    yield
    get_settings.cache_clear()


async def _post_exchange(client, *, telegram_user_id: int = 999999, headers: dict | None = None):
    return await client.post(
        EXCHANGE_PATH,
        json={"token": "whatever-token", "telegram_user_id": telegram_user_id},
        headers={**HOST, **(headers or {})},
    )


@pytest.mark.asyncio
async def test_no_secret_configured_route_is_closed(client, monkeypatch):
    monkeypatch.delenv("PLATFORM_IDENTITY_EXCHANGE_SECRET", raising=False)
    get_settings.cache_clear()

    resp = await _post_exchange(client)

    assert resp.status_code == 403
    assert resp.json()["error"] == "exchange_forbidden"


@pytest.mark.asyncio
async def test_arbitrary_visitor_without_secret_cannot_bind_victim_telegram_id(client, monkeypatch):
    """Negative scenario (ACCEPTANCE A04): an attacker who knows a victim's numeric
    Telegram user id, but holds no server secret, must not be able to bind it."""
    monkeypatch.setenv("PLATFORM_IDENTITY_EXCHANGE_SECRET", "real-internal-secret")
    get_settings.cache_clear()

    with patch("app.identity.routes.exchange_telegram_link_token", new=AsyncMock()) as exchange:
        resp = await _post_exchange(client, telegram_user_id=1147735602, headers={"X-Platform-Identity-Secret": "guessed-wrong"})

    assert resp.status_code == 403
    exchange.assert_not_awaited()


@pytest.mark.asyncio
async def test_correct_internal_secret_still_exchanges(client, monkeypatch):
    """Positive control: the legitimate server-to-server caller, holding the real
    secret, still gets a normal exchange result."""
    monkeypatch.setenv("PLATFORM_IDENTITY_EXCHANGE_SECRET", "real-internal-secret")
    get_settings.cache_clear()

    fake_result = LinkTokenExchangeResult(
        link_id="11111111-1111-1111-1111-111111111111",
        visitor_session_id="22222222-2222-2222-2222-222222222222",
        first_ref="olga-samtsova",
        attributed_owner_id="olga-samtsova",
        mentor_display_name="Ольга",
        journey_type="referral",
        context={},
    )
    with patch("app.identity.routes.exchange_telegram_link_token", new=AsyncMock(return_value=fake_result)) as exchange:
        resp = await _post_exchange(
            client,
            telegram_user_id=1147735602,
            headers={"X-Platform-Identity-Secret": "real-internal-secret"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["first_ref"] == "olga-samtsova"
    exchange.assert_awaited_once()
