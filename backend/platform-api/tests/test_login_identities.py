"""Вход через Google/Max по коду владельца (V34, 15.10.2026) — правила, ручки, бот; база — подделки.

Настоящий SQL (миграция, RLS, гонки, бэкфилл, чистка) — tests/test_login_identities_postgres.py.
"""

from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from app.login_identities import bot, rules, service
from app.login_identities.rules import OAUTH_COOKIE_NAME, LoginIdentityError
from app.settings import get_settings
from app.telegram.processor import process_core_telegram_update
from app.telegram.update_parser import TelegramMessage

HOST = {"host": "wwc.best"}
OWNER = 2
KEY = "browser-key-0123456789abcdefghij"


def _jwt(claims: dict) -> str:
    def seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'RS256', 'kid': 'x'})}.{seg(claims)}.sig"


def _claims(nonce: str = "n0nce", **extra) -> dict:
    base = {
        "iss": "https://accounts.google.com", "aud": "client-1", "sub": "g-100",
        "exp": int((datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()),
        "email": "Evgenia@Example.com", "email_verified": True, "name": "Евгения Волкова", "nonce": nonce,
    }
    return {**base, **extra}


@pytest.fixture
def google_env(monkeypatch):
    monkeypatch.setenv("PLATFORM_GOOGLE_OAUTH_CLIENT_ID", "client-1")
    monkeypatch.setenv("PLATFORM_GOOGLE_OAUTH_CLIENT_SECRET", "secret-1")
    monkeypatch.setenv("PLATFORM_GOOGLE_OAUTH_REDIRECT", "https://wwc.best/api/v1/content-access/login/google/callback")
    monkeypatch.setenv("PLATFORM_BILLING_OWNER_TELEGRAM_ID", str(OWNER))
    monkeypatch.delenv("PLATFORM_LOGIN_CODE_SECRET", raising=False)
    monkeypatch.delenv("PLATFORM_MEDIA_SIGNING_SECRET", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---- rules ----------------------------------------------------------------------------------------


def test_code_is_six_digits_and_tolerates_how_people_type_it():
    code = rules.generate_code()
    assert len(code) == 6 and code.isdigit()
    assert rules.format_code("483921") == "483 921"
    for typed in ("483921", "483 921", "483-921", " 483 921 ", "483.921"):
        assert rules.normalize_code(typed) == "483921", typed
    for bad in ("48392", "4839210", "48392a", "", None):
        assert rules.normalize_code(bad) is None, bad
    assert rules.code_hash("whieda", "483921") != rules.code_hash("other", "483921"), "hash carries the tenant"


def test_code_hash_is_hmac_with_a_server_secret_and_sha256_without(monkeypatch):
    import hashlib
    import hmac

    plain = rules.code_hash("whieda", "483921")
    assert plain == hashlib.sha256(b"whieda:483921").hexdigest()
    keyed = rules.code_hash("whieda", "483921", "pepper")
    assert keyed == hmac.new(b"pepper", b"whieda:483921", hashlib.sha256).hexdigest() and keyed != plain
    monkeypatch.setenv("PLATFORM_LOGIN_CODE_SECRET", "login-secret")
    monkeypatch.setenv("PLATFORM_MEDIA_SIGNING_SECRET", "media-secret")
    get_settings.cache_clear()
    try:
        assert service._code_hash("whieda", "483921") == rules.code_hash("whieda", "483921", "login-secret")
        monkeypatch.delenv("PLATFORM_LOGIN_CODE_SECRET")
        get_settings.cache_clear()
        assert service._code_hash("whieda", "483921") == rules.code_hash("whieda", "483921", "media-secret"), "falls back to the media secret"
    finally:
        get_settings.cache_clear()


def test_browser_key_rules_and_email_masking():
    key = rules.new_browser_key()
    assert rules.normalize_browser_key(key) == key and len(key) >= 32
    assert rules.normalize_browser_key("short") is None and rules.normalize_browser_key("bad key with spaces!!") is None
    assert rules.browser_key_matches(key, rules.sha256_hex(key))
    assert not rules.browser_key_matches(key + "x", rules.sha256_hex(key))
    assert not rules.browser_key_matches(None, rules.sha256_hex(key)) and not rules.browser_key_matches(key, None)
    assert rules.mask_email("evgenia@example.com") == "e***a@example.com"
    assert rules.mask_email("ab@x.ru") == "a***@x.ru" and rules.mask_email("") == "" and rules.mask_email("nope") == ""


@pytest.mark.parametrize(("text", "expected"), [
    ("код входа volkova", ("issue", None, "volkova")),
    ("Код входа Евгения Волкова", ("issue", None, "Евгения Волкова")),
    ("/logincode 688931415", ("issue", None, "688931415")),
    ("способы входа volkova", ("methods", None, "volkova")),
    ("отвязать google volkova", ("unlink", "google", "volkova")),
    ("Отвязать MAX Евгения", ("unlink", "max", "Евгения")),
    ("код входа", None),
    ("отвязать vk volkova", None),
    ("привет, код входа?", None),
])
def test_owner_commands_are_recognised(text, expected):
    assert rules.parse_owner_command(text) == expected


def test_id_token_claims_are_checked_one_by_one():
    nonce_hash = rules.sha256_hex("n0nce")
    out = rules.check_id_token_claims(_claims(), client_id="client-1", nonce_hash=nonce_hash)
    assert out == {"sub": "g-100", "email": "evgenia@example.com", "name": "Евгения Волкова"}
    for broken, reason in [
        (_claims(iss="https://evil.example"), "iss"),
        (_claims(aud="other-client"), "aud"),
        (_claims(exp=int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())), "exp"),
        (_claims(nonce="other"), "nonce"),
        (_claims(sub=""), "sub"),
    ]:
        with pytest.raises(LoginIdentityError) as info:
            rules.check_id_token_claims(broken, client_id="client-1", nonce_hash=nonce_hash)
        assert info.value.code == "google_token_invalid" and info.value.details["reason"] == reason
    with pytest.raises(LoginIdentityError) as info:
        rules.check_id_token_claims(_claims(email_verified=False), client_id="client-1", nonce_hash=nonce_hash)
    assert info.value.code == "email_unverified"
    assert rules.check_id_token_claims(_claims(aud=["client-1", "x"]), client_id="client-1", nonce_hash=nonce_hash)["sub"] == "g-100"


def test_jwt_payload_is_decoded_without_a_signature_check():
    assert rules.decode_jwt_payload(_jwt({"sub": "1"})) == {"sub": "1"}
    for bad in ("", "a.b", "a.!!!.c", f"a.{base64.urlsafe_b64encode(b'[1]').decode()}.c"):
        with pytest.raises(LoginIdentityError):
            rules.decode_jwt_payload(bad)


def test_pkce_pair_is_s256():
    import hashlib

    verifier, challenge = rules.pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected and len(verifier) >= 43


def test_login_methods_start_with_telegram_and_label_by_provider():
    linked = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    methods = rules.login_methods_payload([
        {"provider": "google", "email": "e@x.com", "linked_at": linked},
        {"provider": "max", "display_name": "Евгения", "linked_at": linked},
        {"provider": "max", "display_name": "", "linked_at": None},
    ])
    assert methods[0] == {"provider": "telegram", "label": "Telegram", "linked_at": None}
    assert methods[1] == {"provider": "google", "label": "e@x.com", "linked_at": linked.isoformat()}
    assert methods[2]["label"] == "Евгения" and methods[3]["label"] == "Max"


def test_same_cookie_family(monkeypatch):
    monkeypatch.setenv("PLATFORM_COOKIE_SHARED_DOMAINS", "wwc.best")
    get_settings.cache_clear()
    try:
        assert service.same_cookie_family("wwc.best", "samtsova.wwc.best")
        assert service.same_cookie_family("academy.almira.by", "academy.almira.by")
        assert not service.same_cookie_family("wwc.best", "academy.almira.by")
        assert not service.same_cookie_family("", "wwc.best")
    finally:
        get_settings.cache_clear()


def test_google_config_never_prints_the_secret(google_env):
    from app.login_identities.google import google_config

    config = google_config()
    assert config is not None and "secret-1" not in repr(config) and "secret-1" not in str(config)


# ---- redeem (подделка соединения) ------------------------------------------------------------------


class FakeConn:
    """login_link_codes + account_identities по префиксу запроса; захват кода — update … returning."""

    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.codes = {
            service._code_hash("whieda", "111111"): {"code_id": "c1", "telegram_user_id": 700, "issued_by": OWNER, "expires_at": now + timedelta(hours=1), "used_at": None},
            service._code_hash("whieda", "222222"): {"code_id": "c2", "telegram_user_id": 701, "issued_by": OWNER, "expires_at": now - timedelta(hours=1), "used_at": None},
            service._code_hash("whieda", "333333"): {"code_id": "c3", "telegram_user_id": 702, "issued_by": OWNER, "expires_at": now + timedelta(hours=1), "used_at": now},
        }
        self.identities: list[dict] = []
        self.executed: list[tuple[str, tuple]] = []
        self.always_clash = False

    def cursor(self):
        conn = self

        class Cursor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def execute(self, query, params=None):
                conn.executed.append((" ".join(query.split()), params))

        return Cursor()

    async def fetch_one(self, conn, query, params=()):  # noqa: ARG002
        q = " ".join(query.split())
        if q.startswith("update login_link_codes set used_at = now(), used_provider"):
            row = self.codes.get(params[2])
            if not row or row["used_at"] or row["expires_at"] <= datetime.now(timezone.utc):
                return None
            row["used_at"] = datetime.now(timezone.utc)
            row["used_provider"] = params[0]
            return {"code_id": row["code_id"], "telegram_user_id": row["telegram_user_id"], "issued_by": row["issued_by"]}
        if q.startswith("select used_at, expires_at from login_link_codes"):
            return self.codes.get(params[1])
        if q.startswith("select code_id from login_link_codes"):
            return {"code_id": "clash"} if self.always_clash else None
        if q.startswith("select identity_id, telegram_user_id, email, display_name, linked_at"):
            return next((i for i in self.identities if (i["provider"], i["external_id"]) == (params[1], params[2]) and not i.get("revoked_at")), None)
        if q.startswith("select 1 as present"):
            return next(({"present": 1} for i in self.identities if (i["provider"], i["external_id"]) == (params[1], params[2])), None)
        if q.startswith("insert into account_identities"):
            row = {"identity_id": f"id-{len(self.identities) + 1}", "telegram_user_id": params[1], "provider": params[2], "external_id": params[3], "email": params[4], "display_name": params[5], "linked_by": params[6], "linked_at": datetime.now(timezone.utc)}
            self.identities.append(row)
            return row
        raise AssertionError(f"unexpected query: {q[:90]}")


@pytest.fixture
def conn(google_env):
    fake = FakeConn()
    with patch.object(service, "fetch_one", fake.fetch_one):
        yield fake


@pytest.mark.asyncio
async def test_redeem_claims_the_code_atomically_and_logs_without_secrets(conn, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="whieda.login_identities"):
        out = await service.redeem_code_on(conn, "whieda", code="111 111", provider="google", external_id="g-1", email="e@x.com", display_name="Е")
    assert out["telegram_user_id"] == 700 and not out["already_linked"]
    assert conn.identities[0]["linked_by"] == f"code:{OWNER}"
    claimed = conn.codes[service._code_hash("whieda", "111111")]
    assert claimed["used_at"] and claimed["used_provider"] == "google", "claimed by the update itself, not a later set"
    record = next(r for r in caplog.records if r.getMessage() == "login_identity_linked")
    assert record.provider == "google" and record.linked_by == "code"
    assert "e@x.com" not in caplog.text and "111111" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "expected"), [
    ("999999", "code_invalid"), ("12", "code_invalid"), ("222222", "code_expired"), ("333333", "code_used"),
])
async def test_bad_codes_are_refused_with_a_reason(conn, code, expected):
    with pytest.raises(LoginIdentityError) as info:
        await service.redeem_code_on(conn, "whieda", code=code, provider="max", external_id="5001", email=None, display_name=None)
    assert info.value.code == expected and not conn.identities


