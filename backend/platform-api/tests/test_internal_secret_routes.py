"""F003 + F021 (security audit 2026-09-16/17): HTTP routes that act on a
client-named identity -- telegram-link-tokens/exchange, memory-facts,
onboarding -- require X-Platform-Internal-Secret (PLATFORM_INTERNAL_API_SECRET).
Unset secret = route closed. The Telegram webhook path is unaffected.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.identity.service import LinkTokenExchangeResult
from app.settings import get_settings

HOST = {"host": "wwc.best"}
SECRET = "real-internal-secret"
HDR = "X-Platform-Internal-Secret"

GATED = [
    ("post", "/v1/telegram-link-tokens/exchange", {"token": "t", "telegram_user_id": 1147735602}),
    ("get", "/v1/memory-facts/telegram_user/1147735602", None),
    ("put", "/v1/memory-facts", {"subject_type": "telegram_user", "subject_id": "1147735602", "fact_key": "k", "fact_value": {"a": 1}}),
    ("post", "/v1/onboarding/enroll", {"telegram_user_id": 1147735602}),
    ("post", "/v1/onboarding/command", {"telegram_user_id": 1147735602, "text": "/start"}),
]


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    yield
    get_settings.cache_clear()


async def _call(client, method, path, body, headers):
    fn = getattr(client, method)
    kwargs = {"headers": {**HOST, **headers}}
    if body is not None:
        kwargs["json"] = body
    return await fn(path, **kwargs)


@pytest.mark.parametrize("method,path,body", GATED)
async def test_route_closed_when_secret_unset(client, monkeypatch, method, path, body):
    monkeypatch.delenv("PLATFORM_INTERNAL_API_SECRET", raising=False)
    get_settings.cache_clear()
    resp = await _call(client, method, path, body, {})
    assert resp.status_code == 403
    assert resp.json()["error"] == "internal_secret_required"


@pytest.mark.parametrize("method,path,body", GATED)
async def test_wrong_secret_rejected_before_any_service_call(client, monkeypatch, method, path, body):
    """Negative scenario (ACCEPTANCE A04): knowing a victim's numeric Telegram id
    is not enough to read/write their facts, enroll them or bind their identity."""
    monkeypatch.setenv("PLATFORM_INTERNAL_API_SECRET", SECRET)
    get_settings.cache_clear()
    with (
        patch("app.identity.routes.exchange_telegram_link_token", new=AsyncMock()) as ex,
        patch("app.memory.routes.list_memory_facts", new=AsyncMock()) as lm,
        patch("app.memory.routes.upsert_memory_fact", new=AsyncMock()) as um,
        patch("app.onboarding.routes.enroll_user", new=AsyncMock()) as en,
        patch("app.onboarding.routes.handle_onboarding_text", new=AsyncMock()) as ho,
    ):
        resp = await _call(client, method, path, body, {HDR: "guessed-wrong"})
    assert resp.status_code == 403
    for m in (ex, lm, um, en, ho):
        m.assert_not_awaited()


async def test_correct_secret_still_serves_each_route(client, monkeypatch):
    """Positive control: the legitimate server-to-server caller keeps working."""
    monkeypatch.setenv("PLATFORM_INTERNAL_API_SECRET", SECRET)
    get_settings.cache_clear()
    exchange_result = LinkTokenExchangeResult(
        link_id="11111111-1111-1111-1111-111111111111",
        visitor_session_id="22222222-2222-2222-2222-222222222222",
        first_ref="olga-samtsova",
        attributed_owner_id="olga-samtsova",
        mentor_display_name="Ольга",
        journey_type="referral",
        context={},
    )
    with (
        patch("app.identity.routes.exchange_telegram_link_token", new=AsyncMock(return_value=exchange_result)),
        patch("app.memory.routes.list_memory_facts", new=AsyncMock(return_value=[])),
        patch("app.memory.routes.upsert_memory_fact", new=AsyncMock(return_value={"ok": True})),
        patch("app.onboarding.routes.enroll_user", new=AsyncMock(return_value={"ok": True})),
        patch("app.onboarding.routes.handle_onboarding_text", new=AsyncMock(return_value={"ok": True})),
    ):
        for method, path, body in GATED:
            resp = await _call(client, method, path, body, {HDR: SECRET})
            assert resp.status_code == 200, (path, resp.text)
            assert resp.json()["ok"] is True
