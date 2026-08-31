"""Public content-access challenge — not admin cabinet auth."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.content_access.service import (
    CONTENT_START_PREFIX,
    build_content_deep_link,
    normalize_content_scope,
    sanitize_content_key,
    sanitize_return_to,
)
from app.main import create_app
from app.settings import get_settings
from app.tenancy import TenantContext

HOST = {"host": "wwc.best"}
NONCE = "browser-nonce-0123456789ab"
RETURN_TO = "/articles/krasnye-sledy-na-anionnyh-stelkah/"
CONTENT_KEY = "article/krasnye-sledy-na-anionnyh-stelkah/appendix"


def test_sanitize_return_to_allows_article_path():
    assert sanitize_return_to(RETURN_TO) == RETURN_TO


def test_sanitize_return_to_allows_reviews_path():
    assert sanitize_return_to("/reviews/") == "/reviews/"
    assert sanitize_return_to("/reviews/?tag=wentong") == "/reviews/?tag=wentong"
    assert sanitize_return_to("/reviews/?story=insoles-1") == "/reviews/?story=insoles-1"


def test_sanitize_return_to_allows_catalog_path():
    assert sanitize_return_to("/catalog/anion-insoles/") == "/catalog/anion-insoles/"
    assert sanitize_return_to("/catalog/elixir-fohou/") == "/catalog/elixir-fohou/"


def test_sanitize_return_to_allows_home_and_theme_settings():
    assert sanitize_return_to("/") == "/"
    assert sanitize_return_to("/en/") == "/en/"
    assert sanitize_return_to("/settings/theme/") == "/settings/theme/"


def test_theme_customization_scope_reuses_verified_telegram_session():
    assert normalize_content_scope("theme_customization") == "telegram_verified"
    assert normalize_content_scope("telegram_verified") == "telegram_verified"


def test_sanitize_content_key_allows_review_original():
    assert sanitize_content_key("review/insoles-1/original") == "review/insoles-1/original"
    assert sanitize_content_key("review/archive/originals") == "review/archive/originals"


def test_sanitize_content_key_allows_product_evidence():
    assert sanitize_content_key("product/evidence/awards-patents") == "product/evidence/awards-patents"


def test_sanitize_return_to_rejects_cabinet():
    with pytest.raises(HTTPException) as exc:
        sanitize_return_to("/cabinet/overview/")
    assert exc.value.status_code == 400
    assert exc.value.detail["error"] == "invalid_return_to"


def test_sanitize_return_to_rejects_external():
    with pytest.raises(HTTPException) as exc:
        sanitize_return_to("https://evil.example/articles/x/")
    assert exc.value.status_code == 400


def test_sanitize_return_to_rejects_protocol_relative():
    with pytest.raises(HTTPException) as exc:
        sanitize_return_to("//evil.example/articles/x/")
    assert exc.value.status_code == 400


def test_sanitize_content_key_rejects_path_escape():
    with pytest.raises(HTTPException) as exc:
        sanitize_content_key("../secrets/file.pdf")
    assert exc.value.status_code == 400


def test_build_content_deep_link_uses_prefix():
    link = build_content_deep_link("@WHIEDA_Advisor_bot", "tokABC")
    assert link == "https://t.me/WHIEDA_Advisor_bot?start=content_access_tokABC"
    assert CONTENT_START_PREFIX == "content_access_"


@pytest.fixture
def content_app(monkeypatch):
    monkeypatch.setenv("PLATFORM_TELEGRAM_BOT_USERNAME", "WHIEDA_Advisor_bot")
    monkeypatch.setenv("PLATFORM_CONTENT_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    monkeypatch.setattr("app.main.init_pool", AsyncMock())
    monkeypatch.setattr("app.main.close_pool", AsyncMock())
    monkeypatch.setattr("app.main.check_postgres", AsyncMock(return_value=True))

    async def resolve(host: str) -> TenantContext:
        from app.tenancy import normalize_host

        if normalize_host(host) in {"wwc.best", "test"}:
            return TenantContext(
                tenant_id="whieda",
                status="active",
                display_name="WHIEDA",
                entitlements={"structure_basic": True, "partner_leads": True, "deep_coach": False},
            )
        from fastapi import HTTPException as FastAPIHTTPException

        raise FastAPIHTTPException(status_code=404, detail={"error": "tenant_not_found"})

    monkeypatch.setattr("app.tenancy.resolve_tenant_from_host", resolve)
    application = create_app()
    application.state.http_client = AsyncMock()
    yield application
    get_settings.cache_clear()


@pytest.fixture
async def content_client(content_app):
    transport = ASGITransport(app=content_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_create_challenge_requires_bot_username(monkeypatch, content_app):
    monkeypatch.delenv("PLATFORM_TELEGRAM_BOT_USERNAME", raising=False)
    get_settings.cache_clear()
    transport = ASGITransport(app=content_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/content-access/challenges",
            headers=HOST,
            json={"browser_nonce": NONCE, "return_to": RETURN_TO, "scope": "telegram_verified"},
        )
    assert response.status_code == 503
    assert response.json()["error"] == "telegram_bot_username_not_configured"


@pytest.mark.asyncio
async def test_create_challenge_returns_prefixed_deep_link(content_client):
    created = {
        "ok": True,
        "challenge_id": "11111111-1111-1111-1111-111111111111",
        "expires_at": datetime.now(timezone.utc).isoformat(),
        "deep_link": "https://t.me/WHIEDA_Advisor_bot?start=content_access_abc",
        "poll_interval_sec": 2,
    }
    with patch("app.content_access.routes.create_content_challenge", AsyncMock(return_value=created)):
        response = await content_client.post(
            "/api/v1/content-access/challenges",
            headers=HOST,
            json={"browser_nonce": NONCE, "return_to": RETURN_TO, "scope": "telegram_verified"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "content_access_" in body["deep_link"]
    assert "telegram_user_id" not in body
    assert "token" not in body
    assert "session" not in str(body).lower() or "poll" in str(body).lower()


@pytest.mark.asyncio
async def test_create_challenge_dual_prefix(content_client):
    created = {
        "ok": True,
        "challenge_id": "11111111-1111-1111-1111-111111111111",
        "expires_at": datetime.now(timezone.utc).isoformat(),
        "deep_link": "https://t.me/WHIEDA_Advisor_bot?start=content_access_abc",
        "poll_interval_sec": 2,
    }
    with patch("app.content_access.routes.create_content_challenge", AsyncMock(return_value=created)):
        site = await content_client.post(
            "/api/v1/content-access/challenges",
            headers=HOST,
            json={"browser_nonce": NONCE, "return_to": RETURN_TO},
        )
        core = await content_client.post(
            "/v1/content-access/challenges",
            headers=HOST,
            json={"browser_nonce": NONCE, "return_to": RETURN_TO},
        )
    assert site.status_code == 200
    assert core.status_code == 200


@pytest.mark.asyncio
async def test_poll_pending_does_not_set_cookie(content_client):
    with patch(
        "app.content_access.routes.poll_content_challenge",
        AsyncMock(return_value=({"ok": True, "status": "pending"}, None)),
    ):
        response = await content_client.get(
            "/api/v1/content-access/challenges/11111111-1111-1111-1111-111111111111",
            headers={**HOST, "X-Browser-Nonce": NONCE},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert "wwc_content_session" not in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_poll_approved_sets_http_only_cookie_not_in_body(content_client):
    expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    with patch(
        "app.content_access.routes.poll_content_challenge",
        AsyncMock(return_value=({"ok": True, "status": "authenticated", "expires_at": expires}, "raw-session-token")),
    ):
        response = await content_client.get(
            "/api/v1/content-access/challenges/11111111-1111-1111-1111-111111111111",
            headers={**HOST, "X-Browser-Nonce": NONCE},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "authenticated"
    assert "raw-session-token" not in str(body)
    assert "telegram_user_id" not in body
    cookie = response.headers.get("set-cookie", "")
    assert "wwc_content_session=" in cookie
    assert "HttpOnly" in cookie
    assert "wwc_admin_session" not in cookie


@pytest.mark.asyncio
async def test_me_without_cookie_is_unauthorized(content_client):
    response = await content_client.get("/api/v1/content-access/me", headers=HOST)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_materials_without_cookie_is_unauthorized(content_client):
    response = await content_client.get(
        f"/api/v1/content-access/materials/{CONTENT_KEY}",
        headers=HOST,
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_materials_with_session_returns_body_without_telegram_id(content_client):
    payload = {
        "ok": True,
        "content_key": CONTENT_KEY,
        "kind": "article",
        "scope": "telegram_verified",
        "title": "Приложение",
        "body_html": "<p>Закрытый абзац</p>",
    }
    with patch(
        "app.content_access.routes.read_session_cookie",
        return_value="raw-session-token",
    ):
        with patch(
            "app.content_access.routes.validate_content_session",
            AsyncMock(
                return_value={
                    "session_id": "s1",
                    "tenant_id": "whieda",
                    "scope": "telegram_verified",
                    "expires_at": datetime.now(timezone.utc) + timedelta(days=30),
                    "telegram_user_id": 999001,
                }
            ),
        ):
            with patch("app.content_access.routes.load_material", AsyncMock(return_value=payload)):
                response = await content_client.get(
                    f"/api/v1/content-access/materials/{CONTENT_KEY}",
                    headers=HOST,
                )
    assert response.status_code == 200
    body = response.json()
    assert "Закрытый абзац" in body["body_html"]
    assert "telegram_user_id" not in body
    assert "999001" not in str(body)


@pytest.mark.asyncio
async def test_logout_clears_content_cookie_not_admin(content_client):
    with patch("app.content_access.routes.read_session_cookie", return_value="raw-session-token"):
        with patch("app.content_access.routes.revoke_content_session", AsyncMock(return_value=True)):
            response = await content_client.post("/api/v1/content-access/logout", headers=HOST)
    assert response.status_code == 200
    cookie = response.headers.get("set-cookie", "")
    assert "wwc_content_session=" in cookie
    assert "wwc_admin_session" not in cookie