@pytest.mark.asyncio
async def test_second_redeem_of_the_same_code_sees_it_used(conn):
    await service.redeem_code_on(conn, "whieda", code="111111", provider="google", external_id="g-1", email=None, display_name=None)
    with pytest.raises(LoginIdentityError) as info:
        await service.redeem_code_on(conn, "whieda", code="111111", provider="max", external_id="5001", email=None, display_name=None)
    assert info.value.code == "code_used"


@pytest.mark.asyncio
async def test_external_id_already_bound_to_another_person_is_refused(conn):
    conn.identities.append({"identity_id": "old", "provider": "google", "external_id": "g-1", "telegram_user_id": 999})
    with pytest.raises(LoginIdentityError) as info:
        await service.redeem_code_on(conn, "whieda", code="111111", provider="google", external_id="g-1", email=None, display_name=None)
    assert info.value.code == "identity_taken" and len(conn.identities) == 1
    # Захват кода уже в транзакции — её откат (при исключении) возвращает код владельцу; проверено на PostgreSQL.


@pytest.mark.asyncio
async def test_same_person_linking_twice_is_idempotent(conn):
    conn.identities.append({"identity_id": "old", "provider": "google", "external_id": "g-1", "telegram_user_id": 700})
    out = await service.redeem_code_on(conn, "whieda", code="111111", provider="google", external_id="g-1", email=None, display_name=None)
    assert out == {"telegram_user_id": 700, "identity_id": "old", "already_linked": True}


