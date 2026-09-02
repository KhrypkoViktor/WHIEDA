"""WWC Owner Cabinet P0.1 acceptance checks (task §7)."""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.admin.auth.dependencies import AdminSession, resolve_effective_tenant
from app.admin.auth.service import confirm_login_from_telegram, poll_login_challenge
from app.admin.dto import pick_lead_metadata
from app.admin.masking import mask_contact
from app.admin.services.leads import _lead_detail_item, build_leads_list
from app.admin.ref_urls import canonical_public_ref_url
from app.admin.services.markets import (
    build_markets_payload,
    build_service_centers_payload,
    build_sync_status_payload,
)
from app.main import create_app
from app.settings import get_settings
from app.tenancy import TenantContext

SUPER_ID = 900001
OTHER_ID = 900002
NONCE = "browser-nonce-0123456789ab"
SECRET = "test-admin-confirm-secret"


@pytest.fixture
def admin_app(monkeypatch):
    monkeypatch.setenv("PLATFORM_ADMIN_SUPER_TELEGRAM_IDS", str(SUPER_ID))
    monkeypatch.setenv("PLATFORM_ADMIN_CONFIRM_SECRET", SECRET)
    monkeypatch.setenv("PLATFORM_TELEGRAM_BOT_USERNAME", "wwc_test_bot")
    monkeypatch.setenv("PLATFORM_ADMIN_COOKIE_SECURE", "false")
    get_settings.cache_clear()

    monkeypatch.setattr("app.main.init_pool", AsyncMock())
    monkeypatch.setattr("app.main.close_pool", AsyncMock())
    monkeypatch.setattr("app.main.check_postgres", AsyncMock(return_value=True))

    async def resolve(host: str) -> TenantContext:
        from app.tenancy import normalize_host

        mapping = {
            "cabinet.test.local": "whieda",
            "acme.test.local": "test-acme",
        }
        normalized = normalize_host(host)
        tenant_id = mapping.get(normalized, "whieda")
        return TenantContext(
            tenant_id=tenant_id,
            status="active",
            display_name=tenant_id,
            entitlements={"structure_basic": True, "partner_leads": True, "deep_coach": False},
        )

    monkeypatch.setattr("app.tenancy.resolve_tenant_from_host", resolve)
    monkeypatch.setattr("app.tenancy._load_tenant", resolve)

    application = create_app()
    application.state.http_client = AsyncMock()
    yield application
    get_settings.cache_clear()


@pytest.fixture
async def admin_client(admin_app):
    transport = ASGITransport(app=admin_app)
    async with AsyncClient(transport=transport, base_url="http://cabinet.test.local") as ac:
        yield ac


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _mock_admin_conn(conn: AsyncMock | None = None):
    conn = conn or AsyncMock()
    cur = AsyncMock()

    class _CursorCM:
        def __init__(self, value):
            self.value = value

        async def __aenter__(self):
            return self.value

        async def __aexit__(self, exc_type, exc, tb):
            return False

    conn.cursor = lambda c=cur: _CursorCM(c)

    @asynccontextmanager
    async def _cm():
        yield conn

    return _cm, conn, cur


def _mock_approved_poll_cursor(cur: AsyncMock, *, challenge_id: str, principal_id: str, claim_succeeds: bool = True):
    async def execute(query, params=None):
        cur._last_query = query

    async def fetchone():
        query = str(getattr(cur, "_last_query", "")).lower()
        if "returning" in query and "platform_admin_login_challenges" in query:
            if claim_succeeds:
                return {"challenge_id": challenge_id, "principal_id": principal_id}
            return None
        return None

    cur.execute = AsyncMock(side_effect=execute)
    cur.fetchone = AsyncMock(side_effect=fetchone)


