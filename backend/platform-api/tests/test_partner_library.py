from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.partner_library.import_manifest import manifest_sha, normalize_manifest
from app.partner_library.service import public_item
from app.partner_library.storage import (
    LocalStorageBackend,
    _build_storage_backend,
    validate_storage_key,
)
from app.settings import Settings, get_settings
from app.tenancy import TenantContext

HOST = {"host": "wwc.best"}
ITEM_ID = UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def library_app(monkeypatch):
    monkeypatch.setattr("app.main.init_pool", AsyncMock())
    monkeypatch.setattr("app.main.close_pool", AsyncMock())
    monkeypatch.setattr("app.main.check_postgres", AsyncMock(return_value=True))

    async def resolve(host: str) -> TenantContext:
        return TenantContext(
            tenant_id="whieda",
            status="active",
            display_name="WHIEDA",
            entitlements={"structure_basic": True},
        )

    monkeypatch.setattr("app.tenancy.resolve_tenant_from_host", resolve)
    get_settings.cache_clear()
    yield create_app()
    get_settings.cache_clear()


@pytest.fixture
async def library_client(library_app):
    async with AsyncClient(
        transport=ASGITransport(app=library_app),
        base_url="http://test",
    ) as client:
        yield client


def paid_route_patches(*, paid: bool = True):
    session = {"telegram_user_id": 99123, "scope": "telegram_verified"}
    subscription = {"partner_paid": paid, "subscription_status": "active" if paid else "suspended"}
    return (
        patch("app.partner_library.routes.read_session_cookie", return_value="session-token"),
        patch(
            "app.partner_library.routes.validate_content_session",
            AsyncMock(return_value=session),
        ),
        patch(
            "app.partner_library.routes.resolve_partner_subscription_by_telegram_user_id",
            AsyncMock(return_value=subscription),
        ),
    )


@pytest.mark.asyncio
async def test_library_list_requires_content_session(library_client):
    response = await library_client.get("/api/v1/partner-library/items", headers=HOST)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_library_list_rejects_verified_but_unpaid(library_client):
    first, second, third = paid_route_patches(paid=False)
    with first, second, third:
        response = await library_client.get("/api/v1/partner-library/items", headers=HOST)
    assert response.status_code == 403
    assert response.json()["error"] == "partner_paid_required"


@pytest.mark.asyncio
async def test_library_list_exposes_metadata_but_not_storage_key(library_client):
    rows = [
        {
            "item_id": str(ITEM_ID),
            "slug": "welcome-presentation",
            "category": "presentations",
            "title": "Презентация",
            "description": "Для первой встречи",
            "kind": "file",
            "mime_type": "application/pdf",
            "size_bytes": 1234,
            "published_at": "2026-09-09T10:00:00+00:00",
        }
    ]
    first, second, third = paid_route_patches()
    with first, second, third, patch(
        "app.partner_library.routes.list_published_items",
        AsyncMock(return_value=rows),
    ):
        response = await library_client.get("/api/v1/partner-library/items", headers=HOST)
    assert response.status_code == 200
    assert response.json()["items"] == rows
    assert response.json()["links"][0]["href"] == "/price/repeat/"
    assert "storage_key" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_download_rechecks_paid_access_and_returns_short_lived_url(library_client):
    first, second, third = paid_route_patches()
    payload = {
        "ok": True,
        "item_id": str(ITEM_ID),
        "url": "https://private.example/signed",
        "expires_in": 300,
    }
    with first, second, third, patch(
        "app.partner_library.routes.create_download",
        AsyncMock(return_value=payload),
    ), patch(
        "app.partner_library.routes.get_storage_backend",
        return_value=object(),
    ):
        response = await library_client.get(
            f"/api/v1/partner-library/items/{ITEM_ID}/download",
            headers=HOST,
        )
    assert response.status_code == 200
    assert response.json() == payload
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_download_does_not_accept_arbitrary_storage_path(library_client):
    response = await library_client.get(
        "/api/v1/partner-library/items/../../secrets/download",
        headers=HOST,
    )
    assert response.status_code in {404, 422}


def test_local_storage_signed_url_is_temporary_and_tamper_evident(tmp_path):
    source = tmp_path / "whieda" / "presentations" / "welcome.txt"
    source.parent.mkdir(parents=True)
    source.write_text("hello", encoding="utf-8")
    storage = LocalStorageBackend(
        tmp_path,
        signing_secret="test-secret",
        base_url="/api/v1/partner-library/local-files",
    )
    url = storage.signed_url("whieda/presentations/welcome.txt", 300)
    assert storage.open_signed_path(url.rsplit("/", 1)[-1]) == source
    with pytest.raises(ValueError):
        storage.open_signed_path(url.rsplit("/", 1)[-1] + "x")


@pytest.mark.parametrize("value", ["", "/absolute.pdf", "../secret", "a//b", "a/./b"])
def test_storage_key_rejects_unsafe_paths(value):
    with pytest.raises(ValueError):
        validate_storage_key(value)


def test_local_storage_is_forbidden_outside_dev_and_test(tmp_path):
    settings = Settings(
        environment="staging",
        platform_partner_library_storage_backend="local",
        platform_partner_library_local_root=str(tmp_path),
    )
    with pytest.raises(RuntimeError, match="only in dev/test"):
        _build_storage_backend(settings)


def test_manifest_is_tenant_scoped_and_sha_is_stable():
    payload = {
        "version": 1,
        "tenant_id": "whieda",
        "items": [
            {
                "slug": "welcome-presentation",
                "category": "presentations",
                "title": "Презентация",
                "kind": "file",
                "storage_key": "whieda/presentations/welcome.pdf",
                "mime_type": "application/pdf",
                "status": "published",
            }
        ],
    }
    normalized = normalize_manifest(payload, tenant_id="whieda")
    assert manifest_sha(normalized) == manifest_sha(normalized)
    with pytest.raises(ValueError):
        normalize_manifest(payload, tenant_id="nsp-maxim")


def test_public_item_never_exposes_storage_key():
    item = public_item(
        {
            "item_id": ITEM_ID,
            "slug": "welcome-presentation",
            "category": "presentations",
            "title": "Презентация",
            "description": None,
            "kind": "file",
            "storage_key": "whieda/private/welcome.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 100,
            "published_at": None,
        }
    )
    assert item["item_id"] == str(ITEM_ID)
    assert "storage_key" not in item


def test_partner_library_migration_has_rls_and_published_contract():
    root = Path(__file__).resolve().parents[3]
    sql = (root / "postgres" / "sql" / "platform_partner_library_v1.sql").read_text(
        encoding="utf-8"
    ).lower()
    assert "create table if not exists partner_library_items" in sql
    assert "unique (tenant_id, slug)" in sql
    assert "status in ('draft', 'published', 'archived')" in sql
    assert "enable row level security" in sql
    assert "platform_current_tenant_id()" in sql


def test_partner_library_migration_is_registered_after_subscriptions():
    root = Path(__file__).resolve().parents[3]
    apply_script = (
        root / "postgres" / "scripts" / "apply_staging_platform_all.ps1"
    ).read_text(encoding="utf-8")
    assert apply_script.index("platform_partner_subscriptions_v1.sql") < apply_script.index(
        "platform_partner_library_v1.sql"
    )