@pytest.mark.asyncio
async def test_issue_code_gives_up_after_five_clashes(conn):
    conn.always_clash = True

    @asynccontextmanager
    async def connection(_tenant):
        yield conn

    with patch.object(service, "tenant_connection", connection), \
         patch.object(service, "resolve_person", AsyncMock(return_value={"ref_code": "volkova", "telegram_user_id": 700, "display_name": "Е"})):
        with pytest.raises(LoginIdentityError) as info:
            await service.issue_code("whieda", issued_by=OWNER, target="volkova")
    assert info.value.code == "code_unavailable"
    assert not any("insert into login_link_codes" in q for q, _ in conn.executed)


def test_telegram_cannot_be_revoked_and_unknown_providers_are_rejected():
    for provider, code in (("telegram", "invalid_provider"), ("vk", "invalid_provider")):
        with pytest.raises(LoginIdentityError) as info:
            rules.normalize_provider(provider)
        assert info.value.code == code


# ---- Google: start / callback / link (ручки) --------------------------------------------------------


@asynccontextmanager
async def _recording_connection(inserted: list, _tenant=None):
    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def execute(self, query, params=None):
            inserted.append((" ".join(query.split()), params))

    yield SimpleNamespace(cursor=lambda: Cursor())


@pytest.fixture
def start_db():
    inserted: list[tuple] = []

    @asynccontextmanager
    async def connection(_tenant):
        async with _recording_connection(inserted) as conn:
            yield conn

    with patch.object(service, "tenant_connection", connection), patch.object(service, "warm_hosts", AsyncMock()):
        yield inserted