@pytest.mark.asyncio
async def test_unknown_telegram_id_rejected():
    with patch("app.admin.auth.service._resolve_or_bootstrap_principal", new=AsyncMock(return_value=None)):
        admin_cm, _, _ = _mock_admin_conn()
        with patch("app.admin.auth.service.admin_connection", admin_cm):
            with pytest.raises(HTTPException) as exc:
                await confirm_login_from_telegram(challenge_token="test-token", telegram_user_id=OTHER_ID)
            assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_super_admin_bootstrap_from_env():
    principal = {
        "principal_id": str(uuid.uuid4()),
        "telegram_user_id": SUPER_ID,
        "role": "super_admin",
        "status": "active",
        "display_name": "Platform Super Admin",
        "allowed_tenant_ids": None,
    }
    with patch("app.admin.auth.service._resolve_or_bootstrap_principal", new=AsyncMock(return_value=principal)):
        with patch("app.admin.auth.service.fetch_one", new=AsyncMock(return_value={
            "challenge_id": str(uuid.uuid4()),
            "status": "pending",
            "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
            "used_at": None,
        })):
            admin_cm, _, _ = _mock_admin_conn()
            with patch("app.admin.auth.service.admin_connection", admin_cm):
                with patch("app.admin.auth.service.write_audit_log", new=AsyncMock()):
                    result = await confirm_login_from_telegram(
                        challenge_token="test-token",
                        telegram_user_id=SUPER_ID,
                    )
    assert result["ok"] is True
    assert result["status"] == "approved"


@pytest.mark.asyncio
async def test_challenge_expired_and_nonce_mismatch():
    expired = datetime.now(timezone.utc) - timedelta(minutes=1)
    challenge_id = str(uuid.uuid4())
    row = {
        "challenge_id": challenge_id,
        "status": "pending",
        "expires_at": expired,
        "used_at": None,
        "browser_nonce_hash": _hash(NONCE),
        "principal_id": str(uuid.uuid4()),
        "principal_status": "active",
    }

    with patch("app.admin.auth.service.fetch_one", new=AsyncMock(return_value=row)):
        admin_cm, _, _ = _mock_admin_conn()
        with patch("app.admin.auth.service.admin_connection", admin_cm):
            payload, session = await poll_login_challenge(challenge_id=challenge_id, browser_nonce=NONCE)
            assert payload["status"] == "expired"
            assert session is None

    row_bad_nonce = dict(row)
    row_bad_nonce["expires_at"] = datetime.now(timezone.utc) + timedelta(minutes=5)
    with patch("app.admin.auth.service.fetch_one", new=AsyncMock(return_value=row_bad_nonce)):
        admin_cm, _, _ = _mock_admin_conn()
        with patch("app.admin.auth.service.admin_connection", admin_cm):
            with pytest.raises(HTTPException) as exc:
                await poll_login_challenge(challenge_id=challenge_id, browser_nonce="wrong-nonce-value-123456")
            assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_poll_response_has_no_bearer_or_session_token_in_body():
    challenge_id = str(uuid.uuid4())
    row = {
        "challenge_id": challenge_id,
        "status": "approved",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "used_at": None,
        "browser_nonce_hash": _hash(NONCE),
        "principal_id": str(uuid.uuid4()),
        "principal_status": "active",
    }
    with patch("app.admin.auth.service.fetch_one", new=AsyncMock(return_value=row)):
        admin_cm, _, cur = _mock_admin_conn()
        _mock_approved_poll_cursor(cur, challenge_id=challenge_id, principal_id=row["principal_id"])
        with patch("app.admin.auth.service.admin_connection", admin_cm):
            with patch("app.admin.auth.service.write_audit_log", new=AsyncMock()):
                payload, raw_session = await poll_login_challenge(
                    challenge_id=challenge_id,
                    browser_nonce=NONCE,
                )
    assert raw_session is not None
    assert "token" not in payload
    assert "bearer" not in json.dumps(payload).lower()


