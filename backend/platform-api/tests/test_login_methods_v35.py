"""Новые способы входа (V35, 08.11.2026): устройства, ключи доступа, Яндекс ID, VK ID, ручка methods.

База — подделки (настоящий SQL, лимит и весь поток ключа — tests/test_login_methods_postgres.py).
Ключ доступа проверяется настоящей библиотекой webauthn и программным аутентификатором
(tests/webauthn_testkit.py): подпись не мокается.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import webauthn
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.structs import AuthenticatorSelectionCriteria, ResidentKeyRequirement, UserVerificationRequirement

from app.content_access import devices
from app.login_identities import passkeys, rules, service, vk, yandex
from app.login_identities.rules import OAUTH_COOKIE_NAME, LoginIdentityError
from app.settings import get_settings
from tests.test_login_identities import HOST, SESSION, _recording_connection, start_db  # noqa: F401
from tests.webauthn_testkit import ORIGIN, RP_ID, SoftAuthenticator

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
EDGE = WINDOWS + " Edg/126.0.0.0"
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"


# ---- A: устройства -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("user_agent", "label"),
    [
        (IPHONE, "iPhone · Safari"),
        (ANDROID, "Android · Chrome"),
        (WINDOWS, "Windows · Chrome"),
        (EDGE, "Windows · Edge"),
        (MAC, "Mac · Safari"),
        ("Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0", "Linux · Firefox"),
        ("", "Устройство"),
        (None, "Устройство"),
        ("curl/8.0", "Устройство"),
    ],
)
def test_device_label_is_a_short_human_name(user_agent, label):
    assert devices.device_label(user_agent) == label


def test_ip_hint_keeps_only_a_prefix():
    assert devices.ip_hint("95.84.12.7") == "95.84.*.*"
    assert devices.ip_hint("::ffff:95.84.12.7") == "95.84.*.*"
    assert devices.ip_hint("2a02:6b8:1234:5678::1") == "2a02:6b8::/32"
    assert devices.ip_hint("not-an-ip") is None and devices.ip_hint(None) is None and devices.ip_hint("") is None
    assert "12.7" not in (devices.ip_hint("95.84.12.7") or ""), "full address is never stored"


def test_client_ip_prefers_the_proxy_header():
    assert devices.client_ip({"x-forwarded-for": "95.84.12.7, 10.0.0.1"}, "10.0.0.2") == "95.84.12.7"
    assert devices.client_ip({"x-real-ip": "95.84.12.8"}, "10.0.0.2") == "95.84.12.8"
    assert devices.client_ip({}, "10.0.0.2") == "10.0.0.2"


def test_short_id_is_eight_hex_chars():
    assert devices.short_id("12345678-aaaa-bbbb-cccc-1234567890ab") == "12345678"


def _fake_tenant_connection(conn):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def connection(_tenant):
        yield conn

    return connection


@pytest.mark.asyncio
async def test_sessions_route_marks_the_current_device(client):
    now = datetime.now(timezone.utc)
    rows = [
        {"id": "aaaaaaaa", "device_label": "iPhone · Safari", "created_at": now.isoformat(), "last_seen_at": now.isoformat(), "current": True},
        {"id": "bbbbbbbb", "device_label": "Windows · Chrome", "created_at": now.isoformat(), "last_seen_at": now.isoformat(), "current": False},
    ]
    with patch("app.content_access.routes.tenant_connection", _fake_tenant_connection(object())), \
         patch("app.content_access.routes.read_session_cookie", return_value="raw"), \
         patch("app.content_access.routes.validate_content_session", AsyncMock(return_value=SESSION)), \
         patch.object(devices, "list_active_sessions", AsyncMock(return_value=rows)) as listing:
        response = await client.get("/api/v1/content-access/sessions", headers=HOST)
    assert response.status_code == 200
    assert response.json() == {"ok": True, "limit": 2, "sessions": rows}
    assert response.headers["cache-control"] == "private, no-store"
    assert listing.await_args.args[1:3] == ("whieda", 700)
    assert listing.await_args.kwargs == {"current_session_id": "s1"}


@pytest.mark.asyncio
async def test_sessions_need_a_session(client):
    for method, path in (("get", "/api/v1/content-access/sessions"), ("post", "/api/v1/content-access/sessions/revoke-others"),
                         ("get", "/v1/content-access/sessions")):
        response = await getattr(client, method)(path, headers=HOST)
        assert response.status_code == 401, path


@pytest.mark.asyncio
async def test_revoke_others_keeps_the_current_session_and_drops_the_cache(client):
    with patch("app.content_access.routes.tenant_connection", _fake_tenant_connection(object())), \
         patch("app.content_access.routes.read_session_cookie", return_value="raw"), \
         patch("app.content_access.routes.validate_content_session", AsyncMock(return_value=SESSION)), \
         patch.object(devices, "revoke_other_sessions", AsyncMock(return_value=["h1", "h2"])) as revoke, \
         patch("app.content_access.routes._evict_cached_session") as evict:
        response = await client.post("/api/v1/content-access/sessions/revoke-others", headers=HOST)
    assert response.status_code == 200 and response.json() == {"ok": True, "revoked": 2}
    assert revoke.await_args.kwargs == {"current_session_id": "s1"}
    assert [call.args for call in evict.call_args_list] == [("whieda", "h1"), ("whieda", "h2")]


# ---- E: ручка methods и состояние способов входа -----------------------------------------------------


@pytest.fixture
def clean_providers(monkeypatch):
    for name in (
        "PLATFORM_GOOGLE_OAUTH_CLIENT_ID", "PLATFORM_GOOGLE_OAUTH_CLIENT_SECRET", "PLATFORM_GOOGLE_OAUTH_REDIRECT",
        "PLATFORM_YANDEX_OAUTH_CLIENT_ID", "PLATFORM_YANDEX_OAUTH_CLIENT_SECRET", "PLATFORM_YANDEX_OAUTH_REDIRECT",
        "PLATFORM_VK_ID_CLIENT_ID", "PLATFORM_VK_ID_CLIENT_SECRET", "PLATFORM_VK_ID_REDIRECT", "PLATFORM_MAX_BOT_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_methods_without_keys_offers_only_the_passkey(client, clean_providers):
    for path in ("/api/v1/content-access/login/methods", "/v1/content-access/login/methods"):
        response = await client.get(path, headers=HOST)  # без сессии и без cookie
        assert response.status_code == 200, path
        assert response.json() == {
            "passkey": True, "google": {"enabled": False}, "yandex": {"enabled": False}, "vk": {"enabled": False},
        }
        assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_methods_reports_a_provider_enabled_only_when_its_keys_are_set(client, clean_providers):
    clean_providers.setenv("PLATFORM_YANDEX_OAUTH_CLIENT_ID", "y-id")
    clean_providers.setenv("PLATFORM_YANDEX_OAUTH_CLIENT_SECRET", "y-secret")
    get_settings.cache_clear()
    body = (await client.get("/api/v1/content-access/login/methods", headers=HOST)).json()
    assert body["yandex"] == {"enabled": False}, "a redirect is missing"
    clean_providers.setenv("PLATFORM_YANDEX_OAUTH_REDIRECT", "https://wwc.best/api/v1/content-access/login/yandex/callback")
    clean_providers.setenv("PLATFORM_VK_ID_CLIENT_ID", "vk-id")
    clean_providers.setenv("PLATFORM_VK_ID_REDIRECT", "https://wwc.best/api/v1/content-access/login/vk/callback")
    get_settings.cache_clear()
    body = (await client.get("/api/v1/content-access/login/methods", headers=HOST)).json()
    assert body == {"passkey": True, "google": {"enabled": False}, "yandex": {"enabled": True}, "vk": {"enabled": True}}
    assert "y-secret" not in json.dumps(body)


def test_login_methods_state_has_every_provider_with_enabled_and_linked():
    methods = rules.login_methods_payload([{"provider": "max", "display_name": "Е", "linked_at": None}, {"provider": "yandex", "email": "e@ya.ru", "linked_at": None}])
    state = rules.login_methods_state(methods, enabled={"yandex": True, "google": False}, passkeys=2)
    assert state["telegram"]["linked"] is True and state["telegram"]["enabled"] is True
    assert (state["max"]["enabled"], state["max"]["linked"], state["max"]["label"]) == (False, True, "Е")
    assert (state["yandex"]["enabled"], state["yandex"]["linked"], state["yandex"]["label"]) == (True, True, "e@ya.ru")
    assert (state["google"]["enabled"], state["google"]["linked"], state["google"]["label"]) == (False, False, "Google")
    assert (state["vk"]["enabled"], state["vk"]["linked"]) == (False, False)
    assert state["passkeys"] == 2


def test_new_providers_are_known_revocable_and_labelled():
    assert rules.normalize_provider("Yandex") == "yandex" and rules.normalize_provider("VK") == "vk"
    assert {"yandex", "vk"} <= rules.REVOCABLE_PROVIDERS
    methods = rules.login_methods_payload([
        {"provider": "yandex", "email": "e@yandex.ru", "display_name": "Евгения", "linked_at": None},
        {"provider": "vk", "email": None, "display_name": "Евгения В.", "linked_at": None},
    ])
    assert [(m["provider"], m["label"]) for m in methods] == [("telegram", "Telegram"), ("yandex", "e@yandex.ru"), ("vk", "Евгения В.")]
    assert rules.parse_owner_command("отвязать vk volkova") == ("unlink", "vk", "volkova")
    assert rules.parse_owner_command("отвязать yandex volkova") == ("unlink", "yandex", "volkova")
    for code, status in (("yandex_disabled", 503), ("vk_disabled", 503), ("yandex_denied", 400), ("vk_token_invalid", 400),
                         ("passkey_failed", 401), ("passkey_unsupported", 400)):
        assert rules.error(code).status_code == status, code


@pytest.mark.asyncio
async def test_me_carries_the_state_of_all_methods_and_the_passkey_count(client, clean_providers):
    clean_providers.setenv("PLATFORM_VK_ID_CLIENT_ID", "vk-id")
    clean_providers.setenv("PLATFORM_VK_ID_REDIRECT", "https://wwc.best/api/v1/content-access/login/vk/callback")
    get_settings.cache_clear()
    from tests.fake_db import FakeDb

    methods = [{"provider": "telegram", "label": "Telegram", "linked_at": None}, {"provider": "vk", "label": "Е", "linked_at": None}]
    with patch("app.content_access.routes.tenant_connection", FakeDb().tenant_connection), \
         patch("app.content_access.routes.read_session_cookie", return_value="raw"), \
         patch("app.content_access.routes.validate_content_session", AsyncMock(return_value=SESSION)), \
         patch("app.content_access.routes.resolve_partner_subscription_by_telegram_user_id", AsyncMock(return_value=None)), \
         patch("app.content_access.routes.load_site_account", AsyncMock(return_value=None)), \
         patch("app.content_access.routes.lead_email_of", AsyncMock(return_value=None)), \
         patch("app.content_access.routes.count_passkeys", AsyncMock(return_value=3)), \
         patch("app.content_access.routes.list_login_methods", AsyncMock(return_value=methods)):
        body = (await client.get("/api/v1/content-access/me", headers=HOST)).json()
    state = body["login_methods"]
    assert state["vk"] == {"enabled": True, "linked": True, "label": "Е", "linked_at": None}
    assert state["yandex"]["enabled"] is False and state["yandex"]["linked"] is False
    assert state["telegram"] == {"enabled": True, "linked": True, "label": "Telegram", "linked_at": None}
    assert state["passkeys"] == 3


# ---- C/D: Яндекс ID и VK ID -------------------------------------------------------------------------------

YANDEX_REDIRECT = "https://wwc.best/api/v1/content-access/login/yandex/callback"
VK_REDIRECT = "https://wwc.best/api/v1/content-access/login/vk/callback"


@pytest.fixture
def oauth_env(clean_providers):
    clean_providers.setenv("PLATFORM_YANDEX_OAUTH_CLIENT_ID", "y-id")
    clean_providers.setenv("PLATFORM_YANDEX_OAUTH_CLIENT_SECRET", "y-secret")
    clean_providers.setenv("PLATFORM_YANDEX_OAUTH_REDIRECT", YANDEX_REDIRECT)
    clean_providers.setenv("PLATFORM_VK_ID_CLIENT_ID", "vk-id")
    clean_providers.setenv("PLATFORM_VK_ID_CLIENT_SECRET", "vk-secret")
    clean_providers.setenv("PLATFORM_VK_ID_REDIRECT", VK_REDIRECT)
    clean_providers.setenv("PLATFORM_COOKIE_SHARED_DOMAINS", "wwc.best")
    get_settings.cache_clear()
    yield clean_providers


def _mock_http(module, handler):
    real = httpx.AsyncClient

    def make(*_args, **kwargs):
        return real(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout"))

    return patch.object(module.httpx, "AsyncClient", make)


def test_configs_never_print_the_secret(oauth_env):
    assert "y-secret" not in repr(yandex.yandex_config()) and "vk-secret" not in repr(vk.vk_config())


def test_yandex_is_off_when_any_key_is_missing(clean_providers):
    clean_providers.setenv("PLATFORM_YANDEX_OAUTH_CLIENT_ID", "y-id")
    get_settings.cache_clear()
    assert yandex.yandex_config() is None
    with pytest.raises(LoginIdentityError) as info:
        yandex.require_yandex_config()
    assert info.value.code == "yandex_disabled" and info.value.status_code == 503


def test_vk_needs_only_id_and_redirect(clean_providers):
    clean_providers.setenv("PLATFORM_VK_ID_CLIENT_ID", "vk-id")
    get_settings.cache_clear()
    assert vk.vk_config() is None
    clean_providers.setenv("PLATFORM_VK_ID_REDIRECT", VK_REDIRECT)
    get_settings.cache_clear()
    config = vk.vk_config()
    assert config is not None and config.client_secret == ""


@pytest.mark.asyncio
async def test_yandex_start_redirects_with_pkce_and_a_browser_cookie(client, oauth_env, start_db):
    response = await client.get(
        "/api/v1/content-access/login/yandex/start", params={"return_to": "/me/"}, headers={"host": "wwc.best"}
    )
    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == "https://oauth.yandex.ru/authorize"
    query = parse_qs(location.query)
    assert query["response_type"] == ["code"] and query["client_id"] == ["y-id"]
    assert query["redirect_uri"] == [YANDEX_REDIRECT] and query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] and query["state"]
    assert "y-secret" not in response.headers["location"]
    assert response.headers["set-cookie"].startswith(f"{OAUTH_COOKIE_NAME}=")
    sql, params = start_db[0]
    assert sql.startswith("insert into login_pending") and params[1] == "yandex"
    assert params[2] == rules.sha256_hex(query["state"][0])


@pytest.mark.asyncio
async def test_vk_start_redirects_with_pkce_and_the_email_scope(client, oauth_env, start_db):
    response = await client.get(
        "/v1/content-access/login/vk/start", params={"return_to": "/me/"}, headers={"host": "wwc.best"}
    )
    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == "https://id.vk.com/authorize"
    query = parse_qs(location.query)
    assert query["scope"] == ["email"] and query["code_challenge_method"] == ["S256"]
    assert query["client_id"] == ["vk-id"] and query["redirect_uri"] == [VK_REDIRECT]
    assert start_db[0][1][1] == "vk"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["yandex", "vk"])
async def test_start_without_keys_returns_the_browser_with_a_disabled_code(client, clean_providers, provider):
    with patch.object(service, "warm_hosts", AsyncMock()):
        response = await client.get(
            f"/api/v1/content-access/login/{provider}/start", params={"return_to": "/me/"}, headers={"host": "wwc.best"}
        )
    assert response.status_code == 302
    assert response.headers["location"] == f"https://wwc.best/me/#wwc-link-error={provider}_disabled"


@pytest.mark.asyncio
async def test_yandex_exchange_and_profile_map_the_fields_as_the_tz_says(oauth_env):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth.yandex.ru":
            seen["token_form"] = parse_qs(request.content.decode())
            return httpx.Response(200, json={"access_token": "tok-1", "token_type": "bearer"})
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"id": "9001", "default_email": "Evgenia@Yandex.ru", "display_name": "Евгения В.", "real_name": "Евгения Волкова"})

    config = yandex.require_yandex_config()
    with _mock_http(yandex, handler):
        tokens = await yandex.exchange_code(config, code="c1", code_verifier="v1")
        profile = await yandex.fetch_profile(tokens["access_token"])
    assert seen["token_form"]["grant_type"] == ["authorization_code"] and seen["token_form"]["code_verifier"] == ["v1"]
    assert seen["token_form"]["client_secret"] == ["y-secret"]
    assert seen["auth"] == "OAuth tok-1" and "format=json" in seen["url"]
    assert profile == {"sub": "9001", "email": "evgenia@yandex.ru", "name": "Евгения В."}


@pytest.mark.asyncio
async def test_yandex_profile_falls_back_to_the_real_name_and_may_have_no_email(oauth_env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": 77, "real_name": "Евгения Волкова"})

    with _mock_http(yandex, handler):
        assert await yandex.fetch_profile("t") == {"sub": "77", "email": None, "name": "Евгения Волкова"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [(400, {"error": "invalid_grant"}), (200, {}), (200, {"access_token": ""}), (500, {})])
async def test_yandex_bad_token_answers_are_token_invalid(oauth_env, status, body):
    with _mock_http(yandex, lambda request: httpx.Response(status, json=body)):
        with pytest.raises(LoginIdentityError) as info:
            await yandex.exchange_code(yandex.require_yandex_config(), code="c", code_verifier="v")
    assert info.value.code == "yandex_token_invalid"


@pytest.mark.asyncio
async def test_yandex_network_failure_and_profile_without_id_are_token_invalid(oauth_env):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with _mock_http(yandex, boom):
        with pytest.raises(LoginIdentityError) as info:
            await yandex.exchange_code(yandex.require_yandex_config(), code="c", code_verifier="v")
    assert info.value.code == "yandex_token_invalid"
    with _mock_http(yandex, lambda request: httpx.Response(200, json={"default_email": "e@y.ru"})):
        with pytest.raises(LoginIdentityError) as info:
            await yandex.fetch_profile("t")
    assert info.value.code == "yandex_token_invalid"


@pytest.mark.asyncio
async def test_vk_exchange_sends_device_id_and_pkce_and_profile_maps_user_id(oauth_env):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/auth":
            seen["token_form"] = parse_qs(request.content.decode())
            return httpx.Response(200, json={"access_token": "vk-tok", "user_id": 555, "refresh_token": "r"})
        seen["info_form"] = parse_qs(request.content.decode())
        return httpx.Response(200, json={"user": {"user_id": "555", "first_name": "Евгения", "last_name": "Волкова", "email": "E@VK.com"}})

    config = vk.require_vk_config()
    with _mock_http(vk, handler):
        tokens = await vk.exchange_code(config, code="c1", code_verifier="v1", device_id="dev-1", state="st-1")
        profile = await vk.fetch_profile(config, tokens)
    form = seen["token_form"]
    assert form["grant_type"] == ["authorization_code"] and form["device_id"] == ["dev-1"]
    assert form["code_verifier"] == ["v1"] and form["state"] == ["st-1"] and form["redirect_uri"] == [VK_REDIRECT]
    assert seen["info_form"] == {"client_id": ["vk-id"], "access_token": ["vk-tok"]}
    assert profile == {"sub": "555", "email": "e@vk.com", "name": "Евгения Волкова"}


@pytest.mark.asyncio
async def test_vk_profile_without_email_and_with_user_id_only_from_the_token(oauth_env):
    with _mock_http(vk, lambda request: httpx.Response(200, json={"user": {"first_name": "Е"}})):
        profile = await vk.fetch_profile(vk.require_vk_config(), {"access_token": "t", "user_id": 42})
    assert profile == {"sub": "42", "email": None, "name": "Е"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [(400, {}), (200, {"error": "invalid_grant"}), (200, {})])
async def test_vk_bad_token_answers_are_token_invalid(oauth_env, status, body):
    with _mock_http(vk, lambda request: httpx.Response(status, json=body)):
        with pytest.raises(LoginIdentityError) as info:
            await vk.exchange_code(vk.require_vk_config(), code="c", code_verifier="v", device_id="d", state="s")
    assert info.value.code == "vk_token_invalid"


@pytest.mark.asyncio
async def test_exchange_claims_dispatches_by_provider_and_needs_the_vk_device_id(oauth_env):
    pending = {"code_verifier": "ver", "nonce_hash": "x"}
    with patch.object(yandex, "exchange_code", AsyncMock(return_value={"access_token": "t"})) as exchange, \
         patch.object(yandex, "fetch_profile", AsyncMock(return_value={"sub": "1", "email": None, "name": None})):
        claims = await service._exchange_claims("yandex", yandex.require_yandex_config(), code="c", pending=pending, state="s", extra={})
    assert claims["sub"] == "1" and exchange.await_args.kwargs == {"code": "c", "code_verifier": "ver"}
    with pytest.raises(LoginIdentityError) as info:
        await service._exchange_claims("vk", vk.require_vk_config(), code="c", pending=pending, state="s", extra={})
    assert info.value.code == "vk_token_invalid" and info.value.details == {"reason": "device_id"}
    with patch.object(vk, "exchange_code", AsyncMock(return_value={"access_token": "t"})) as exchange, \
         patch.object(vk, "fetch_profile", AsyncMock(return_value={"sub": "2", "email": None, "name": None})):
        claims = await service._exchange_claims("vk", vk.require_vk_config(), code="c", pending=pending, state="s", extra={"device_id": "d"})
    assert claims["sub"] == "2" and exchange.await_args.kwargs == {"code": "c", "code_verifier": "ver", "device_id": "d", "state": "s"}


@pytest.mark.asyncio
async def test_vk_callback_passes_device_id_and_yandex_does_not(client, oauth_env):
    result = {"outcome": "link_required", "link_token": "tok", "claim": False, "host": "wwc.best", "return_to": "https://wwc.best/me/"}
    with patch.object(service, "vk_callback", AsyncMock(return_value=result)) as vk_callback, \
         patch.object(service, "yandex_callback", AsyncMock(return_value=result)) as yandex_callback:
        vk_response = await client.get(
            "/api/v1/content-access/login/vk/callback", params={"state": "s", "code": "c", "device_id": "dev-9"}, headers=HOST
        )
        yandex_response = await client.get(
            "/api/v1/content-access/login/yandex/callback", params={"state": "s", "code": "c"}, headers=HOST
        )
    assert vk_response.status_code == 302 and vk_response.headers["location"] == "https://wwc.best/me/#wwc-link=tok"
    assert vk_callback.await_args.kwargs["extra"] == {"device_id": "dev-9"}
    assert yandex_response.status_code == 302 and "extra" not in yandex_callback.await_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["yandex", "vk"])
async def test_callback_errors_go_back_to_the_page_with_the_provider_code(client, oauth_env, provider):
    result = {"outcome": "error", "error": f"{provider}_denied", "host": "wwc.best", "return_to": "https://wwc.best/me/"}
    with patch.object(service, f"{provider}_callback", AsyncMock(return_value=result)):
        response = await client.get(f"/api/v1/content-access/login/{provider}/callback", params={"error": "access_denied", "state": "s"}, headers=HOST)
    assert response.headers["location"] == f"https://wwc.best/me/#wwc-link-error={provider}_denied"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["yandex", "vk"])
async def test_known_identity_signs_in_through_a_one_time_login(client, oauth_env, provider):
    result = {"outcome": "signed_in", "telegram_user_id": 700, "host": "wwc.best", "return_to": "https://wwc.best/me/"}
    with patch.object(service, f"{provider}_callback", AsyncMock(return_value=result)), \
         patch.object(service, "browser_login_url", AsyncMock(return_value="https://wwc.best/me/#wwc-login=cid.nonce")):
        response = await client.get(f"/api/v1/content-access/login/{provider}/callback", params={"state": "s", "code": "c"}, headers=HOST)
    assert response.headers["location"] == "https://wwc.best/me/#wwc-login=cid.nonce"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["yandex", "vk"])
async def test_foreign_state_is_a_plain_400(client, oauth_env, provider):
    with patch.object(service, f"{provider}_callback", AsyncMock(side_effect=rules.error(f"{provider}_state_invalid"))):
        response = await client.get(f"/api/v1/content-access/login/{provider}/callback", params={"state": "bad", "code": "c"}, headers=HOST)
    assert response.status_code == 400 and response.json()["error"] == f"{provider}_state_invalid"


# ---- B: ключ доступа ------------------------------------------------------------------------------------


def _registration_options(user_id: bytes = b"u" * 32):
    return webauthn.generate_registration_options(
        rp_id=RP_ID, rp_name="WWC", user_id=user_id, user_name="Евгения", challenge=b"c" * 32,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED, user_verification=UserVerificationRequirement.PREFERRED
        ),
    )


def test_software_authenticator_registers_and_signs_in_against_the_real_library():
    authenticator = SoftAuthenticator()
    reg_options = json.loads(webauthn.options_to_json(_registration_options()))
    assert reg_options["authenticatorSelection"]["residentKey"] == "required"
    registered = webauthn.verify_registration_response(
        credential=authenticator.create(reg_options), expected_challenge=b"c" * 32, expected_rp_id=RP_ID, expected_origin=[ORIGIN],
    )
    assert registered.credential_id == authenticator.credential_id and registered.sign_count == 0

    stored_key, stored_count = registered.credential_public_key, registered.sign_count
    for expected_count in (1, 2):
        auth_options = json.loads(webauthn.options_to_json(webauthn.generate_authentication_options(rp_id=RP_ID, challenge=b"a" * 32)))
        assert auth_options.get("allowCredentials") in (None, []), "discoverable: the browser picks the key"
        verified = webauthn.verify_authentication_response(
            credential=authenticator.get(auth_options), expected_challenge=b"a" * 32, expected_rp_id=RP_ID,
            expected_origin=[ORIGIN], credential_public_key=stored_key, credential_current_sign_count=stored_count,
        )
        assert verified.new_sign_count == expected_count
        stored_count = verified.new_sign_count


def test_a_regressed_counter_a_foreign_origin_and_a_foreign_key_are_rejected():
    authenticator = SoftAuthenticator()
    registered = webauthn.verify_registration_response(
        credential=authenticator.create(json.loads(webauthn.options_to_json(_registration_options()))),
        expected_challenge=b"c" * 32, expected_rp_id=RP_ID, expected_origin=[ORIGIN],
    )
    options = json.loads(webauthn.options_to_json(webauthn.generate_authentication_options(rp_id=RP_ID, challenge=b"a" * 32)))

    def verify(credential, count=5):
        return webauthn.verify_authentication_response(
            credential=credential, expected_challenge=b"a" * 32, expected_rp_id=RP_ID, expected_origin=[ORIGIN],
            credential_public_key=registered.credential_public_key, credential_current_sign_count=count,
        )

    with pytest.raises(Exception):
        verify(authenticator.get(options, sign_count=5))  # клон ключа: счётчик не вырос
    with pytest.raises(Exception):
        verify(authenticator.get(options, origin="https://evil.example", sign_count=6))
    from cryptography.hazmat.primitives.asymmetric import ec

    with pytest.raises(Exception):
        verify(authenticator.get(options, sign_count=6, private_key=ec.generate_private_key(ec.SECP256R1())))
    assert verify(authenticator.get(options, sign_count=6)).new_sign_count == 6


def test_user_handle_is_stable_opaque_and_per_person():
    handle = passkeys.user_handle("whieda", 700)
    assert handle == passkeys.user_handle("whieda", 700) and len(handle) == 32
    assert handle != passkeys.user_handle("whieda", 701) and handle != passkeys.user_handle("other", 700)
    assert b"700" not in handle


def test_passkey_label_is_the_device_system_only():
    assert passkeys.passkey_label(IPHONE) == "iPhone" and passkeys.passkey_label(WINDOWS) == "Windows"
    assert passkeys.passkey_label(None) == "Устройство"


def test_the_challenge_is_read_from_client_data_and_garbage_is_passkey_failed():
    authenticator = SoftAuthenticator()
    options = json.loads(webauthn.options_to_json(_registration_options()))
    credential = authenticator.create(options)
    assert passkeys._challenge_of(credential) == base64url_to_bytes(options["challenge"])
    for broken in ({}, {"response": {}}, {"response": {"clientDataJSON": "!!"}}):
        with pytest.raises(LoginIdentityError) as info:
            passkeys._challenge_of(broken)
        assert info.value.code == "passkey_failed"
    assert passkeys._credential_dict({"credential": credential, "label": "x"}) == credential
    assert passkeys._credential_dict({**credential, "label": "x"}) == credential
    with pytest.raises(LoginIdentityError):
        passkeys._credential_dict({"id": "a"})


def test_webauthn_origins_are_an_exact_list(monkeypatch):
    monkeypatch.delenv("PLATFORM_WEBAUTHN_ORIGINS", raising=False)
    get_settings.cache_clear()
    assert get_settings().parsed_webauthn_origins() == ("https://wwc.best", "https://www.wwc.best")
    assert get_settings().platform_webauthn_rp_id == "wwc.best"
    monkeypatch.setenv("PLATFORM_WEBAUTHN_ORIGINS", "https://wwc.best/, https://dev.wwc.best")
    get_settings.cache_clear()
    assert get_settings().parsed_webauthn_origins() == ("https://wwc.best", "https://dev.wwc.best")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_passkey_routes_that_need_a_session_say_401_without_one(client):
    for method, path in (
        ("post", "/api/v1/content-access/login/passkey/register/options"),
        ("post", "/api/v1/content-access/login/passkey/register/verify"),
        ("get", "/api/v1/content-access/login/passkeys"),
        ("delete", "/api/v1/content-access/login/passkeys/11111111-1111-1111-1111-111111111111"),
    ):
        kwargs = {"json": {}} if method == "post" else {}
        response = await getattr(client, method)(path, headers=HOST, **kwargs)
        assert response.status_code == 401, path


@pytest.mark.asyncio
async def test_passkey_register_flow_passes_the_person_and_the_browser_answer(client):
    options = {"challenge": "abc", "rp": {"id": "wwc.best"}}
    with patch("app.content_access.routes.read_session_cookie", return_value="raw"), \
         patch("app.content_access.routes.validate_content_session", AsyncMock(return_value=SESSION)), \
         patch.object(passkeys, "registration_options", AsyncMock(return_value=options)) as reg_options, \
         patch.object(passkeys, "register", AsyncMock(return_value={"id": "p1", "label": "iPhone"})) as register:
        first = await client.post("/api/v1/content-access/login/passkey/register/options", headers=HOST)
        second = await client.post(
            "/v1/content-access/login/passkey/register/verify", headers={**HOST, "user-agent": IPHONE},
            json={"credential": {"id": "x", "response": {}}, "label": "Мой iPhone"},
        )
        third = await client.post(
            "/api/v1/content-access/login/passkey/register/verify", headers=HOST, json={"id": "y", "response": {}, "type": "public-key"},
        )
    assert first.status_code == 200 and first.json() == options and first.headers["cache-control"] == "private, no-store"
    assert reg_options.await_args.args == ("whieda",) and reg_options.await_args.kwargs["telegram_user_id"] == 700
    assert second.json() == {"ok": True, "id": "p1", "label": "iPhone"}
    assert register.await_args_list[0].kwargs["body"] == {"credential": {"id": "x", "response": {}}, "label": "Мой iPhone"}
    assert register.await_args_list[0].kwargs["user_agent"] == IPHONE
    assert register.await_args_list[1].kwargs["body"] == {"id": "y", "response": {}, "type": "public-key"}
    assert third.status_code == 200


@pytest.mark.asyncio
async def test_passkey_register_failure_is_a_401_with_one_code(client):
    with patch("app.content_access.routes.read_session_cookie", return_value="raw"), \
         patch("app.content_access.routes.validate_content_session", AsyncMock(return_value=SESSION)), \
         patch.object(passkeys, "register", AsyncMock(side_effect=rules.error("passkey_failed", reason="verify"))):
        response = await client.post("/api/v1/content-access/login/passkey/register/verify", headers=HOST, json={"id": "y", "response": {}})
    assert response.status_code == 401 and response.json()["error"] == "passkey_failed"


@pytest.mark.asyncio
async def test_passkey_auth_options_need_no_session_and_verify_sets_the_session_cookie(client):
    with patch.object(passkeys, "authentication_options", AsyncMock(return_value={"challenge": "zzz", "rpId": "wwc.best"})), \
         patch.object(passkeys, "authenticate", AsyncMock(return_value={"telegram_user_id": 700, "passkey_id": "p1"})) as authenticate, \
         patch.object(service, "session_for", AsyncMock(return_value=({"ok": True}, "raw-session"))) as session_for:
        options = await client.post("/api/v1/content-access/login/passkey/auth/options", headers=HOST)
        verified = await client.post(
            "/api/v1/content-access/login/passkey/auth/verify",
            headers={**HOST, "user-agent": WINDOWS, "x-forwarded-for": "95.84.12.7"},
            json={"credential": {"id": "x", "response": {}}, "return_to": "/me/"},
        )
    assert options.status_code == 200 and options.json()["challenge"] == "zzz"
    assert verified.status_code == 200 and verified.json() == {"ok": True, "return_to": "/me/"}
    assert "wwc_content_session=raw-session" in verified.headers["set-cookie"] and "HttpOnly" in verified.headers["set-cookie"]
    assert authenticate.await_args.kwargs["body"] == {"credential": {"id": "x", "response": {}}}, "return_to is not part of the browser answer"
    kwargs = session_for.await_args.kwargs
    assert kwargs["telegram_user_id"] == 700 and kwargs["source"] == "passkey_login"
    assert kwargs["user_agent"] == WINDOWS and kwargs["client_ip_value"] == "95.84.12.7"


@pytest.mark.asyncio
async def test_passkey_auth_verify_refuses_with_401_and_sets_no_cookie_and_cleans_a_bad_return_to(client):
    with patch.object(passkeys, "authenticate", AsyncMock(side_effect=rules.error("passkey_failed", reason="unknown_credential"))):
        refused = await client.post("/api/v1/content-access/login/passkey/auth/verify", headers=HOST, json={"id": "x", "response": {}})
    assert refused.status_code == 401 and refused.json()["error"] == "passkey_failed"
    assert "set-cookie" not in refused.headers
    with patch.object(passkeys, "authenticate", AsyncMock(return_value={"telegram_user_id": 700, "passkey_id": "p1"})), \
         patch.object(service, "session_for", AsyncMock(return_value=({"ok": True}, "raw"))):
        evil = await client.post(
            "/api/v1/content-access/login/passkey/auth/verify", headers=HOST,
            json={"id": "x", "response": {}, "return_to": "https://evil.example/steal"},
        )
    assert evil.status_code == 200 and evil.json()["return_to"] == "/"


@pytest.mark.asyncio
async def test_passkey_list_and_delete(client):
    listing = [{"id": "p1", "label": "iPhone", "created_at": "2026-11-08T10:00:00+00:00", "last_used_at": None}]
    with patch("app.content_access.routes.read_session_cookie", return_value="raw"), \
         patch("app.content_access.routes.validate_content_session", AsyncMock(return_value=SESSION)), \
         patch.object(passkeys, "list_passkeys", AsyncMock(return_value=listing)), \
         patch.object(passkeys, "delete_passkey", AsyncMock(side_effect=[True, False])) as delete:
        got = await client.get("/api/v1/content-access/login/passkeys", headers=HOST)
        gone = await client.delete("/api/v1/content-access/login/passkeys/22222222-2222-2222-2222-222222222222", headers=HOST)
        foreign = await client.delete("/v1/content-access/login/passkeys/33333333-3333-3333-3333-333333333333", headers=HOST)
    assert got.json() == {"ok": True, "passkeys": listing}
    assert gone.status_code == 200 and gone.json() == {"ok": True}
    assert foreign.status_code == 404 and foreign.json()["error"] == "passkey_not_found"
    assert delete.await_args_list[0].kwargs == {"telegram_user_id": 700, "passkey_id": "22222222-2222-2222-2222-222222222222"}


@pytest.mark.asyncio
async def test_start_passes_the_logged_in_person_so_the_link_needs_no_code(client, oauth_env):
    with patch("app.login_identities.routes.read_session_cookie", return_value="raw"), \
         patch("app.login_identities.routes.validate_content_session", AsyncMock(return_value=SESSION)), \
         patch.object(service, "yandex_start", AsyncMock(return_value=("https://oauth.yandex.ru/authorize?x=1", "key"))) as start:
        response = await client.get("/api/v1/content-access/login/yandex/start", params={"return_to": "/me/"}, headers=HOST)
    assert response.status_code == 302 and start.await_args.kwargs["session_user_id"] == 700
    with patch.object(service, "yandex_start", AsyncMock(return_value=("https://oauth.yandex.ru/authorize?x=1", "key"))) as start:
        await client.get("/api/v1/content-access/login/yandex/start", params={"return_to": "/me/"}, headers=HOST)
    assert start.await_args.kwargs["session_user_id"] is None, "a guest has no session"
    with patch("app.login_identities.routes.read_session_cookie", return_value="raw"), \
         patch("app.login_identities.routes.validate_content_session", AsyncMock(side_effect=RuntimeError("db down"))), \
         patch.object(service, "vk_start", AsyncMock(return_value=("https://id.vk.com/authorize?x=1", None))) as start:
        response = await client.get("/api/v1/content-access/login/vk/start", params={"return_to": "/me/"}, headers=HOST)
    assert response.status_code == 302 and start.await_args.kwargs["session_user_id"] is None, "a broken lookup falls back to the code"


@pytest.mark.asyncio
async def test_start_stores_the_person_only_on_a_family_host(client, oauth_env, start_db):
    with patch("app.login_identities.routes.read_session_cookie", return_value="raw"), \
         patch("app.login_identities.routes.validate_content_session", AsyncMock(return_value=SESSION)):
        await client.get("/api/v1/content-access/login/yandex/start", params={"return_to": "/me/"}, headers={"host": "wwc.best"})
    sql, params = start_db[0]
    assert "telegram_user_id" in sql and params[11] == 700


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["yandex", "vk"])
async def test_a_linked_callback_just_returns_to_the_page_without_a_new_login(client, oauth_env, provider):
    result = {"outcome": "linked", "already_linked": False, "telegram_user_id": 700, "host": "wwc.best", "return_to": "https://wwc.best/me/"}
    with patch.object(service, f"{provider}_callback", AsyncMock(return_value=result)), \
         patch.object(service, "browser_login_url", AsyncMock()) as browser:
        response = await client.get(f"/api/v1/content-access/login/{provider}/callback", params={"state": "s", "code": "c"}, headers=HOST)
    assert response.status_code == 302 and response.headers["location"] == "https://wwc.best/me/"
    browser.assert_not_awaited()


@pytest.mark.asyncio
async def test_passkey_list_and_delete_also_live_under_the_passkey_prefix(client):
    listing = [{"id": "p1", "label": "iPhone", "created_at": "2026-11-08T10:00:00+00:00", "last_used_at": None}]
    with patch("app.content_access.routes.read_session_cookie", return_value="raw"), \
         patch("app.content_access.routes.validate_content_session", AsyncMock(return_value=SESSION)), \
         patch.object(passkeys, "list_passkeys", AsyncMock(return_value=listing)), \
         patch.object(passkeys, "delete_passkey", AsyncMock(return_value=True)):
        got = await client.get("/api/v1/content-access/login/passkey/passkeys", headers=HOST)
        gone = await client.delete("/api/v1/content-access/login/passkey/passkeys/22222222-2222-2222-2222-222222222222", headers=HOST)
    assert got.json() == {"ok": True, "passkeys": listing} and gone.json() == {"ok": True}


def test_challenge_id_is_optional_and_must_be_a_uuid():
    assert passkeys._challenge_id_of({}) is None
    assert passkeys._challenge_id_of({"challenge_id": "11111111-1111-1111-1111-111111111111"}) == "11111111-1111-1111-1111-111111111111"
    with pytest.raises(LoginIdentityError):
        passkeys._challenge_id_of({"challenge_id": "junk"})
    credential = {"id": "a", "response": {}, "challenge_id": "x", "return_to": "/"}
    assert passkeys._credential_dict(credential) == {"id": "a", "response": {}}