@pytest.mark.asyncio
async def test_google_start_sends_the_browser_back_with_an_error_when_google_is_off(client, monkeypatch):
    for name in ("PLATFORM_GOOGLE_OAUTH_CLIENT_ID", "PLATFORM_GOOGLE_OAUTH_CLIENT_SECRET", "PLATFORM_GOOGLE_OAUTH_REDIRECT"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    try:
        with patch.object(service, "warm_hosts", AsyncMock()):
            response = await client.get("/api/v1/content-access/login/google/start", params={"return_to": "/me/"}, headers={"host": "samtsova.wwc.best"})
            assert response.status_code == 302
            assert response.headers["location"] == "https://samtsova.wwc.best/me/#wwc-link-error=google_disabled"
            # Негодный путь или чужой хост в return_to — корень хоста запроса.
            response = await client.get("/api/v1/content-access/login/google/start", params={"return_to": "/admin/x"}, headers=HOST)
            assert response.headers["location"] == "https://wwc.best/#wwc-link-error=google_disabled"
            response = await client.get("/api/v1/content-access/login/google/start", params={"return_to": "https://evil.example/"}, headers=HOST)
            assert response.status_code == 302 and response.headers["location"] == "https://wwc.best/#wwc-link-error=google_disabled"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_google_start_redirects_to_google_with_pkce_state_and_a_browser_cookie(client, google_env, start_db):
    response = await client.get(
        "/api/v1/content-access/login/google/start",
        params={"return_to": "/price/", "visitor_session_id": "11111111-1111-1111-1111-111111111111"},
        headers={"host": "samtsova.wwc.best"},
    )
    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    assert location.netloc == "accounts.google.com"
    query = parse_qs(location.query)
    assert query["client_id"] == ["client-1"] and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://wwc.best/api/v1/content-access/login/google/callback"]
    assert query["scope"] == ["openid email profile"] and query["prompt"] == ["select_account"]
    assert response.headers["cache-control"] == "private, no-store"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{OAUTH_COOKIE_NAME}=") and "Domain=.wwc.best" in cookie and "HttpOnly" in cookie
    assert "SameSite=lax" in cookie and "Max-Age=900" in cookie and "Secure" in cookie
    key = cookie.split(";", 1)[0].split("=", 1)[1]
    (sql, params) = start_db[0]
    assert sql.startswith("insert into login_pending")
    assert params[1] == rules.sha256_hex(query["state"][0]) and params[2] == rules.sha256_hex(query["nonce"][0])
    assert params[4] == "samtsova.wwc.best" and params[5] == "https://samtsova.wwc.best/price/"
    assert params[6] == "11111111-1111-1111-1111-111111111111"
    assert params[7] == rules.sha256_hex(key), "the pending row remembers the browser that started"
    assert params[8] is None, "no site key on a family host"
    assert "secret-1" not in response.headers["location"]


@pytest.mark.asyncio
async def test_query_key_never_becomes_the_cookie_on_a_family_host(client, google_env, start_db):
    """Атака 15.10: чужая страница открывает жертве start?browser_key=K — cookie жертвы не должна стать K."""
    response = await client.get(
        "/api/v1/content-access/login/google/start", params={"return_to": "/me/", "browser_key": KEY}, headers=HOST
    )
    assert response.status_code == 302
    cookie_value = response.headers["set-cookie"].split(";", 1)[0].split("=", 1)[1]
    assert cookie_value != KEY and rules.normalize_browser_key(cookie_value) == cookie_value
    params = start_db[0][1]
    assert params[7] == rules.sha256_hex(cookie_value) and params[7] != rules.sha256_hex(KEY)
    assert params[8] is None, "a client key is not stored at all on a family host"


@pytest.fixture
def showcase_env(google_env, monkeypatch):
    # Вторая «семья» — хост вне семьи callback-хоста wwc.best (как витрина на своём домене).
    monkeypatch.setenv("PLATFORM_COOKIE_SHARED_DOMAINS", "wwc.best,other.test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_showcase_start_keeps_the_site_key_apart_and_sets_no_cookie(client, showcase_env, start_db):
    response = await client.get(
        "/api/v1/content-access/login/google/start",
        params={"return_to": "https://site.other.test/me/", "browser_key": KEY}, headers=HOST,
    )
    assert response.status_code == 302 and urlparse(response.headers["location"]).netloc == "accounts.google.com"
    assert not any(c.startswith(f"{OAUTH_COOKIE_NAME}=") for c in response.headers.get_list("set-cookie"))
    params = start_db[0][1]
    assert params[4] == "site.other.test" and params[7] is None and params[8] == rules.sha256_hex(KEY)


@pytest.mark.asyncio
async def test_showcase_start_without_a_site_key_goes_back_with_an_error(client, showcase_env, start_db):
    response = await client.get(
        "/api/v1/content-access/login/google/start", params={"return_to": "https://site.other.test/me/"}, headers=HOST
    )
    assert response.status_code == 302
    assert response.headers["location"] == "https://site.other.test/me/#wwc-link-error=browser_key_required"
    assert not start_db and "set-cookie" not in response.headers


def test_presented_key_follows_the_row_mode_without_fallback():
    family = {"browser_key_hash": rules.sha256_hex(KEY), "site_key_hash": None}
    showcase = {"browser_key_hash": None, "site_key_hash": rules.sha256_hex(KEY)}
    other = "other-key-0123456789abcdefghijkl"
    # Хост семьи: считается только cookie; body-ключ без cookie — отказ.
    assert rules.browser_key_matches(*service.presented_key(family, cookie_key=KEY, body_key=None))
    assert not rules.browser_key_matches(*service.presented_key(family, cookie_key=None, body_key=KEY))
    assert not rules.browser_key_matches(*service.presented_key(family, cookie_key=other, body_key=KEY))
    # Витрина: считается только body; cookie (даже «правильная») не помогает.
    assert rules.browser_key_matches(*service.presented_key(showcase, cookie_key=None, body_key=KEY))
    assert not rules.browser_key_matches(*service.presented_key(showcase, cookie_key=KEY, body_key=None))
    assert not rules.browser_key_matches(*service.presented_key(showcase, cookie_key=KEY, body_key=other))
    # Строка без режима — ничего не подходит.
    assert not rules.browser_key_matches(*service.presented_key({}, cookie_key=KEY, body_key=KEY))


@pytest.mark.asyncio
async def test_google_start_with_a_bad_path_goes_back_to_the_host_root(client, google_env):
    with patch.object(service, "warm_hosts", AsyncMock()):
        response = await client.get("/api/v1/content-access/login/google/start", params={"return_to": "/api/zzz"}, headers=HOST)
        assert response.status_code == 302 and response.headers["location"] == "https://wwc.best/#wwc-link-error=invalid_return_to"
        response = await client.get("/api/v1/content-access/login/google/start", params={"return_to": "https://evil.example/"}, headers=HOST)
    # Чужой хост в return_to — тоже на корень хоста запроса (он проверен семьёй), без JSON в браузере.
    assert response.status_code == 302 and response.headers["location"] == "https://wwc.best/#wwc-link-error=invalid_return_to"
    assert "set-cookie" not in response.headers, "no oauth cookie without a Google redirect"


SIGNED_IN = {"outcome": "signed_in", "telegram_user_id": 700, "host": "samtsova.wwc.best", "return_to": "https://samtsova.wwc.best/price/", "visitor_session_id": None}


@pytest.mark.asyncio
async def test_callback_passes_the_cookie_and_returns_only_a_one_time_login(client, google_env):
    with patch.object(service, "google_callback", AsyncMock(return_value=SIGNED_IN)) as callback, \
         patch.object(service, "browser_login_url", AsyncMock(return_value="https://samtsova.wwc.best/price/#wwc-login=cid.nonce")) as browser, \
         patch.object(service, "session_for", AsyncMock(return_value=({"ok": True}, "raw-session"))) as session_for:
        response = await client.get(
            "/api/v1/content-access/login/google/callback", params={"state": "s", "code": "c"},
            headers=HOST, cookies={OAUTH_COOKIE_NAME: KEY},
        )
    assert response.status_code == 302 and response.headers["location"] == "https://samtsova.wwc.best/price/#wwc-login=cid.nonce"
    assert callback.await_args.kwargs == {"state": "s", "code": "c", "oauth_error": None, "cookie_key": KEY}
    browser.assert_awaited_once_with("whieda", telegram_user_id=700, return_to="https://samtsova.wwc.best/price/")
    cookies = response.headers.get_list("set-cookie")
    assert not any("wwc_content_session=" in c for c in cookies), "one session per login: the site redeems #wwc-login"
    session_for.assert_not_awaited()
    assert any(c.startswith(f"{OAUTH_COOKIE_NAME}=") and ("Max-Age=0" in c or "expires=" in c.lower()) for c in cookies), "oauth cookie cleared"


@pytest.mark.asyncio
async def test_callback_for_a_showcase_host_hands_over_only_the_one_time_login(client, google_env):
    result = {**SIGNED_IN, "host": "academy.almira.by", "return_to": "https://academy.almira.by/academy/"}
    with patch.object(service, "google_callback", AsyncMock(return_value=result)), \
         patch.object(service, "browser_login_url", AsyncMock(return_value="https://academy.almira.by/academy/#wwc-login=cid.nonce")), \
         patch.object(service, "session_for", AsyncMock()) as session_for:
        response = await client.get("/api/v1/content-access/login/google/callback", params={"state": "s", "code": "c"}, headers=HOST)
    assert response.status_code == 302 and response.headers["location"].endswith("#wwc-login=cid.nonce")
    assert not any("wwc_content_session=" in c for c in response.headers.get_list("set-cookie"))
    session_for.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_without_identity_sends_the_site_to_the_code_screen(client, google_env):
    result = {"outcome": "link_required", "link_token": "tok-1", "claim": False, "host": "wwc.best", "return_to": "https://wwc.best/me/"}
    with patch.object(service, "google_callback", AsyncMock(return_value=result)):
        response = await client.get("/api/v1/content-access/login/google/callback", params={"state": "s", "code": "c"}, headers=HOST)
    assert response.status_code == 302 and response.headers["location"] == "https://wwc.best/me/#wwc-link=tok-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["google_denied", "google_token_invalid", "email_unverified", "code_locked"])
async def test_callback_errors_go_back_to_the_page_with_a_code(client, google_env, error):
    result = {"outcome": "error", "error": error, "host": "wwc.best", "return_to": "https://wwc.best/me/"}
    with patch.object(service, "google_callback", AsyncMock(return_value=result)):
        response = await client.get("/api/v1/content-access/login/google/callback", params={"state": "s", "error": "access_denied"}, headers=HOST)
    assert response.status_code == 302 and response.headers["location"] == f"https://wwc.best/me/#wwc-link-error={error}"


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["google_state_invalid", "oauth_browser_mismatch"])
async def test_unknown_state_or_foreign_browser_is_a_plain_400(client, google_env, code):
    with patch.object(service, "google_callback", AsyncMock(side_effect=rules.error(code))):
        response = await client.get("/api/v1/content-access/login/google/callback", params={"state": "zzz", "code": "c"}, headers=HOST)
    assert response.status_code == 400 and response.json()["error"] == code
    assert not any("wwc_content_session=" in c for c in response.headers.get_list("set-cookie"))


@pytest.mark.asyncio
async def test_pending_screen_shows_a_masked_email(client, google_env):
    info = {"provider": "google", "email_masked": "e***a@example.com", "display_name": "Евгения", "status": "awaiting_code"}
    with patch.object(service, "pending_info", AsyncMock(return_value=info)) as pending:
        response = await client.get("/api/v1/content-access/login/pending/tok-1", headers=HOST)
    assert response.status_code == 200 and response.json() == {"ok": True, **info}
    assert response.headers["cache-control"] == "private, no-store"
    pending.assert_awaited_once_with("whieda", link_token="tok-1")
    with patch.object(service, "pending_info", AsyncMock(side_effect=rules.error("pending_invalid"))):
        response = await client.get("/api/v1/content-access/login/pending/zzz", headers=HOST)
    assert response.status_code == 404 and response.json()["error"] == "pending_invalid"


@pytest.mark.asyncio
async def test_link_with_the_owner_code_signs_in_and_passes_both_keys_apart(client, google_env):
    linked = {"telegram_user_id": 700, "identity_id": "id-1", "already_linked": False, "visitor_session_id": None}
    with patch.object(service, "link_pending", AsyncMock(return_value=linked)) as link_pending, \
         patch.object(service, "session_for", AsyncMock(return_value=({"ok": True, "status": "authenticated", "expires_at": "2026-11-14T00:00:00+00:00"}, "raw-session"))):
        response = await client.post(
            "/api/v1/content-access/login/link", json={"pending_id": "tok-1", "code": "483 921", "browser_key": KEY},
            headers=HOST, cookies={OAUTH_COOKIE_NAME: "cookie-key-0123456789abcdefghij"},
        )
    assert response.status_code == 200 and response.json()["status"] == "authenticated"
    cookies = response.headers.get_list("set-cookie")
    assert any("wwc_content_session=raw-session" in c for c in cookies)
    assert any(c.startswith(f"{OAUTH_COOKIE_NAME}=") and ("Max-Age=0" in c or "expires=" in c.lower()) for c in cookies)
    assert response.headers["cache-control"] == "private, no-store"
    link_pending.assert_awaited_once_with(
        "whieda", link_token="tok-1", code="483 921", cookie_key="cookie-key-0123456789abcdefghij", body_key=KEY
    )


@pytest.mark.asyncio
async def test_link_without_body_key_passes_none_and_code_is_optional_for_a_claim(client, google_env):
    linked = {"telegram_user_id": 700, "identity_id": None, "already_linked": True, "visitor_session_id": None}
    with patch.object(service, "link_pending", AsyncMock(return_value=linked)) as link_pending, \
         patch.object(service, "session_for", AsyncMock(return_value=({"ok": True, "status": "authenticated"}, "raw"))):
        response = await client.post(
            "/api/v1/content-access/login/link", json={"pending_id": "tok-1"}, headers=HOST, cookies={OAUTH_COOKIE_NAME: KEY},
        )
    assert response.status_code == 200
    link_pending.assert_awaited_once_with("whieda", link_token="tok-1", code=None, cookie_key=KEY, body_key=None)


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "status"), [
    ("code_invalid", 400), ("code_expired", 410), ("code_used", 409), ("code_locked", 429),
    ("identity_taken", 409), ("pending_invalid", 404), ("pending_expired", 410),
    ("oauth_browser_mismatch", 400), ("code_required", 400),
])
async def test_link_errors_carry_their_codes(client, google_env, code, status):
    with patch.object(service, "link_pending", AsyncMock(side_effect=rules.error(code))):
        response = await client.post("/api/v1/content-access/login/link", json={"pending_id": "tok-1", "code": "000000"}, headers=HOST)
    assert response.status_code == status and response.json()["error"] == code
    assert "set-cookie" not in response.headers