@pytest.mark.asyncio
async def test_concurrent_poll_creates_single_session():
    challenge_id = str(uuid.uuid4())
    principal_id = str(uuid.uuid4())
    row = {
        "challenge_id": challenge_id,
        "status": "approved",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "used_at": None,
        "browser_nonce_hash": _hash(NONCE),
        "principal_id": principal_id,
        "principal_status": "active",
    }
    claim_count = 0
    session_inserts = 0

    admin_cm, conn, cur = _mock_admin_conn()

    async def execute(query, params=None):
        nonlocal claim_count, session_inserts
        cur._last_query = query
        if "insert into platform_admin_sessions" in str(query).lower():
            session_inserts += 1

    async def fetchone():
        nonlocal claim_count
        query = str(getattr(cur, "_last_query", "")).lower()
        if "returning" in query and "platform_admin_login_challenges" in query:
            if claim_count == 0:
                claim_count += 1
                return {"challenge_id": challenge_id, "principal_id": principal_id}
            return None
        return None

    cur.execute = AsyncMock(side_effect=execute)
    cur.fetchone = AsyncMock(side_effect=fetchone)

    async def run_poll():
        with patch("app.admin.auth.service.fetch_one", new=AsyncMock(return_value=dict(row))):
            with patch("app.admin.auth.service.admin_connection", admin_cm):
                with patch("app.admin.auth.service.write_audit_log", new=AsyncMock()):
                    return await poll_login_challenge(challenge_id=challenge_id, browser_nonce=NONCE)

    import asyncio

    results = await asyncio.gather(run_poll(), run_poll())
    sessions = [r[1] for r in results if r[1] is not None]
    statuses = [r[0]["status"] for r in results]
    assert len(sessions) == 1
    assert session_inserts == 1
    assert "authenticated" in statuses
    assert "used" in statuses


@pytest.mark.asyncio
async def test_used_challenge_poll_returns_used_without_session():
    challenge_id = str(uuid.uuid4())
    row = {
        "challenge_id": challenge_id,
        "status": "used",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "used_at": datetime.now(timezone.utc),
        "browser_nonce_hash": _hash(NONCE),
        "principal_id": str(uuid.uuid4()),
        "principal_status": "active",
    }
    with patch("app.admin.auth.service.fetch_one", new=AsyncMock(return_value=row)):
        admin_cm, _, _ = _mock_admin_conn()
        with patch("app.admin.auth.service.admin_connection", admin_cm):
            payload, session = await poll_login_challenge(challenge_id=challenge_id, browser_nonce=NONCE)
    assert payload["status"] == "used"
    assert session is None


@pytest.mark.asyncio
async def test_expired_approved_challenge_poll_no_session():
    challenge_id = str(uuid.uuid4())
    row = {
        "challenge_id": challenge_id,
        "status": "approved",
        "expires_at": datetime.now(timezone.utc) - timedelta(minutes=1),
        "used_at": None,
        "browser_nonce_hash": _hash(NONCE),
        "principal_id": str(uuid.uuid4()),
        "principal_status": "active",
    }
    with patch("app.admin.auth.service.fetch_one", new=AsyncMock(return_value=row)):
        admin_cm, _, _ = _mock_admin_conn()
        with patch("app.admin.auth.service.admin_connection", admin_cm):
            payload, session = await poll_login_challenge(challenge_id=challenge_id, browser_nonce=NONCE)
    assert payload["status"] == "expired"
    assert session is None


@pytest.mark.asyncio
async def test_unauthenticated_and_role_forbidden(admin_client):
    resp = await admin_client.get("/v1/admin/me")
    assert resp.status_code == 401

    session = AdminSession(
        session_id="s1",
        principal_id=str(uuid.uuid4()),
        telegram_user_id=SUPER_ID,
        role="viewer",
        display_name="Viewer",
        allowed_tenant_ids=["whieda"],
        active_tenant_id=None,
    )
    with pytest.raises(HTTPException) as exc:
        from app.admin.auth.dependencies import require_admin_roles

        require_admin_roles(session, "super_admin", "admin")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_logout_revokes_session(admin_client):
    with patch("app.admin.routes.revoke_session", new=AsyncMock(return_value=True)):
        resp = await admin_client.post("/v1/admin/auth/logout")
        assert resp.status_code == 200

    with patch("app.admin.routes.read_session_cookie", return_value="sess"):
        with patch("app.admin.routes.revoke_session", new=AsyncMock(return_value=True)) as revoke:
            resp = await admin_client.post("/v1/admin/auth/logout")
            assert resp.status_code == 200
            revoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_super_admin_cross_tenant_is_audited():
    session = AdminSession(
        session_id="s1",
        principal_id=str(uuid.uuid4()),
        telegram_user_id=SUPER_ID,
        role="super_admin",
        display_name="Victor",
        allowed_tenant_ids=None,
        active_tenant_id=None,
    )

    class Req:
        state = type(
            "S",
            (),
            {"tenant": TenantContext("whieda", "active", "WHIEDA", {"partner_leads": True})},
        )()

    with patch("app.admin.auth.dependencies._assert_tenant_exists", new=AsyncMock()):
        with patch("app.admin.auth.dependencies.write_audit_log", new=AsyncMock()) as audit:
            tenant = await resolve_effective_tenant(Req(), session, requested_tenant_id="test-acme")
            assert tenant == "test-acme"
            audit.assert_awaited()


