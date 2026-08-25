"""Durable Telegram inbox/outbox: enqueue-before-ACK, lease, binding-scoped send."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.telegram.bindings import BotBindingContext
from app.telegram.delivery import TelegramDeliveryError, send_telegram_text
from app.telegram.inbox import (
    InMemoryInboxStore,
    compact_telegram_payload,
    get_inbox_store,
    set_inbox_store_for_tests,
    utcnow,
)
from app.telegram.worker import dispatch_inbox_work, handle_claimed_inbox


def _context(tenant, *, binding_id: str, token: str, secret: str) -> BotBindingContext:
    return BotBindingContext(
        binding_id=binding_id,
        tenant=tenant,
        bot_token_ref=f"env:{binding_id.upper()}_BOT_TOKEN",
        webhook_secret_ref=f"env:{binding_id.upper()}_WEBHOOK_SECRET",
        bot_username=f"{binding_id}_bot",
        status="active",
        processing_mode="core",
        bot_token=token,
        webhook_secret=secret,
    )


def _update(update_id: int = 7001, *, phone: bool = False) -> dict:
    message = {
        "message_id": 11,
        "text": "привет",
        "chat": {"id": 100, "type": "private"},
        "from": {"id": 200},
    }
    if phone:
        message["contact"] = {"phone_number": "+375111111111"}
    return {"update_id": update_id, "message": message}


@pytest.fixture
def inbox_store():
    store = InMemoryInboxStore()
    set_inbox_store_for_tests(store)
    yield store
    set_inbox_store_for_tests(None)


@pytest.fixture
def nsp_tenant(whieda_tenant):
    return type(whieda_tenant)(
        tenant_id="nsp-maxim",
        status="active",
        display_name="NSP",
        entitlements={},
    )


@pytest.fixture
def whieda_binding(whieda_tenant):
    return _context(whieda_tenant, binding_id="whieda-advisor-bot", token="whieda-token", secret="whieda-secret")


@pytest.fixture
def nsp_binding(nsp_tenant):
    return _context(nsp_tenant, binding_id="nsp-leader-bot", token="nsp-token", secret="nsp-secret")


def test_compact_payload_drops_contact_and_keeps_update_id():
    payload = compact_telegram_payload(_update(phone=True))
    assert payload is not None
    assert payload["update_id"] == 7001
    assert "+375111111111" not in str(payload)
    assert "contact" not in payload["message"]
    assert payload["message"]["text"] == "привет"


@pytest.mark.asyncio
async def test_same_update_twice_one_binding_one_inbox_row(inbox_store, whieda_binding):
    payload = compact_telegram_payload(_update(8001))
    first = await inbox_store.enqueue(
        binding_id=whieda_binding.binding_id,
        tenant_id=whieda_binding.tenant.tenant_id,
        telegram_update_id=8001,
        payload=payload,
    )
    second = await inbox_store.enqueue(
        binding_id=whieda_binding.binding_id,
        tenant_id=whieda_binding.tenant.tenant_id,
        telegram_update_id=8001,
        payload=payload,
    )
    assert first.inserted is True
    assert second.inserted is False
    assert first.inbox_id == second.inbox_id
    rows = [
        row
        for row in inbox_store.snapshot_rows()
        if row.binding_id == whieda_binding.binding_id and row.telegram_update_id == 8001
    ]
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_same_update_id_whieda_and_nsp_are_isolated(inbox_store, whieda_binding, nsp_binding):
    payload = compact_telegram_payload(_update(8002))
    await inbox_store.enqueue(
        binding_id=whieda_binding.binding_id,
        tenant_id=whieda_binding.tenant.tenant_id,
        telegram_update_id=8002,
        payload=payload,
    )
    await inbox_store.enqueue(
        binding_id=nsp_binding.binding_id,
        tenant_id=nsp_binding.tenant.tenant_id,
        telegram_update_id=8002,
        payload=payload,
    )
    rows = [row for row in inbox_store.snapshot_rows() if row.telegram_update_id == 8002]
    assert len(rows) == 2
    tenants = {row.tenant_id for row in rows}
    bindings = {row.binding_id for row in rows}
    assert tenants == {"whieda", "nsp-maxim"}
    assert bindings == {whieda_binding.binding_id, nsp_binding.binding_id}


@pytest.mark.asyncio
async def test_parallel_claim_has_single_owner(inbox_store, whieda_binding):
    await inbox_store.enqueue(
        binding_id=whieda_binding.binding_id,
        tenant_id=whieda_binding.tenant.tenant_id,
        telegram_update_id=8003,
        payload=compact_telegram_payload(_update(8003)),
    )
    first, second = await asyncio.gather(
        inbox_store.claim_next("worker-a"),
        inbox_store.claim_next("worker-b"),
    )
    claimed = [row for row in (first, second) if row is not None]
    assert len(claimed) == 1
    assert claimed[0].lease_owner in {"worker-a", "worker-b"}


@pytest.mark.asyncio
async def test_crash_expired_lease_retries_from_store_state(inbox_store, whieda_binding):
    enqueued = await inbox_store.enqueue(
        binding_id=whieda_binding.binding_id,
        tenant_id=whieda_binding.tenant.tenant_id,
        telegram_update_id=8004,
        payload=compact_telegram_payload(_update(8004)),
    )
    crashed = await inbox_store.claim_by_id(enqueued.inbox_id, "worker-crash")
    assert crashed is not None
    assert crashed.state == "leased"
    inbox_store.expire_lease(enqueued.inbox_id, now=utcnow())
    persisted = inbox_store.inbox[enqueued.inbox_id]
    assert persisted.state == "leased"
    assert persisted.lease_until < utcnow()

    sends: list[str] = []

    async def process_update(binding, _update_body, _trace):
        sends.append(binding.bot_token)

    await dispatch_inbox_work(
        preferred_inbox_id=enqueued.inbox_id,
        webhook_binding=whieda_binding,
        trace_id="restart",
        store=inbox_store,
        process_update=process_update,
        owner="worker-retry",
    )
    assert sends == ["whieda-token"]
    assert inbox_store.inbox[enqueued.inbox_id].state == "processed"


@pytest.mark.asyncio
async def test_processed_update_does_not_send_again(inbox_store, whieda_binding):
    enqueued = await inbox_store.enqueue(
        binding_id=whieda_binding.binding_id,
        tenant_id=whieda_binding.tenant.tenant_id,
        telegram_update_id=8005,
        payload=compact_telegram_payload(_update(8005)),
    )
    sends: list[str] = []

    async def process_update(binding, _update_body, _trace):
        sends.append(binding.bot_token)

    await dispatch_inbox_work(
        preferred_inbox_id=enqueued.inbox_id,
        webhook_binding=whieda_binding,
        trace_id="once",
        store=inbox_store,
        process_update=process_update,
        owner="worker-1",
    )
    await dispatch_inbox_work(
        preferred_inbox_id=enqueued.inbox_id,
        webhook_binding=whieda_binding,
        trace_id="twice",
        store=inbox_store,
        process_update=process_update,
        owner="worker-2",
    )
    assert sends == ["whieda-token"]
    assert inbox_store.inbox[enqueued.inbox_id].state == "processed"
    assert inbox_store.snapshot_deliveries() == []


@pytest.mark.asyncio
async def test_telegram_delivery_error_retries_without_foreign_token(
    inbox_store, nsp_binding, whieda_binding
):
    enqueued = await inbox_store.enqueue(
        binding_id=nsp_binding.binding_id,
        tenant_id=nsp_binding.tenant.tenant_id,
        telegram_update_id=8006,
        payload=compact_telegram_payload(_update(8006)),
    )
    tokens: list[str] = []

    async def process_update(binding, _update_body, _trace):
        tokens.append(binding.bot_token)
        assert binding.bot_token == "nsp-token"
        assert binding.bot_token != whieda_binding.bot_token
        raise TelegramDeliveryError("telegram_send_failed:502")

    claimed = await inbox_store.claim_by_id(enqueued.inbox_id, "worker-d")
    assert claimed is not None
    await handle_claimed_inbox(
        claimed,
        webhook_binding=nsp_binding,
        trace_id="delivery-fail",
        store=inbox_store,
        process_update=process_update,
    )
    row = inbox_store.inbox[enqueued.inbox_id]
    assert row.state == "pending"
    assert row.retry_count == 1
    assert tokens == ["nsp-token"]
    assert "whieda-token" not in tokens
    assert inbox_store.snapshot_deliveries() == []


@pytest.mark.asyncio
async def test_worker_send_uses_binding_token_not_handler_token(
    inbox_store, nsp_binding, monkeypatch
):
    enqueued = await inbox_store.enqueue(
        binding_id=nsp_binding.binding_id,
        tenant_id=nsp_binding.tenant.tenant_id,
        telegram_update_id=8007,
        payload=compact_telegram_payload(_update(8007)),
    )
    claimed = await inbox_store.claim_by_id(enqueued.inbox_id, "worker-e")
    posts: list[str] = []

    async def fake_post(url, payload, timeout_sec):
        posts.append(url)
        return {"ok": True, "result": {"message_id": 1}}, 200

    async def process_update(_binding, _update_body, _trace):
        await send_telegram_text(chat_id="100", text="hi", bot_token="whieda-token")

    monkeypatch.setattr("app.telegram.delivery._post_telegram", fake_post)
    await handle_claimed_inbox(
        claimed,
        webhook_binding=nsp_binding,
        trace_id="foreign",
        store=inbox_store,
        process_update=process_update,
    )
    row = inbox_store.inbox[enqueued.inbox_id]
    assert row.state == "processed"
    assert row.last_error is None
    assert posts and "nsp-token" in posts[0]
    assert all("whieda-token" not in url for url in posts)
    deliveries = inbox_store.snapshot_deliveries()
    assert len(deliveries) == 1
    assert deliveries[0].status == "sent"


@pytest.mark.asyncio
async def test_webhook_duplicate_does_not_create_second_row(
    client, inbox_store, nsp_binding, monkeypatch
):
    monkeypatch.setattr(
        "app.telegram.routes.resolve_bot_binding_context",
        AsyncMock(return_value=nsp_binding),
    )
    process = AsyncMock()
    monkeypatch.setattr("app.telegram.routes._process_telegram_update", process)

    headers = {"x-telegram-bot-api-secret-token": "nsp-secret"}
    first = await client.post("/v1/telegram/nsp-leader-bot/webhook", json=_update(8010), headers=headers)
    second = await client.post("/v1/telegram/nsp-leader-bot/webhook", json=_update(8010), headers=headers)
    assert first.status_code == 200
    assert second.status_code == 200
    rows = [row for row in inbox_store.snapshot_rows() if row.telegram_update_id == 8010]
    assert len(rows) == 1
    assert process.await_count >= 1


@pytest.mark.asyncio
async def test_disabled_unknown_bad_secret_are_not_stored(
    client, inbox_store, nsp_binding, monkeypatch
):
    process = AsyncMock()
    monkeypatch.setattr("app.telegram.routes._process_telegram_update", process)

    monkeypatch.setattr(
        "app.telegram.routes.resolve_bot_binding_context",
        AsyncMock(return_value=None),
    )
    unknown = await client.post("/v1/telegram/unknown/webhook", json=_update(8011))
    disabled = await client.post("/v1/telegram/disabled/webhook", json=_update(8011))
    assert unknown.status_code == 200
    assert disabled.status_code == 200

    monkeypatch.setattr(
        "app.telegram.routes.resolve_bot_binding_context",
        AsyncMock(return_value=nsp_binding),
    )
    forbidden = await client.post(
        "/v1/telegram/nsp-leader-bot/webhook",
        json=_update(8011),
        headers={"x-telegram-bot-api-secret-token": "wrong"},
    )
    assert forbidden.status_code == 403
    assert inbox_store.snapshot_rows() == []
    process.assert_not_awaited()


@pytest.mark.asyncio
async def test_inbox_unavailable_returns_503_without_row(
    client, inbox_store, nsp_binding, monkeypatch
):
    monkeypatch.setattr(
        "app.telegram.routes.resolve_bot_binding_context",
        AsyncMock(return_value=nsp_binding),
    )
    monkeypatch.setattr(
        "app.telegram.routes.get_inbox_store",
        lambda: type(
            "Boom",
            (),
            {"enqueue": AsyncMock(side_effect=RuntimeError("db down"))},
        )(),
    )
    response = await client.post(
        "/v1/telegram/nsp-leader-bot/webhook",
        json=_update(8012),
        headers={"x-telegram-bot-api-secret-token": "nsp-secret"},
    )
    assert response.status_code == 503
    assert response.json()["error"] == "telegram_inbox_unavailable"
    assert inbox_store.snapshot_rows() == []


def test_get_inbox_store_uses_test_override(inbox_store):
    assert get_inbox_store() is inbox_store