# ---- кабинет: способы входа и отвязка ----------------------------------------------------------------

SESSION = {"session_id": "s1", "tenant_id": "whieda", "scope": "telegram_verified", "expires_at": datetime.now(timezone.utc) + timedelta(days=1), "telegram_user_id": 700}


@pytest.mark.asyncio
async def test_me_lists_login_methods(client):
    methods = [{"provider": "telegram", "label": "Telegram", "linked_at": None}, {"provider": "google", "label": "e@x.com", "linked_at": "2026-10-15T10:00:00+00:00"}]
    with patch("app.content_access.routes.read_session_cookie", return_value="raw"), \
         patch("app.content_access.routes.validate_content_session", AsyncMock(return_value=SESSION)), \
         patch("app.content_access.routes.resolve_partner_subscription_by_telegram_user_id", AsyncMock(return_value=None)), \
         patch("app.content_access.routes.load_site_account", AsyncMock(return_value=None)), \
         patch("app.content_access.routes.list_login_methods", AsyncMock(return_value=methods)) as listing:
        response = await client.get("/api/v1/content-access/me", headers=HOST)
    assert response.status_code == 200 and response.json()["login_methods"] == methods
    listing.assert_awaited_once_with("whieda", 700)


@pytest.mark.asyncio
async def test_me_survives_a_broken_methods_lookup_with_telegram_only(client):
    with patch("app.content_access.routes.read_session_cookie", return_value="raw"), \
         patch("app.content_access.routes.validate_content_session", AsyncMock(return_value=SESSION)), \
         patch("app.content_access.routes.resolve_partner_subscription_by_telegram_user_id", AsyncMock(return_value=None)), \
         patch("app.content_access.routes.load_site_account", AsyncMock(return_value=None)), \
         patch("app.content_access.routes.list_login_methods", AsyncMock(side_effect=RuntimeError("no pool"))):
        response = await client.get("/api/v1/content-access/me", headers=HOST)
    assert response.status_code == 200
    assert response.json()["login_methods"] == [{"provider": "telegram", "label": "Telegram", "linked_at": None}]


