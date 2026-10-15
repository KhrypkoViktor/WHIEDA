"""Вход через Google/Max по коду владельца (V34, 15.10.2026) — правила, ручки, бот; база — подделки.

Настоящий SQL (миграция, RLS, попытки, бэкфилл) — tests/test_login_identities_postgres.py.
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
from app.login_identities.rules import LoginIdentityError
from app.settings import get_settings
from app.telegram.update_parser import TelegramMessage

HOST = {"host": "wwc.best"}
OWNER = 2


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


# ---- redeem (подделка соединения) ------------------------------------------------------------------


class FakeConn:
    """login_link_codes + account_identities по префиксу запроса."""

    def __init__(self) -> None:
        now = datetime.now(timezone.utc)
        self.codes = {
            rules.code_hash("whieda", "111111"): {"code_id": "c1", "telegram_user_id": 700, "issued_by": OWNER, "expires_at": now + timedelta(hours=1), "used_at": None},
            rules.code_hash("whieda", "222222"): {"code_id": "c2", "telegram_user_id": 701, "issued_by": OWNER, "expires_at": now - timedelta(hours=1), "used_at": None},
            rules.code_hash("whieda", "333333"): {"code_id": "c3", "telegram_user_id": 702, "issued_by": OWNER, "expires_at": now + timedelta(hours=1), "used_at": now},
        }
        self.identities: list[dict] = []
        self.executed: list[tuple[str, tuple]] = []

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
        if q.startswith("select code_id, telegram_user_id, issued_by, expires_at, used_at"):
            return self.codes.get(params[1])
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
def conn():
    fake = FakeConn()
    with patch.object(service, "fetch_one", fake.fetch_one):
        yield fake


@pytest.mark.asyncio
async def test_redeem_links_marks_the_code_used_and_logs_without_secrets(conn, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="whieda.login_identities"):
        out = await service.redeem_code_on(conn, "whieda", code="111 111", provider="google", external_id="g-1", email="e@x.com", display_name="Е")
    assert out["telegram_user_id"] == 700 and not out["already_linked"]
    assert conn.identities[0]["linked_by"] == f"code:{OWNER}"
    assert any("update login_link_codes set used_at" in q and p == ("google", "c1") for q, p in conn.executed)
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
async def test_external_id_already_bound_to_another_person_is_refused(conn):
    conn.identities.append({"identity_id": "old", "provider": "google", "external_id": "g-1", "telegram_user_id": 999})
    with pytest.raises(LoginIdentityError) as info:
        await service.redeem_code_on(conn, "whieda", code="111111", provider="google", external_id="g-1", email=None, display_name=None)
    assert info.value.code == "identity_taken"
    assert not any("update login_link_codes" in q for q, _ in conn.executed), "the code stays valid for its owner"


@pytest.mark.asyncio
async def test_same_person_linking_twice_is_idempotent(conn):
    conn.identities.append({"identity_id": "old", "provider": "google", "external_id": "g-1", "telegram_user_id": 700})
    out = await service.redeem_code_on(conn, "whieda", code="111111", provider="google", external_id="g-1", email=None, display_name=None)
    assert out == {"telegram_user_id": 700, "identity_id": "old", "already_linked": True}


def test_telegram_cannot_be_revoked_and_unknown_providers_are_rejected():
    for provider, code in (("telegram", "invalid_provider"), ("vk", "invalid_provider")):
        with pytest.raises(LoginIdentityError) as info:
            rules.normalize_provider(provider)
        assert info.value.code == code


# ---- Google: start / callback / link (ручки) --------------------------------------------------------


@pytest.mark.asyncio
async def test_google_start_is_off_without_settings(client, monkeypatch):
    for name in ("PLATFORM_GOOGLE_OAUTH_CLIENT_ID", "PLATFORM_GOOGLE_OAUTH_CLIENT_SECRET", "PLATFORM_GOOGLE_OAUTH_REDIRECT"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    try:
        response = await client.get("/api/v1/content-access/login/google/start", params={"return_to": "/me/"}, headers=HOST)
    finally:
        get_settings.cache_clear()
    assert response.status_code == 503 and response.json()["error"] == "google_disabled"


@pytest.mark.asyncio
async def test_google_start_redirects_to_google_with_pkce_and_state(client, google_env):
    inserted: list[tuple] = []

    @asynccontextmanager
    async def connection(_tenant):
        class Cursor:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def execute(self, query, params=None):
                inserted.append((" ".join(query.split()), params))

        yield SimpleNamespace(cursor=lambda: Cursor())

    with patch.object(service, "tenant_connection", connection), patch.object(service, "warm_hosts", AsyncMock()):
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
    (sql, params) = inserted[0]
    assert sql.startswith("insert into login_pending")
    assert params[1] == rules.sha256_hex(query["state"][0]) and params[2] == rules.sha256_hex(query["nonce"][0])
    assert params[4] == "samtsova.wwc.best" and params[5] == "https://samtsova.wwc.best/price/"
    assert params[6] == "11111111-1111-1111-1111-111111111111"
    assert "secret-1" not in response.headers["location"]


@pytest.mark.asyncio
async def test_google_start_refuses_a_foreign_return_host(client, google_env):
    with patch.object(service, "warm_hosts", AsyncMock()):
        response = await client.get(
            "/api/v1/content-access/login/google/start", params={"return_to": "https://evil.example/"}, headers=HOST
        )
    assert response.status_code == 400 and response.json()["error"] == "invalid_return_to"


@pytest.mark.asyncio
async def test_callback_with_known_identity_sets_the_cookie_on_the_redirect(client, google_env):
    result = {"outcome": "signed_in", "telegram_user_id": 700, "host": "samtsova.wwc.best", "return_to": "https://samtsova.wwc.best/price/", "visitor_session_id": None}
    with patch.object(service, "google_callback", AsyncMock(return_value=result)), \
         patch.object(service, "session_for", AsyncMock(return_value=({"ok": True}, "raw-session"))) as session_for:
        response = await client.get("/api/v1/content-access/login/google/callback", params={"state": "s", "code": "c"}, headers=HOST)
    assert response.status_code == 302 and response.headers["location"] == "https://samtsova.wwc.best/price/"
    cookie = response.headers["set-cookie"]
    assert "wwc_content_session=raw-session" in cookie and "Domain=.wwc.best" in cookie and "HttpOnly" in cookie
    session_for.assert_awaited_once_with("whieda", telegram_user_id=700, visitor_session_id=None, source="google_login")


@pytest.mark.asyncio
async def test_callback_for_a_showcase_host_hands_over_a_one_time_login(client, google_env):
    result = {"outcome": "signed_in", "telegram_user_id": 700, "host": "academy.almira.by", "return_to": "https://academy.almira.by/academy/", "visitor_session_id": None}
    with patch.object(service, "google_callback", AsyncMock(return_value=result)), \
         patch.object(service, "browser_login_url", AsyncMock(return_value="https://academy.almira.by/academy/#wwc-login=cid.nonce")) as browser:
        response = await client.get("/api/v1/content-access/login/google/callback", params={"state": "s", "code": "c"}, headers=HOST)
    assert response.status_code == 302 and response.headers["location"].endswith("#wwc-login=cid.nonce")
    assert "set-cookie" not in response.headers
    browser.assert_awaited_once_with("whieda", telegram_user_id=700, return_to="https://academy.almira.by/academy/")


@pytest.mark.asyncio
async def test_callback_without_identity_sends_the_site_to_the_code_screen(client, google_env):
    result = {"outcome": "link_required", "link_token": "tok-1", "host": "wwc.best", "return_to": "https://wwc.best/me/"}
    with patch.object(service, "google_callback", AsyncMock(return_value=result)):
        response = await client.get("/api/v1/content-access/login/google/callback", params={"state": "s", "code": "c"}, headers=HOST)
    assert response.status_code == 302 and response.headers["location"] == "https://wwc.best/me/#wwc-link=tok-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["google_denied", "google_token_invalid", "email_unverified"])
async def test_callback_errors_go_back_to_the_page_with_a_code(client, google_env, error):
    result = {"outcome": "error", "error": error, "host": "wwc.best", "return_to": "https://wwc.best/me/"}
    with patch.object(service, "google_callback", AsyncMock(return_value=result)):
        response = await client.get("/api/v1/content-access/login/google/callback", params={"state": "s", "error": "access_denied"}, headers=HOST)
    assert response.status_code == 302 and response.headers["location"] == f"https://wwc.best/me/#wwc-link-error={error}"


@pytest.mark.asyncio
async def test_unknown_state_is_a_plain_400(client, google_env):
    with patch.object(service, "google_callback", AsyncMock(side_effect=rules.error("google_state_invalid"))):
        response = await client.get("/api/v1/content-access/login/google/callback", params={"state": "zzz", "code": "c"}, headers=HOST)
    assert response.status_code == 400 and response.json()["error"] == "google_state_invalid"


@pytest.mark.asyncio
async def test_link_with_the_owner_code_signs_in(client, google_env):
    linked = {"telegram_user_id": 700, "identity_id": "id-1", "already_linked": False, "visitor_session_id": None}
    with patch.object(service, "link_pending", AsyncMock(return_value=linked)) as link_pending, \
         patch.object(service, "session_for", AsyncMock(return_value=({"ok": True, "status": "authenticated", "expires_at": "2026-11-14T00:00:00+00:00"}, "raw-session"))):
        response = await client.post("/api/v1/content-access/login/link", json={"pending_id": "tok-1", "code": "483 921"}, headers=HOST)
    assert response.status_code == 200 and response.json()["status"] == "authenticated"
    assert "wwc_content_session=raw-session" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "private, no-store"
    link_pending.assert_awaited_once_with("whieda", link_token="tok-1", code="483 921")


@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "status"), [
    ("code_invalid", 400), ("code_expired", 410), ("code_used", 409), ("code_locked", 429),
    ("identity_taken", 409), ("pending_invalid", 404), ("pending_expired", 410),
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
async def test_non_owner_cannot_issue_codes(sent):
    with patch.object(service, "issue_code", AsyncMock()) as issue:
        out = await bot.try_handle_login_identity_message(TENANT, _msg("код входа volkova", user_id=700), trace_id="t")
    assert out["status"] == "forbidden" and sent == ["Команда недоступна."]
    issue.assert_not_awaited()
    assert await bot.try_handle_login_identity_message(TENANT, _msg("привет"), trace_id="t") is None
    assert (await bot.try_handle_login_identity_message(TENANT, _msg("код входа x", chat_type="supergroup"), trace_id="t"))["status"] == "private_chat_required"


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
