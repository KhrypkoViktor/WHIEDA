"""Multi-tenant Telegram ingress truth table and outbound isolation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.telegram.bindings import BotBindingContext, binding_context_scope
from app.telegram.inbox import InMemoryInboxStore, set_inbox_store_for_tests


def _context(tenant, *, binding_id: str = "nsp-binding") -> BotBindingContext:
    return BotBindingContext(
        binding_id=binding_id,
        tenant=tenant,
        bot_token_ref="env:NSP_BOT_TOKEN",
        webhook_secret_ref="env:NSP_WEBHOOK_SECRET",
        bot_username="NSP_Leader_bot",
        status="active",
        processing_mode="core",
        bot_token="nsp-token",
        webhook_secret="nsp-secret",
    )


def _update() -> dict:
    return {
        "update_id": 501,
        "message": {
            "message_id": 11,
            "text": "привет",
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200},
            "contact": {"phone_number": "+375000000000"},
        },
    }


@pytest.fixture(autouse=True)
def inbox_store():
    store = InMemoryInboxStore()
    set_inbox_store_for_tests(store)
    yield store
    set_inbox_store_for_tests(None)


class _Connection:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


class _Pool:
    def connection(self, **_kwargs):
        return _Connection()


@pytest.mark.asyncio
async def test_unknown_binding_returns_200_without_processing(client, monkeypatch, inbox_store):
    resolve = AsyncMock(return_value=None)
    process = AsyncMock()
    monkeypatch.setattr("app.telegram.routes.resolve_bot_binding_context", resolve)
    monkeypatch.setattr("app.telegram.routes._process_telegram_update", process)

    response = await client.post("/v1/telegram/unknown/webhook", json=_update())

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    process.assert_not_awaited()
    assert inbox_store.snapshot_rows() == []


@pytest.mark.asyncio
async def test_active_binding_bad_secret_returns_403(client, whieda_tenant, monkeypatch, inbox_store):
    context = _context(whieda_tenant)
    monkeypatch.setattr(
        "app.telegram.routes.resolve_bot_binding_context",
        AsyncMock(return_value=context),
    )
    process = AsyncMock()
    monkeypatch.setattr("app.telegram.routes._process_telegram_update", process)

    response = await client.post(
        "/v1/telegram/nsp-binding/webhook",
        json=_update(),
        headers={"x-telegram-bot-api-secret-token": "wrong"},
    )

    assert response.status_code == 403
    assert response.json()["error"] == "invalid_webhook_secret"
    process.assert_not_awaited()
    assert inbox_store.snapshot_rows() == []
    assert "nsp-secret" not in response.text
    assert "nsp-token" not in response.text


def test_active_binding_unicode_bad_secret_returns_403(whieda_tenant):
    from app.telegram.bindings import verify_webhook_secret

    context = _context(whieda_tenant)
    with pytest.raises(HTTPException) as exc:
        verify_webhook_secret(context, "неверный")
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "invalid_webhook_secret"


@pytest.mark.asyncio
async def test_active_binding_valid_secret_returns_200_and_processes(
    client, whieda_tenant, monkeypatch, inbox_store
):
    context = _context(whieda_tenant)
    monkeypatch.setattr(
        "app.telegram.routes.resolve_bot_binding_context",
        AsyncMock(return_value=context),
    )
    process = AsyncMock()
    monkeypatch.setattr("app.telegram.routes._process_telegram_update", process)

    response = await client.post(
        "/v1/telegram/nsp-binding/webhook",
        json=_update(),
        headers={"x-telegram-bot-api-secret-token": "nsp-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    process.assert_awaited_once()
    assert process.await_args.args[0] is context
    # Durable inbox is opt-in per binding; the default keeps the in-process path.
    assert inbox_store.snapshot_rows() == []
    assert "nsp-token" not in response.text
    assert "nsp-secret" not in response.text


@pytest.mark.asyncio
async def test_durable_inbox_binding_persists_update_before_processing(
    client, whieda_tenant, monkeypatch, inbox_store
):
    context = _context(whieda_tenant)
    monkeypatch.setattr(
        "app.telegram.routes.resolve_bot_binding_context",
        AsyncMock(return_value=context),
    )
    monkeypatch.setattr("app.telegram.routes._durable_inbox_enabled", lambda binding: True)
    process = AsyncMock()
    monkeypatch.setattr("app.telegram.routes._process_telegram_update", process)

    response = await client.post(
        "/v1/telegram/nsp-binding/webhook",
        json=_update(),
        headers={"x-telegram-bot-api-secret-token": "nsp-secret"},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    process.assert_awaited_once()
    assert process.await_args.args[0] is context
    rows = inbox_store.snapshot_rows()
    assert len(rows) == 1
    assert rows[0].binding_id == "nsp-binding"
    assert rows[0].telegram_update_id == 501
    assert rows[0].state == "processed"
    assert "phone_number" not in str(rows[0].payload)
    assert "+375000000000" not in str(rows[0].payload)
    assert "nsp-token" not in response.text
    assert "nsp-secret" not in response.text


@pytest.mark.asyncio
async def test_binding_store_unavailable_returns_503(client, monkeypatch, inbox_store):
    monkeypatch.setattr(
        "app.telegram.routes.resolve_bot_binding_context",
        AsyncMock(
            side_effect=HTTPException(
                status_code=503,
                detail={"error": "bot_binding_store_unavailable"},
            )
        ),
    )

    response = await client.post("/v1/telegram/nsp-binding/webhook", json=_update())

    assert response.status_code == 503
    assert response.json()["error"] == "bot_binding_store_unavailable"
    assert inbox_store.snapshot_rows() == []


def test_binding_scope_keeps_nsp_token_and_username(whieda_tenant):
    from app.telegram.bindings import current_bot_binding

    context = _context(whieda_tenant)
    with binding_context_scope(context):
        current = current_bot_binding()
        assert current.bot_token == "nsp-token"
        assert current.bot_username == "NSP_Leader_bot"
        assert current.binding_id == "nsp-binding"


def test_nsp_binding_cannot_use_legacy_mode(whieda_tenant):
    from app.telegram.bindings import validate_processing_mode

    tenant = type(whieda_tenant)(
        tenant_id="nsp-maxim",
        status="active",
        display_name="NSP",
        entitlements={},
    )
    with pytest.raises(HTTPException) as exc:
        validate_processing_mode(tenant, "legacy")
    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "bot_binding_misconfigured"


@pytest.mark.asyncio
async def test_outbound_text_uses_current_binding_token(whieda_tenant, monkeypatch):
    from app.telegram.processor import deliver_text

    context = _context(whieda_tenant)
    send = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr("app.telegram.processor.send_telegram_text", send)

    with binding_context_scope(context):
        await deliver_text(100, "ответ NSP")

    send.assert_awaited_once()
    assert send.await_args.kwargs["bot_token"] == "nsp-token"


def test_binding_repr_does_not_expose_secrets(whieda_tenant):
    rendered = repr(_context(whieda_tenant))
    assert "nsp-token" not in rendered
    assert "nsp-secret" not in rendered


@pytest.mark.asyncio
async def test_nsp_binding_cannot_confirm_whieda_admin_challenge(
    whieda_tenant, monkeypatch
):
    from app.telegram.admin_login import try_handle_admin_login

    context = _context(
        type(whieda_tenant)(
            tenant_id="nsp-maxim",
            status="active",
            display_name="NSP",
            entitlements={},
        )
    )
    confirm = AsyncMock()
    monkeypatch.setattr(
        "app.telegram.admin_login.confirm_login_from_telegram",
        confirm,
    )
    update = {
        "update_id": 502,
        "message": {
            "text": "/start admin_login_secret",
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200},
        },
    }

    with binding_context_scope(context):
        result = await try_handle_admin_login(update, trace_id="nsp-admin")

    assert result is not None
    assert result["status"] == "binding_not_allowed"
    confirm.assert_not_awaited()


def test_runtime_surfaces_do_not_read_global_telegram_identity():
    app_dir = Path(__file__).resolve().parents[1] / "app" / "telegram"
    for name in ("routes.py", "processor.py", "catalog_browse.py", "admin_login.py"):
        text = (app_dir / name).read_text(encoding="utf-8")
        assert "telegram_bot_token" not in text
        assert "telegram_webhook_secret" not in text
        assert "telegram_bot_username" not in text


@pytest.mark.asyncio
async def test_disabled_binding_resolves_to_none(monkeypatch):
    from app.telegram.bindings import resolve_bot_binding_context

    monkeypatch.setattr("app.telegram.bindings.get_pool", lambda: _Pool())
    monkeypatch.setattr(
        "app.telegram.bindings.fetch_one",
        AsyncMock(
            return_value={
                "tenant_id": "nsp-maxim",
                "tenant_status": "active",
                "display_name": "NSP",
                "binding_status": "disabled",
            }
        ),
    )

    assert await resolve_bot_binding_context("disabled") is None


@pytest.mark.asyncio
async def test_rotated_binding_resolves_to_none(monkeypatch):
    from app.telegram.bindings import resolve_bot_binding_context

    monkeypatch.setattr("app.telegram.bindings.get_pool", lambda: _Pool())
    monkeypatch.setattr(
        "app.telegram.bindings.fetch_one",
        AsyncMock(
            return_value={
                "tenant_id": "nsp-maxim",
                "tenant_status": "active",
                "display_name": "NSP",
                "binding_status": "rotated",
            }
        ),
    )

    assert await resolve_bot_binding_context("rotated") is None


@pytest.mark.asyncio
async def test_active_binding_reads_refs_and_secrets(monkeypatch):
    from app.telegram.bindings import resolve_bot_binding_context

    monkeypatch.setenv("NSP_BOT_TOKEN", "resolved-token")
    monkeypatch.setenv("NSP_WEBHOOK_SECRET", "resolved-secret")
    monkeypatch.setattr("app.telegram.bindings.get_pool", lambda: _Pool())
    monkeypatch.setattr(
        "app.telegram.bindings.fetch_one",
        AsyncMock(
            return_value={
                "tenant_id": "nsp-maxim",
                "tenant_status": "active",
                "display_name": "NSP",
                "binding_status": "active",
                "bot_token_ref": "env:NSP_BOT_TOKEN",
                "webhook_secret_ref": "env:NSP_WEBHOOK_SECRET",
                "bot_username": "NSP_Leader_bot",
                "processing_mode": "core",
            }
        ),
    )
    monkeypatch.setattr(
        "app.telegram.bindings.fetch_all_entitlements",
        AsyncMock(return_value=[{"feature_key": "structure_basic", "enabled": True}]),
    )

    context = await resolve_bot_binding_context("nsp")

    assert context is not None
    assert context.tenant.tenant_id == "nsp-maxim"
    assert context.bot_token == "resolved-token"
    assert context.webhook_secret == "resolved-secret"


@pytest.mark.asyncio
async def test_active_binding_missing_secret_value_returns_503(monkeypatch):
    from app.telegram.bindings import resolve_bot_binding_context

    monkeypatch.delenv("MISSING_NSP_TOKEN", raising=False)
    monkeypatch.setenv("NSP_WEBHOOK_SECRET", "resolved-secret")
    monkeypatch.setattr("app.telegram.bindings.get_pool", lambda: _Pool())
    monkeypatch.setattr(
        "app.telegram.bindings.fetch_one",
        AsyncMock(
            return_value={
                "tenant_id": "nsp-maxim",
                "tenant_status": "active",
                "display_name": "NSP",
                "binding_status": "active",
                "bot_token_ref": "env:MISSING_NSP_TOKEN",
                "webhook_secret_ref": "env:NSP_WEBHOOK_SECRET",
                "bot_username": "NSP_Leader_bot",
                "processing_mode": "core",
            }
        ),
    )
    monkeypatch.setattr(
        "app.telegram.bindings.fetch_all_entitlements",
        AsyncMock(return_value=[]),
    )

    with pytest.raises(HTTPException) as exc:
        await resolve_bot_binding_context("nsp")

    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "bot_binding_secret_unavailable"