@pytest.mark.asyncio
async def test_revoke_needs_a_session_and_revokes_only_google_or_max(client):
    response = await client.delete("/api/v1/content-access/login/identities/google", headers=HOST)
    assert response.status_code == 401
    with patch("app.content_access.routes.read_session_cookie", return_value="raw"), \
         patch("app.content_access.routes.validate_content_session", AsyncMock(return_value=SESSION)):
        for provider, error in (("telegram", "invalid_provider"), ("vk", "invalid_provider")):
            response = await client.delete(f"/api/v1/content-access/login/identities/{provider}", headers=HOST)
            assert response.status_code == 400 and response.json()["error"] == error, provider
        with patch.object(service, "revoke_identity", AsyncMock(return_value=1)) as revoke:
            response = await client.delete("/api/v1/content-access/login/identities/Google", headers=HOST)
    assert response.status_code == 200 and response.json() == {"ok": True, "provider": "google", "revoked": 1}
    revoke.assert_awaited_once_with("whieda", telegram_user_id=700, provider="Google", revoked_by="self:700")


# ---- бот: команды владельца --------------------------------------------------------------------------


def _msg(text: str, user_id: int = OWNER, chat_type: str = "private") -> TelegramMessage:
    return TelegramMessage(chat_id=user_id, user_id=user_id, message_id=1, text=text, chat_type=chat_type, file_id=None, raw={})


