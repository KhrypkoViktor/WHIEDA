"""Core Gate K: durable Telegram delivery outbox."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from app.db_feature_readiness import (
    FEATURE_REQUIREMENTS,
    SQL_DIR,
    evaluate_from_relations,
    get_feature_status,
    reset_feature_readiness_for_tests,
    set_table_probe_for_tests,
)
from app.telegram.delivery import (
    TelegramDeliveryError,
    TelegramDeliveryUnknown,
    capture_delivery_plan,
    deliver_structured_advisor_response,
)
from app.telegram.inbox import (
    DeliveryOutboxRecord,
    InMemoryInboxStore,
    OUTBOX_MAX_ATTEMPTS,
    compact_telegram_payload,
    utcnow,
)
from app.telegram.outbox_inspect import inspect_snapshot
from app.telegram.worker import drain_binding_outbox, handle_claimed_inbox

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "backend" / "platform-api" / "scripts" / "inspect_telegram_delivery_outbox.py"
QA = ROOT / "qa" / "telegram_delivery_outbox"
SCRIPTS = ROOT / "postgres" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from staging_proof_lib import APPLY_ORDER  # noqa: E402

FORBIDDEN = (
    "postgresql://",
    "AAHsecret",
    "+375111111111",
    "supabase",
    "185.252.232.93",
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _reset_readiness():
    reset_feature_readiness_for_tests()
    yield
    reset_feature_readiness_for_tests()


@pytest.fixture
def store():
    return InMemoryInboxStore()


def _binding(whieda_tenant, *, binding_id: str, token: str, tenant_id: str | None = None):
    from app.telegram.bindings import BotBindingContext

    tenant = type(whieda_tenant)(
        tenant_id=tenant_id or "tenant-north",
        status="active",
        display_name="North",
        entitlements={},
    )
    return BotBindingContext(
        binding_id=binding_id,
        tenant=tenant,
        bot_token_ref=f"env:{binding_id.upper()}_BOT_TOKEN",
        webhook_secret_ref=f"env:{binding_id.upper()}_WEBHOOK_SECRET",
        bot_username=f"{binding_id}_bot",
        status="active",
        processing_mode="core",
        bot_token=token,
        webhook_secret=f"{binding_id}-secret",
    )


def _update(update_id: int = 9101, text: str = "цена") -> dict:
    return {
        "update_id": update_id,
        "message": {
            "message_id": 11,
            "text": text,
            "chat": {"id": 100, "type": "private"},
            "from": {"id": 200},
        },
    }


async def _enqueue(store, binding, update_id: int = 9101, text: str = "цена"):
    return await store.enqueue(
        binding_id=binding.binding_id,
        tenant_id=binding.tenant.tenant_id,
        telegram_update_id=update_id,
        payload=compact_telegram_payload(_update(update_id, text)),
    )


def _assert_redacted(blob: str) -> None:
    lowered = blob.lower()
    for item in FORBIDDEN:
        assert item.lower() not in lowered


@pytest.fixture
def telegram_posts(monkeypatch):
    calls: list[dict] = []

    async def fake_post(url, payload, timeout_sec):
        calls.append({"url": url, "payload": payload})
        if payload.get("_fail"):
            return {"ok": False}, 502
        return {"ok": True, "result": {"message_id": 1}}, 200

    monkeypatch.setattr("app.telegram.delivery._post_telegram", fake_post)
    return calls


@pytest.mark.asyncio
async def test_one_update_creates_one_text_outbox_row(store, whieda_tenant, telegram_posts):
    binding = _binding(whieda_tenant, binding_id="north-advisor-bot", token="north-token")
    enqueued = await _enqueue(store, binding)
    claimed = await store.claim_by_id(enqueued.inbox_id, "w1")

    async def process_update(_binding, _update_body, _trace):
        from app.telegram.delivery import send_telegram_text

        await send_telegram_text(chat_id="100", text="Цена 10 USD", bot_token="ignored-token")

    await handle_claimed_inbox(
        claimed, webhook_binding=binding, trace_id="t1", store=store, process_update=process_update
    )
    rows = store.snapshot_deliveries()
    assert len(rows) == 1
    assert rows[0].kind == "text"
    assert rows[0].sequence_no == 1
    assert rows[0].binding_id == "north-advisor-bot"
    assert rows[0].status == "sent"
    assert store.inbox[enqueued.inbox_id].state == "processed"
    assert telegram_posts and "north-token" in telegram_posts[0]["url"]
    assert "ignored-token" not in telegram_posts[0]["url"]
    assert "bot_token" not in str(rows[0].payload)


@pytest.mark.asyncio
async def test_photo_response_queues_photo_then_text(store, whieda_tenant, telegram_posts, monkeypatch):
    monkeypatch.setattr(
        "app.telegram.tenant_media.get_settings",
        lambda: type("S", (), {"platform_tenant_media_base_url": "https://media.example.org/media"})(),
    )
    binding = _binding(whieda_tenant, binding_id="north-advisor-bot", token="north-token")
    enqueued = await _enqueue(store, binding)
    claimed = await store.claim_by_id(enqueued.inbox_id, "w1")

    async def process_update(_binding, _update_body, _trace):
        await deliver_structured_advisor_response(
            100,
            {
                "answer_text": "Карточка SHARE-01",
                "answer_mode": "structured_photo",
                "product": {"sku": "SHARE-01"},
                "media": {"filename": "1.webp", "sku": "SHARE-01"},
            },
            bot_token="ignored-token",
            tenant_id="tenant-north",
            binding_status="active",
        )

    await handle_claimed_inbox(
        claimed, webhook_binding=binding, trace_id="photo", store=store, process_update=process_update
    )
    rows = sorted(store.snapshot_deliveries(), key=lambda row: row.sequence_no)
    assert [row.kind for row in rows] == ["photo", "text"]
    assert rows[0].binding_id == rows[1].binding_id == "north-advisor-bot"
    assert [row.status for row in rows] == ["sent", "sent"]
    assert telegram_posts[0]["url"].endswith("/sendPhoto")
    assert telegram_posts[1]["url"].endswith("/sendMessage")
    assert all("north-token" in item["url"] for item in telegram_posts)


@pytest.mark.asyncio
async def test_duplicate_update_does_not_duplicate_plan(store, whieda_tenant, telegram_posts):
    binding = _binding(whieda_tenant, binding_id="north-advisor-bot", token="north-token")
    first = await _enqueue(store, binding, 9201)
    second = await _enqueue(store, binding, 9201)
    assert first.inbox_id == second.inbox_id
    assert second.inserted is False

    async def process_update(_binding, _update_body, _trace):
        from app.telegram.delivery import send_telegram_text

        await send_telegram_text(chat_id="100", text="один план", bot_token="north-token")

    claimed = await store.claim_by_id(first.inbox_id, "w1")
    await handle_claimed_inbox(
        claimed, webhook_binding=binding, trace_id="dup1", store=store, process_update=process_update
    )
    claimed_again = await store.claim_by_id(first.inbox_id, "w2")
    assert claimed_again is None
    rows = store.snapshot_deliveries()
    assert len(rows) == 1
    assert len(telegram_posts) == 1


@pytest.mark.asyncio
async def test_crash_before_send_recovers_pending_item(store, whieda_tenant, telegram_posts):
    binding = _binding(whieda_tenant, binding_id="north-advisor-bot", token="north-token")
    enqueued = await _enqueue(store, binding, 9301)
    with capture_delivery_plan() as drafts:
        from app.telegram.delivery import send_telegram_text

        await send_telegram_text(chat_id="100", text="после рестарта", bot_token="north-token")
    await store.enqueue_delivery_plan(
        inbox_id=enqueued.inbox_id,
        binding_id=binding.binding_id,
        tenant_id=binding.tenant.tenant_id,
        telegram_update_id=9301,
        items=list(drafts),
    )
    await store.mark_processed(enqueued.inbox_id)
    pending = store.snapshot_deliveries()
    assert pending[0].status == "pending"
    assert telegram_posts == []
    sent = await drain_binding_outbox(binding, trace_id="recover", store=store)
    assert sent == 1
    assert store.snapshot_deliveries()[0].status == "sent"
    assert telegram_posts and "north-token" in telegram_posts[0]["url"]


@pytest.mark.asyncio
async def test_confirmed_failure_retries_then_dead(store, whieda_tenant, monkeypatch):
    monkeypatch.setattr("app.telegram.inbox.OUTBOX_MAX_ATTEMPTS", 2)
    binding = _binding(whieda_tenant, binding_id="north-advisor-bot", token="north-token")
    enqueued = await _enqueue(store, binding, 9401)
    await store.enqueue_delivery_plan(
        inbox_id=enqueued.inbox_id,
        binding_id=binding.binding_id,
        tenant_id=binding.tenant.tenant_id,
        telegram_update_id=9401,
        items=[{"kind": "text", "payload": {"chat_id": "100", "text": "retry"}}],
    )
    await store.mark_processed(enqueued.inbox_id)

    async def fail_post(url, payload, timeout_sec):
        raise TelegramDeliveryError("telegram_send_failed:502")

    monkeypatch.setattr("app.telegram.delivery._post_telegram", fail_post)
    await drain_binding_outbox(binding, trace_id="fail1", store=store)
    row = store.snapshot_deliveries()[0]
    assert row.status == "retryable_failed"
    store.deliveries[row.outbox_id] = DeliveryOutboxRecord(
        outbox_id=row.outbox_id,
        inbox_id=row.inbox_id,
        binding_id=row.binding_id,
        tenant_id=row.tenant_id,
        telegram_update_id=row.telegram_update_id,
        sequence_no=row.sequence_no,
        kind=row.kind,
        payload=row.payload,
        status=row.status,
        idempotency_key=row.idempotency_key,
        lease_until=utcnow(),
        attempt_count=row.attempt_count,
        last_error_code=row.last_error_code,
        created_at=row.created_at,
    )
    await drain_binding_outbox(binding, trace_id="fail2", store=store)
    dead = store.snapshot_deliveries()[0]
    assert dead.status == "dead"
    assert dead.attempt_count >= 2
    assert dead.last_error_code


@pytest.mark.asyncio
async def test_ambiguous_delivery_is_not_resent(store, whieda_tenant, monkeypatch):
    binding = _binding(whieda_tenant, binding_id="north-advisor-bot", token="north-token")
    enqueued = await _enqueue(store, binding, 9501)
    await store.enqueue_delivery_plan(
        inbox_id=enqueued.inbox_id,
        binding_id=binding.binding_id,
        tenant_id=binding.tenant.tenant_id,
        telegram_update_id=9501,
        items=[{"kind": "text", "payload": {"chat_id": "100", "text": "maybe"}}],
    )

    async def timeout_post(url, payload, timeout_sec):
        raise TelegramDeliveryUnknown("telegram_send_ambiguous")

    monkeypatch.setattr("app.telegram.delivery._post_telegram", timeout_post)
    await drain_binding_outbox(binding, trace_id="unk", store=store)
    row = store.snapshot_deliveries()[0]
    assert row.status == "unknown_delivery"
    posts: list[str] = []

    async def ok_post(url, payload, timeout_sec):
        posts.append(url)
        return {"ok": True, "result": {"message_id": 1}}, 200

    monkeypatch.setattr("app.telegram.delivery._post_telegram", ok_post)
    await drain_binding_outbox(binding, trace_id="unk2", store=store)
    assert store.snapshot_deliveries()[0].status == "unknown_delivery"
    assert posts == []


@pytest.mark.asyncio
async def test_text_waits_for_photo_until_sent_or_dead(store, whieda_tenant, telegram_posts):
    binding = _binding(whieda_tenant, binding_id="north-advisor-bot", token="north-token")
    enqueued = await _enqueue(store, binding, 9601)
    await store.enqueue_delivery_plan(
        inbox_id=enqueued.inbox_id,
        binding_id=binding.binding_id,
        tenant_id=binding.tenant.tenant_id,
        telegram_update_id=9601,
        items=[
            {"kind": "photo", "payload": {"chat_id": "100", "photo_url": "https://media.example.org/p.webp"}},
            {"kind": "text", "payload": {"chat_id": "100", "text": "описание"}},
        ],
    )
    first = await store.claim_delivery("w", binding_id=binding.binding_id)
    second = await store.claim_delivery("w", binding_id=binding.binding_id)
    assert first is not None and first.kind == "photo"
    assert second is None
    await store.mark_delivery_sent(first.outbox_id)
    text_row = await store.claim_delivery("w", binding_id=binding.binding_id)
    assert text_row is not None and text_row.kind == "text"

    later = await _enqueue(store, binding, 9602)
    await store.enqueue_delivery_plan(
        inbox_id=later.inbox_id,
        binding_id=binding.binding_id,
        tenant_id=binding.tenant.tenant_id,
        telegram_update_id=9602,
        items=[
            {"kind": "photo", "payload": {"chat_id": "100", "photo_url": "https://media.example.org/p.webp"}},
            {"kind": "text", "payload": {"chat_id": "100", "text": "после dead"}},
        ],
    )
    photo = next(row for row in store.snapshot_deliveries() if row.telegram_update_id == 9602 and row.kind == "photo")
    await store.mark_delivery_retryable(photo.outbox_id, "x")
    # force dead
    for _ in range(OUTBOX_MAX_ATTEMPTS):
        current = store.deliveries[photo.outbox_id]
        store.deliveries[photo.outbox_id] = DeliveryOutboxRecord(
            **{**current.__dict__, "lease_until": utcnow()}
        )
        status = await store.mark_delivery_retryable(photo.outbox_id, "x")
        if status == "dead":
            break
    skipped = await store.claim_delivery("w", binding_id=binding.binding_id)
    assert skipped is not None
    assert skipped.kind == "text"
    assert skipped.telegram_update_id == 9602


@pytest.mark.asyncio
async def test_no_cross_binding_token_or_media(store, whieda_tenant, telegram_posts):
    north = _binding(whieda_tenant, binding_id="north-advisor-bot", token="north-token", tenant_id="tenant-north")
    south = _binding(whieda_tenant, binding_id="south-advisor-bot", token="south-token", tenant_id="tenant-south")
    n_enq = await _enqueue(store, north, 9701)
    s_enq = await _enqueue(store, south, 9701)
    await store.enqueue_delivery_plan(
        inbox_id=n_enq.inbox_id,
        binding_id=north.binding_id,
        tenant_id=north.tenant.tenant_id,
        telegram_update_id=9701,
        items=[{"kind": "text", "payload": {"chat_id": "100", "text": "north"}}],
    )
    await store.enqueue_delivery_plan(
        inbox_id=s_enq.inbox_id,
        binding_id=south.binding_id,
        tenant_id=south.tenant.tenant_id,
        telegram_update_id=9701,
        items=[{"kind": "text", "payload": {"chat_id": "200", "text": "south"}}],
    )
    await drain_binding_outbox(north, trace_id="n", store=store)
    assert all("north-token" in item["url"] for item in telegram_posts)
    assert all("south-token" not in item["url"] for item in telegram_posts)
    south_row = next(row for row in store.snapshot_deliveries() if row.binding_id == "south-advisor-bot")
    assert south_row.status == "pending"
    assert south_row.payload["chat_id"] == "200"


def test_missing_outbox_migration_is_degraded_not_ready():
    relations = json.loads(
        (ROOT / "qa" / "schema_feature_readiness" / "manifests" / "ready.json").read_text(encoding="utf-8")
    )["relations"]
    without_outbox = [name for name in relations if name != "telegram_delivery_outbox"]
    report = evaluate_from_relations(without_outbox)
    outbox = next(item for item in report.features if item.feature == "telegram_durable_outbox")
    assert outbox.state == "degraded"
    assert "telegram_delivery_outbox" in outbox.missing_tables
    assert report.ok is False


@pytest.mark.asyncio
async def test_missing_outbox_columns_are_unavailable(monkeypatch):
    async def probe(tables):
        if "telegram_delivery_outbox" in tables:
            return ("telegram_delivery_outbox.sequence_no",)
        return ()

    set_table_probe_for_tests(probe)
    status = await get_feature_status("telegram_durable_outbox")
    assert status.ready is False
    assert status.state == "degraded"
    assert "sequence_no" in status.missing_tables[0]


def test_registry_outbox_migration_is_in_apply_order():
    spec = FEATURE_REQUIREMENTS["telegram_durable_outbox"]
    path = SQL_DIR / spec["migration"]
    assert path.is_file()
    assert spec["migration"] in APPLY_ORDER
    text = path.read_text(encoding="utf-8")
    for column in spec["columns"]:
        assert column in text
    assert APPLY_ORDER[-1] == spec["migration"]
    assert APPLY_ORDER.index("platform_telegram_durable_inbox_v1.sql") < APPLY_ORDER.index(spec["migration"])


def test_inspect_cli_redacts_and_refuses_retry(tmp_path: Path):
    ready = _run("--offline-snapshot", str(QA / "snapshots" / "ready.json"))
    assert ready.returncode == 0
    payload = json.loads(ready.stdout)
    assert payload["counts"]["sent"] == 1
    blob = ready.stdout
    assert "chat_id" not in blob
    _assert_redacted(blob)

    unknown = inspect_snapshot(
        json.loads((QA / "snapshots" / "unknown-delivery.json").read_text(encoding="utf-8"))["rows"]
    )
    assert unknown["counts"]["unknown_delivery"] == 1
    assert unknown["unknown_delivery"][0]["payload"]["chat_ref"].startswith("chat:")
    _assert_redacted(json.dumps(unknown))

    dead = inspect_snapshot(json.loads((QA / "snapshots" / "dead.json").read_text(encoding="utf-8"))["rows"])
    assert dead["dead"][0]["status"] == "dead"
    _assert_redacted(json.dumps(dead))

    refused = _run("--offline-snapshot", str(QA / "snapshots" / "ready.json"), "--retry-all")
    assert refused.returncode == 1
    assert json.loads(refused.stdout)["code"] == "action_refused"


@pytest.mark.asyncio
async def test_timeout_classified_unknown(monkeypatch):
    class BoomClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json=None):
            raise httpx.TimeoutException("timed out")

    monkeypatch.setattr("app.telegram.delivery.httpx.AsyncClient", BoomClient)
    from app.telegram.delivery import outbound_binding_guard, send_telegram_text

    with outbound_binding_guard("tok"):
        with pytest.raises(TelegramDeliveryUnknown):
            await send_telegram_text(chat_id="1", text="hi", bot_token="tok")