@pytest.mark.asyncio
async def test_tenant_admin_cannot_read_foreign_tenant():
    session = AdminSession(
        session_id="s1",
        principal_id=str(uuid.uuid4()),
        telegram_user_id=OTHER_ID,
        role="admin",
        display_name="Tenant Admin",
        allowed_tenant_ids=["whieda"],
        active_tenant_id=None,
    )

    class Req:
        state = type(
            "S",
            (),
            {"tenant": TenantContext("whieda", "active", "WHIEDA", {"partner_leads": True})},
        )()

    with pytest.raises(HTTPException) as exc:
        await resolve_effective_tenant(Req(), session, requested_tenant_id="test-acme")
    assert exc.value.status_code == 403


def test_list_masks_contacts_and_detail_metadata_allowlist():
    assert mask_contact("+375291234567") == "+3***67"
    assert "@" in mask_contact("user@example.com")
    meta = pick_lead_metadata({"visitor_session_id": "abc", "secret_token": "nope"})
    assert "visitor_session_id" in meta
    assert "secret_token" not in meta


@pytest.mark.asyncio
async def test_leads_list_uses_tenant_connection(admin_client):
    rows = [
        {
            "lead_id": uuid.uuid4(),
            "public_id": "L-1",
            "status": "new",
            "delivery_status": "pending",
            "name": "Test",
            "contact": "+79990001122",
            "product_name": "Prod",
            "product_sku": "M001",
            "initial_ref_code": "abc",
            "first_ref_code": "abc",
            "active_ref_code": "abc",
            "attributed_owner_id": "owner1",
            "assigned_owner_id": "owner1",
            "country_code": "RU",
            "city": "Moscow",
            "metadata": {"visitor_session_id": "sess-1"},
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
    ]
    with patch("app.admin.repositories.leads.list_leads", new=AsyncMock(return_value=(rows, 1))):
        payload = await build_leads_list("whieda", limit=10, offset=0)
    assert payload["ok"] is True
    assert payload["items"][0]["contact_masked"] != rows[0]["contact"]
    assert "contact" not in payload["items"][0]


@pytest.mark.asyncio
async def test_empty_markets_returns_gap_200(admin_client):
    with patch("app.admin.repositories.markets.list_markets", new=AsyncMock(return_value=[])):
        with patch("app.admin.repositories.markets.fetch_markets_sync_registry", new=AsyncMock(return_value=None)):
            with patch("app.admin.repositories.markets.count_markets", new=AsyncMock(return_value=0)):
                with patch("app.admin.repositories.markets.count_active_service_centers", new=AsyncMock(return_value=0)):
                    with patch("app.admin.repositories.markets.list_service_centers", new=AsyncMock(return_value=[])):
                        markets = await build_markets_payload("whieda")
                        sync = await build_sync_status_payload("whieda")
    assert markets["meta"]["field_status"] == "gap"
    assert sync["partners_sync"]["field_status"] == "gap"


@pytest.mark.asyncio
async def test_empty_service_centers_returns_gap_200():
    with patch("app.admin.repositories.markets.list_service_centers", new=AsyncMock(return_value=[])):
        payload = await build_service_centers_payload("whieda")
    assert payload["ok"] is True
    assert payload["items"] == []
    assert payload["meta"]["field_status"] == "gap"


def test_canonical_public_ref_url():
    assert canonical_public_ref_url("ladnaya") == "https://wwc.best/?ref=ladnaya"
    assert canonical_public_ref_url("LADNAYA") == "https://wwc.best/?ref=ladnaya"
    assert canonical_public_ref_url(None) is None


def test_lead_detail_payload_uses_contact_masked_only():
    item = _lead_detail_item(
        {
            "lead_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "contact": "+79991234567",
            "status": "new",
        },
    )
    assert "contact" not in item
    assert "contact_masked" in item
    assert "***" in item["contact_masked"]
    assert "+79991234567" not in item["contact_masked"]