TENANT = SimpleNamespace(tenant_id="whieda")
PERSON = {"ref_code": "volkova", "telegram_user_id": 700, "display_name": "Евгения Волкова"}


@pytest.fixture
def sent(google_env):
    out: list[str] = []
    with patch.object(bot, "_deliver", AsyncMock(side_effect=lambda chat, text: out.append(text))):
        yield out


@pytest.mark.asyncio
async def test_non_owner_words_are_not_a_command_and_get_no_reply(sent):
    with patch.object(service, "issue_code", AsyncMock()) as issue:
        assert await bot.try_handle_login_identity_message(TENANT, _msg("код входа не приходит", user_id=700), trace_id="t") is None
        assert await bot.try_handle_login_identity_message(TENANT, _msg("код входа volkova", user_id=700), trace_id="t") is None
    assert sent == [] and issue.assert_not_awaited() is None
    assert await bot.try_handle_login_identity_message(TENANT, _msg("привет"), trace_id="t") is None
    assert (await bot.try_handle_login_identity_message(TENANT, _msg("код входа x", chat_type="supergroup"), trace_id="t"))["status"] == "private_chat_required"


@pytest.mark.asyncio
async def test_partner_text_about_login_codes_reaches_the_next_handler_through_the_processor(
    whieda_tenant, whieda_bot_binding, google_env, sent
):
    update = {"message": {"message_id": 5, "text": "код входа не приходит", "chat": {"id": 700, "type": "private"}, "from": {"id": 700}}}
    with patch("app.telegram.processor._link_partner_chat", AsyncMock()), \
         patch("app.telegram.processor.try_handle_wish_message", AsyncMock(return_value=None)), \
         patch("app.telegram.processor.try_handle_profile_reject_reason", AsyncMock(return_value=None)), \
         patch("app.telegram.processor.try_handle_shop_message", AsyncMock(return_value=None)), \
         patch("app.telegram.processor.try_handle_support_message", AsyncMock(return_value=None)), \
         patch("app.telegram.processor.try_handle_renewal_message", AsyncMock(return_value=None)), \
         patch("app.telegram.processor.try_handle_site_request_message", AsyncMock(return_value=None)), \
         patch("app.telegram.processor.try_start_renewal_by_text", AsyncMock(return_value=None)), \
         patch("app.telegram.processor.try_handle_billing_message", AsyncMock(return_value=None)), \
         patch("app.telegram.processor.try_handle_referral_admin_message", AsyncMock(return_value=None)), \
         patch("app.telegram.processor.try_handle_profile_requests_command", AsyncMock(return_value={"ok": True, "route": "next_handler"})) as next_handler, \
         patch.object(service, "issue_code", AsyncMock()) as issue:
        result = await process_core_telegram_update(whieda_tenant, update, "t1", binding=whieda_bot_binding)
    assert result["route"] == "next_handler", "a partner's words fall through to the handlers after ours"
    next_handler.assert_awaited_once()
    issue.assert_not_awaited()
    assert sent == [], "and the partner gets no «Команда недоступна.»"


@pytest.mark.asyncio
async def test_owner_gets_the_code_once_and_nothing_goes_to_the_partner(sent):
    issued = {"code": "483921", "code_display": "483 921", "person": PERSON, "expires_at": datetime.now(timezone.utc)}
    with patch.object(service, "issue_code", AsyncMock(return_value=issued)) as issue:
        out = await bot.try_handle_login_identity_message(TENANT, _msg("код входа Евгения"), trace_id="t")
    assert out["status"] == "issued" and out["telegram_user_id"] == 700
    issue.assert_awaited_once_with("whieda", issued_by=OWNER, target="Евгения")
    assert len(sent) == 1 and "Евгения Волкова (volkova): 483 921" in sent[0] and "24 часа" in sent[0]


@pytest.mark.asyncio
async def test_ambiguous_and_missing_targets_are_explained(sent):
    candidates = [PERSON, {"ref_code": "evgenia2", "telegram_user_id": 701, "display_name": "Евгения Петрова"}]
    with patch.object(service, "issue_code", AsyncMock(side_effect=rules.error("partner_ambiguous", target="Евгения", candidates=candidates))):
        out = await bot.try_handle_login_identity_message(TENANT, _msg("код входа Евгения"), trace_id="t")
    assert out["status"] == "partner_ambiguous"
    assert "Евгения Волкова (volkova) · id 700" in sent[0] and "Евгения Петрова (evgenia2)" in sent[0]
    with patch.object(service, "issue_code", AsyncMock(side_effect=rules.error("partner_not_found", target="zzz"))):
        out = await bot.try_handle_login_identity_message(TENANT, _msg("код входа zzz"), trace_id="t")
    assert out["status"] == "partner_not_found" and "Не нашёл: zzz" in sent[1]


@pytest.mark.asyncio
async def test_methods_and_unlink_commands(sent):
    linked = datetime(2026, 10, 15, 7, 30, tzinfo=timezone.utc)
    identities = [{"provider": "google", "email": "e@x.com", "linked_at": linked}, {"provider": "max", "display_name": "Евгения", "linked_at": linked}]

    @asynccontextmanager
    async def connection(_tenant):
        yield object()

    with patch.object(bot, "tenant_connection", connection), \
         patch.object(service, "resolve_person", AsyncMock(return_value=PERSON)), \
         patch.object(service, "list_identities", AsyncMock(return_value=identities)), \
         patch.object(service, "active_code_info", AsyncMock(return_value=linked + timedelta(hours=24))), \
         patch.object(service, "revoke_identity", AsyncMock(side_effect=[1, 0])) as revoke:
        out = await bot.try_handle_login_identity_message(TENANT, _msg("способы входа volkova"), trace_id="t")
        assert out["status"] == "methods"
        text = sent[0]
        assert text.startswith("Способы входа — Евгения Волкова (volkova):")
        assert "• Telegram" in text and "• Google: e@x.com (с 15.10.2026 10:30)" in text and "• Max: Евгения" in text
        assert "Активный код: до 16.10.2026 10:30 МСК." in text
        out = await bot.try_handle_login_identity_message(TENANT, _msg("отвязать google volkova"), trace_id="t")
        assert out["status"] == "unlinked" and sent[1].startswith("Google отвязан у Евгения Волкова (volkova).")
        out = await bot.try_handle_login_identity_message(TENANT, _msg("отвязать max volkova"), trace_id="t")
        assert sent[2] == "У Евгения Волкова (volkova) нет привязки Max."
    assert revoke.await_args_list[0].kwargs == {"telegram_user_id": 700, "provider": "google", "revoked_by": f"owner:{OWNER}"}


def test_processor_routes_owner_commands_to_the_login_handler():
    from app.telegram import processor

    assert processor.try_handle_login_identity_message is bot.try_handle_login_identity_message


def test_worker_runs_the_login_cleanup_hourly():
    from app.jobs import worker

    assert worker.login_cleanup_step is service.login_cleanup_step
    assert worker.LOGIN_CLEANUP_INTERVAL_SEC == 3600.0
    source = open(worker.__file__, encoding="utf-8").read()
    assert "await login_cleanup_step()" in source and "LOGIN_CLEANUP_INTERVAL_SEC" in source.split("async def worker_loop", 1)[1]


# ---- Max: код в мини-приложении (ручка) -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_webapp_route_passes_the_code_through(client):
    with patch("app.content_access.routes.max_webapp", AsyncMock(return_value={"ok": True, "status": "linked", "url": "", "browser_login_url": "https://wwc.best/me/#wwc-login=x"})) as webapp:
        response = await client.post("/api/v1/content-access/max-webapp", json={"init_data": "i", "return_to": "https://wwc.best/max-app/", "code": "483 921"}, headers=HOST)
    assert response.status_code == 200 and response.json()["browser_login_url"].endswith("#wwc-login=x")
    webapp.assert_awaited_once_with("whieda", init_data="i", return_to="https://wwc.best/max-app/", code="483 921")


def test_http_exception_mapping_keeps_status_codes():
    from app.login_identities.routes import _http

    exc = _http(rules.error("code_locked"))
    assert isinstance(exc, HTTPException) and exc.status_code == 429 and exc.detail == {"error": "code_locked"}
