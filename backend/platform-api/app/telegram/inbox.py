"""Durable Telegram inbox/outbox store (shared across Core processes)."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from app.telegram.log_safe import redact_telegram_secrets

LEASE_SECONDS = 30
MAX_RETRIES = 8
OUTBOX_MAX_ATTEMPTS = 8
OUTBOX_LEASE_SECONDS = 30
BUSINESS_REPLY_KEY_SUFFIX = "business_reply"
OUTBOX_BLOCKING_STATUSES = frozenset(
    {"pending", "leased", "retryable_failed", "unknown_delivery"}
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def compact_telegram_payload(update: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(update, dict):
        return None
    raw_update_id = update.get("update_id")
    try:
        update_id = int(raw_update_id)
    except (TypeError, ValueError):
        return None
    payload: dict[str, Any] = {"update_id": update_id}
    message = update.get("message")
    if isinstance(message, dict):
        chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
        sender = message.get("from") if isinstance(message.get("from"), dict) else {}
        payload["message"] = {
            "message_id": message.get("message_id"),
            "text": message.get("text"),
            "chat": {"id": chat.get("id"), "type": chat.get("type")},
            "from": {"id": sender.get("id")},
        }
        # Фото и файл без подписи — шаг «фото» заявки и чек об оплате.
        if message.get("caption"):
            payload["message"]["caption"] = message.get("caption")
        photos = message.get("photo")
        if isinstance(photos, list) and photos and isinstance(photos[-1], dict):
            payload["message"]["photo"] = [{"file_id": photos[-1].get("file_id")}]
        document = message.get("document")
        if isinstance(document, dict) and document.get("file_id"):
            payload["message"]["document"] = {"file_id": document.get("file_id")}
    callback = update.get("callback_query")
    if isinstance(callback, dict):
        cb_message = callback.get("message") if isinstance(callback.get("message"), dict) else {}
        cb_chat = cb_message.get("chat") if isinstance(cb_message.get("chat"), dict) else {}
        cb_sender = callback.get("from") if isinstance(callback.get("from"), dict) else {}
        payload["callback_query"] = {
            "id": callback.get("id"),
            "data": callback.get("data"),
            "from": {"id": cb_sender.get("id")},
            "message": {
                "message_id": cb_message.get("message_id"),
                "chat": {"id": cb_chat.get("id"), "type": cb_chat.get("type")},
            },
        }
    return payload


def business_idempotency_key(inbox_id: str) -> str:
    return f"{inbox_id}:{BUSINESS_REPLY_KEY_SUFFIX}"


def safe_error_summary(exc: BaseException | str) -> str:
    text = redact_telegram_secrets(str(exc))
    return text[:300]


@dataclass(frozen=True)
class InboxRecord:
    inbox_id: str
    binding_id: str
    tenant_id: str
    telegram_update_id: int
    payload: dict[str, Any]
    state: str
    received_at: datetime
    lease_owner: str | None = None
    lease_until: datetime | None = None
    processed_at: datetime | None = None
    retry_count: int = 0
    last_error: str | None = None


@dataclass(frozen=True)
class EnqueueResult:
    inbox_id: str
    inserted: bool


@dataclass(frozen=True)
class OutboxRecord:
    outbox_id: str
    inbox_id: str
    binding_id: str
    tenant_id: str
    idempotency_key: str
    state: str


@dataclass(frozen=True)
class DeliveryOutboxRecord:
    outbox_id: str
    inbox_id: str
    binding_id: str
    tenant_id: str
    telegram_update_id: int
    sequence_no: int
    kind: str
    payload: dict[str, Any]
    status: str
    idempotency_key: str
    lease_until: datetime | None = None
    attempt_count: int = 0
    last_error_code: str | None = None
    created_at: datetime | None = None
    sent_at: datetime | None = None

    @property
    def delivery_id(self) -> str:
        return self.outbox_id


class InboxStore(Protocol):
    async def enqueue(
        self,
        *,
        binding_id: str,
        tenant_id: str,
        telegram_update_id: int,
        payload: dict[str, Any],
    ) -> EnqueueResult: ...

    async def claim_next(self, owner: str, *, now: datetime | None = None) -> InboxRecord | None: ...

    async def claim_by_id(
        self, inbox_id: str, owner: str, *, now: datetime | None = None
    ) -> InboxRecord | None: ...

    async def mark_processed(self, inbox_id: str) -> None: ...

    async def release_for_retry(self, inbox_id: str, error: str) -> None: ...

    async def reserve_outbox(
        self,
        *,
        inbox_id: str,
        binding_id: str,
        tenant_id: str,
        idempotency_key: str,
    ) -> OutboxRecord: ...

    async def mark_outbox_sent(self, outbox_id: str) -> None: ...

    async def enqueue_delivery_plan(
        self,
        *,
        inbox_id: str,
        binding_id: str,
        tenant_id: str,
        telegram_update_id: int,
        items: list[Any],
    ) -> list[DeliveryOutboxRecord]: ...

    async def claim_delivery(
        self,
        owner: str,
        *,
        binding_id: str | None = None,
        now: datetime | None = None,
    ) -> DeliveryOutboxRecord | None: ...

    async def mark_delivery_sent(self, outbox_id: str) -> None: ...

    async def mark_delivery_retryable(self, outbox_id: str, error_code: str) -> str: ...

    async def mark_delivery_unknown(self, outbox_id: str, error_code: str) -> None: ...

    def snapshot_deliveries(self) -> list[DeliveryOutboxRecord]: ...

    def snapshot_rows(self) -> list[InboxRecord]: ...


def _claimable(row: InboxRecord, now: datetime) -> bool:
    if row.state == "pending":
        return True
    if row.state == "leased" and row.lease_until is not None and row.lease_until < now:
        return True
    return False


class InMemoryInboxStore:
    """Row-state store used by tests to model DB, including restart after crash."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.inbox: dict[str, InboxRecord] = {}
        self.outbox: dict[str, OutboxRecord] = {}
        self.deliveries: dict[str, DeliveryOutboxRecord] = {}
        self._by_binding_update: dict[tuple[str, int], str] = {}
        self._delivery_keys: dict[tuple[str, int, int], str] = {}

    def snapshot_rows(self) -> list[InboxRecord]:
        return list(self.inbox.values())

    async def enqueue(
        self,
        *,
        binding_id: str,
        tenant_id: str,
        telegram_update_id: int,
        payload: dict[str, Any],
    ) -> EnqueueResult:
        async with self._lock:
            key = (binding_id, int(telegram_update_id))
            existing_id = self._by_binding_update.get(key)
            if existing_id:
                return EnqueueResult(inbox_id=existing_id, inserted=False)
            inbox_id = str(uuid.uuid4())
            record = InboxRecord(
                inbox_id=inbox_id,
                binding_id=binding_id,
                tenant_id=tenant_id,
                telegram_update_id=int(telegram_update_id),
                payload=dict(payload),
                state="pending",
                received_at=utcnow(),
            )
            self.inbox[inbox_id] = record
            self._by_binding_update[key] = inbox_id
            return EnqueueResult(inbox_id=inbox_id, inserted=True)

    async def claim_next(self, owner: str, *, now: datetime | None = None) -> InboxRecord | None:
        async with self._lock:
            return self._claim_locked(owner, now=now or utcnow())

    async def claim_by_id(
        self, inbox_id: str, owner: str, *, now: datetime | None = None
    ) -> InboxRecord | None:
        async with self._lock:
            return self._claim_locked(owner, now=now or utcnow(), inbox_id=inbox_id)

    def _claim_locked(
        self, owner: str, *, now: datetime, inbox_id: str | None = None
    ) -> InboxRecord | None:
        candidates = [
            row
            for row in self.inbox.values()
            if _claimable(row, now) and (inbox_id is None or row.inbox_id == inbox_id)
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda row: row.received_at)
        current = candidates[0]
        leased = InboxRecord(
            inbox_id=current.inbox_id,
            binding_id=current.binding_id,
            tenant_id=current.tenant_id,
            telegram_update_id=current.telegram_update_id,
            payload=current.payload,
            state="leased",
            received_at=current.received_at,
            lease_owner=owner,
            lease_until=now + timedelta(seconds=LEASE_SECONDS),
            processed_at=current.processed_at,
            retry_count=current.retry_count,
            last_error=current.last_error,
        )
        self.inbox[current.inbox_id] = leased
        return leased

    async def mark_processed(self, inbox_id: str) -> None:
        async with self._lock:
            current = self.inbox[inbox_id]
            self.inbox[inbox_id] = InboxRecord(
                inbox_id=current.inbox_id,
                binding_id=current.binding_id,
                tenant_id=current.tenant_id,
                telegram_update_id=current.telegram_update_id,
                payload=current.payload,
                state="processed",
                received_at=current.received_at,
                lease_owner=None,
                lease_until=None,
                processed_at=utcnow(),
                retry_count=current.retry_count,
                last_error=None,
            )

    async def release_for_retry(self, inbox_id: str, error: str) -> None:
        async with self._lock:
            current = self.inbox[inbox_id]
            retry_count = current.retry_count + 1
            self.inbox[inbox_id] = InboxRecord(
                inbox_id=current.inbox_id,
                binding_id=current.binding_id,
                tenant_id=current.tenant_id,
                telegram_update_id=current.telegram_update_id,
                payload=current.payload,
                state="failed" if retry_count >= MAX_RETRIES else "pending",
                received_at=current.received_at,
                lease_owner=None,
                lease_until=None,
                processed_at=current.processed_at,
                retry_count=retry_count,
                last_error=safe_error_summary(error),
            )

    def expire_lease(self, inbox_id: str, *, now: datetime | None = None) -> None:
        """Test/helper: persist expired lease in store state (restart/crash)."""
        current = self.inbox[inbox_id]
        moment = now or utcnow()
        self.inbox[inbox_id] = InboxRecord(
            inbox_id=current.inbox_id,
            binding_id=current.binding_id,
            tenant_id=current.tenant_id,
            telegram_update_id=current.telegram_update_id,
            payload=current.payload,
            state="leased",
            received_at=current.received_at,
            lease_owner=current.lease_owner,
            lease_until=moment - timedelta(seconds=1),
            processed_at=current.processed_at,
            retry_count=current.retry_count,
            last_error=current.last_error,
        )

    async def reserve_outbox(
        self,
        *,
        inbox_id: str,
        binding_id: str,
        tenant_id: str,
        idempotency_key: str,
    ) -> OutboxRecord:
        async with self._lock:
            for item in self.outbox.values():
                if item.inbox_id == inbox_id and item.idempotency_key == idempotency_key:
                    return item
            record = OutboxRecord(
                outbox_id=str(uuid.uuid4()),
                inbox_id=inbox_id,
                binding_id=binding_id,
                tenant_id=tenant_id,
                idempotency_key=idempotency_key,
                state="pending",
            )
            self.outbox[record.outbox_id] = record
            return record

    async def mark_outbox_sent(self, outbox_id: str) -> None:
        async with self._lock:
            current = self.outbox[outbox_id]
            self.outbox[outbox_id] = OutboxRecord(
                outbox_id=current.outbox_id,
                inbox_id=current.inbox_id,
                binding_id=current.binding_id,
                tenant_id=current.tenant_id,
                idempotency_key=current.idempotency_key,
                state="sent",
            )

    def snapshot_deliveries(self) -> list[DeliveryOutboxRecord]:
        return list(self.deliveries.values())

    async def enqueue_delivery_plan(
        self,
        *,
        inbox_id: str,
        binding_id: str,
        tenant_id: str,
        telegram_update_id: int,
        items: list[Any],
    ) -> list[DeliveryOutboxRecord]:
        from app.telegram.delivery import DeliveryDraft, compact_delivery_payload

        rows: list[DeliveryOutboxRecord] = []
        async with self._lock:
            for index, item in enumerate(items, start=1):
                if isinstance(item, DeliveryDraft):
                    kind = item.kind
                    payload = compact_delivery_payload(kind, item.payload)
                elif isinstance(item, dict):
                    kind = str(item.get("kind") or "text")
                    payload = compact_delivery_payload(kind, item.get("payload") or item)
                else:
                    continue
                key = (binding_id, int(telegram_update_id), index)
                existing_id = self._delivery_keys.get(key)
                if existing_id:
                    rows.append(self.deliveries[existing_id])
                    continue
                outbox_id = str(uuid.uuid4())
                record = DeliveryOutboxRecord(
                    outbox_id=outbox_id,
                    inbox_id=inbox_id,
                    binding_id=binding_id,
                    tenant_id=tenant_id,
                    telegram_update_id=int(telegram_update_id),
                    sequence_no=index,
                    kind=kind,
                    payload=payload,
                    status="pending",
                    idempotency_key=f"{inbox_id}:{index}:{kind}",
                    created_at=utcnow(),
                )
                self.deliveries[outbox_id] = record
                self._delivery_keys[key] = outbox_id
                rows.append(record)
        return rows

    def _delivery_ready_locked(
        self, row: DeliveryOutboxRecord, now: datetime, binding_id: str | None
    ) -> bool:
        if binding_id is not None and row.binding_id != binding_id:
            return False
        if row.status == "pending":
            ready = True
        elif row.status == "retryable_failed" and (
            row.lease_until is None or row.lease_until <= now
        ):
            ready = True
        elif row.status == "leased" and row.lease_until is not None and row.lease_until < now:
            ready = True
        else:
            ready = False
        if not ready:
            return False
        blocked = any(
            other.binding_id == row.binding_id
            and other.telegram_update_id == row.telegram_update_id
            and other.sequence_no < row.sequence_no
            and other.status in OUTBOX_BLOCKING_STATUSES
            for other in self.deliveries.values()
        )
        return not blocked

    async def claim_delivery(
        self,
        owner: str,
        *,
        binding_id: str | None = None,
        now: datetime | None = None,
    ) -> DeliveryOutboxRecord | None:
        moment = now or utcnow()
        async with self._lock:
            candidates = [
                row
                for row in self.deliveries.values()
                if self._delivery_ready_locked(row, moment, binding_id)
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda row: (row.created_at or moment, row.sequence_no))
            current = candidates[0]
            leased = DeliveryOutboxRecord(
                outbox_id=current.outbox_id,
                inbox_id=current.inbox_id,
                binding_id=current.binding_id,
                tenant_id=current.tenant_id,
                telegram_update_id=current.telegram_update_id,
                sequence_no=current.sequence_no,
                kind=current.kind,
                payload=current.payload,
                status="leased",
                idempotency_key=current.idempotency_key,
                lease_until=moment + timedelta(seconds=OUTBOX_LEASE_SECONDS),
                attempt_count=current.attempt_count,
                last_error_code=current.last_error_code,
                created_at=current.created_at,
                sent_at=current.sent_at,
            )
            self.deliveries[current.outbox_id] = leased
            return leased

    def expire_delivery_lease(self, outbox_id: str, *, now: datetime | None = None) -> None:
        current = self.deliveries[outbox_id]
        moment = now or utcnow()
        self.deliveries[outbox_id] = DeliveryOutboxRecord(
            outbox_id=current.outbox_id,
            inbox_id=current.inbox_id,
            binding_id=current.binding_id,
            tenant_id=current.tenant_id,
            telegram_update_id=current.telegram_update_id,
            sequence_no=current.sequence_no,
            kind=current.kind,
            payload=current.payload,
            status="leased",
            idempotency_key=current.idempotency_key,
            lease_until=moment - timedelta(seconds=1),
            attempt_count=current.attempt_count,
            last_error_code=current.last_error_code,
            created_at=current.created_at,
            sent_at=current.sent_at,
        )

    async def mark_delivery_sent(self, outbox_id: str) -> None:
        async with self._lock:
            current = self.deliveries[outbox_id]
            self.deliveries[outbox_id] = DeliveryOutboxRecord(
                outbox_id=current.outbox_id,
                inbox_id=current.inbox_id,
                binding_id=current.binding_id,
                tenant_id=current.tenant_id,
                telegram_update_id=current.telegram_update_id,
                sequence_no=current.sequence_no,
                kind=current.kind,
                payload=current.payload,
                status="sent",
                idempotency_key=current.idempotency_key,
                lease_until=None,
                attempt_count=current.attempt_count,
                last_error_code=None,
                created_at=current.created_at,
                sent_at=utcnow(),
            )

    async def mark_delivery_retryable(self, outbox_id: str, error_code: str) -> str:
        async with self._lock:
            current = self.deliveries[outbox_id]
            attempts = current.attempt_count + 1
            status = "dead" if attempts >= OUTBOX_MAX_ATTEMPTS else "retryable_failed"
            backoff = 2 ** min(attempts, 8)
            self.deliveries[outbox_id] = DeliveryOutboxRecord(
                outbox_id=current.outbox_id,
                inbox_id=current.inbox_id,
                binding_id=current.binding_id,
                tenant_id=current.tenant_id,
                telegram_update_id=current.telegram_update_id,
                sequence_no=current.sequence_no,
                kind=current.kind,
                payload=current.payload,
                status=status,
                idempotency_key=current.idempotency_key,
                lease_until=(utcnow() + timedelta(seconds=backoff)) if status == "retryable_failed" else None,
                attempt_count=attempts,
                last_error_code=safe_error_summary(error_code)[:80],
                created_at=current.created_at,
                sent_at=current.sent_at,
            )
            return status

    async def mark_delivery_unknown(self, outbox_id: str, error_code: str) -> None:
        async with self._lock:
            current = self.deliveries[outbox_id]
            self.deliveries[outbox_id] = DeliveryOutboxRecord(
                outbox_id=current.outbox_id,
                inbox_id=current.inbox_id,
                binding_id=current.binding_id,
                tenant_id=current.tenant_id,
                telegram_update_id=current.telegram_update_id,
                sequence_no=current.sequence_no,
                kind=current.kind,
                payload=current.payload,
                status="unknown_delivery",
                idempotency_key=current.idempotency_key,
                lease_until=None,
                attempt_count=current.attempt_count + 1,
                last_error_code=safe_error_summary(error_code)[:80],
                created_at=current.created_at,
                sent_at=current.sent_at,
            )


class PostgresInboxStore:
    async def enqueue(
        self,
        *,
        binding_id: str,
        tenant_id: str,
        telegram_update_id: int,
        payload: dict[str, Any],
    ) -> EnqueueResult:
        from app.db_feature_readiness import SchemaFeatureUnavailable, get_feature_status

        status = await get_feature_status("telegram_durable_inbox")
        if not status.ready:
            raise SchemaFeatureUnavailable(status)
        row = await _fetch_one(
            """
            select inbox_id::text as inbox_id, inserted
            from telegram_inbox_enqueue(%s, %s, %s, %s::jsonb)
            """,
            (binding_id, tenant_id, int(telegram_update_id), json.dumps(payload)),
        )
        if not row or not row.get("inbox_id"):
            raise RuntimeError("telegram_inbox_enqueue_failed")
        return EnqueueResult(inbox_id=str(row["inbox_id"]), inserted=bool(row["inserted"]))

    async def claim_next(self, owner: str, *, now: datetime | None = None) -> InboxRecord | None:
        row = await _fetch_one(
            """
            select inbox_id::text as inbox_id, binding_id, tenant_id, telegram_update_id,
                   payload, retry_count, state
            from telegram_inbox_claim(%s, %s)
            """,
            (owner, LEASE_SECONDS),
        )
        return _row_to_record(row)

    async def claim_by_id(
        self, inbox_id: str, owner: str, *, now: datetime | None = None
    ) -> InboxRecord | None:
        row = await _fetch_one(
            """
            select inbox_id::text as inbox_id, binding_id, tenant_id, telegram_update_id,
                   payload, retry_count, state
            from telegram_inbox_claim_by_id(%s::uuid, %s, %s)
            """,
            (inbox_id, owner, LEASE_SECONDS),
        )
        return _row_to_record(row)

    async def mark_processed(self, inbox_id: str) -> None:
        await _execute("select telegram_inbox_mark_processed(%s::uuid)", (inbox_id,))

    async def release_for_retry(self, inbox_id: str, error: str) -> None:
        await _execute(
            "select telegram_inbox_release_for_retry(%s::uuid, %s, %s)",
            (inbox_id, safe_error_summary(error), MAX_RETRIES),
        )

    async def reserve_outbox(
        self,
        *,
        inbox_id: str,
        binding_id: str,
        tenant_id: str,
        idempotency_key: str,
    ) -> OutboxRecord:
        row = await _fetch_one(
            """
            select outbox_id::text as outbox_id, state
            from telegram_outbox_reserve(%s::uuid, %s, %s, %s)
            """,
            (inbox_id, binding_id, tenant_id, idempotency_key),
        )
        if not row or not row.get("outbox_id"):
            raise RuntimeError("telegram_outbox_reserve_failed")
        return OutboxRecord(
            outbox_id=str(row["outbox_id"]),
            inbox_id=inbox_id,
            binding_id=binding_id,
            tenant_id=tenant_id,
            idempotency_key=idempotency_key,
            state=str(row["state"]),
        )

    async def mark_outbox_sent(self, outbox_id: str) -> None:
        await _execute("select telegram_outbox_mark_sent(%s::uuid)", (outbox_id,))

    async def enqueue_delivery_plan(
        self,
        *,
        inbox_id: str,
        binding_id: str,
        tenant_id: str,
        telegram_update_id: int,
        items: list[Any],
    ) -> list[DeliveryOutboxRecord]:
        from app.db_feature_readiness import SchemaFeatureUnavailable, get_feature_status
        from app.telegram.delivery import DeliveryDraft, compact_delivery_payload

        status = await get_feature_status("telegram_durable_outbox")
        if not status.ready:
            raise SchemaFeatureUnavailable(status)
        payload_items = []
        for index, item in enumerate(items, start=1):
            if isinstance(item, DeliveryDraft):
                kind = item.kind
                payload = compact_delivery_payload(kind, item.payload)
            elif isinstance(item, dict):
                kind = str(item.get("kind") or "text")
                payload = compact_delivery_payload(kind, item.get("payload") or item)
            else:
                continue
            payload_items.append(
                {
                    "sequence_no": index,
                    "kind": kind,
                    "payload": payload,
                    "idempotency_key": f"{inbox_id}:{index}:{kind}",
                }
            )
        rows = await _fetch_all(
            """
            select outbox_id::text as outbox_id, sequence_no, inserted
            from telegram_outbox_enqueue_items(%s::uuid, %s, %s, %s, %s::jsonb)
            """,
            (
                inbox_id,
                binding_id,
                tenant_id,
                int(telegram_update_id),
                json.dumps(payload_items),
            ),
        )
        result: list[DeliveryOutboxRecord] = []
        for row in rows:
            result.append(
                DeliveryOutboxRecord(
                    outbox_id=str(row["outbox_id"]),
                    inbox_id=inbox_id,
                    binding_id=binding_id,
                    tenant_id=tenant_id,
                    telegram_update_id=int(telegram_update_id),
                    sequence_no=int(row["sequence_no"]),
                    kind=str(payload_items[int(row["sequence_no"]) - 1]["kind"]),
                    payload=dict(payload_items[int(row["sequence_no"]) - 1]["payload"]),
                    status="pending",
                    idempotency_key=str(payload_items[int(row["sequence_no"]) - 1]["idempotency_key"]),
                )
            )
        return result

    async def claim_delivery(
        self,
        owner: str,
        *,
        binding_id: str | None = None,
        now: datetime | None = None,
    ) -> DeliveryOutboxRecord | None:
        row = await _fetch_one(
            """
            select outbox_id::text as outbox_id, inbox_id::text as inbox_id, binding_id,
                   tenant_id, telegram_update_id, sequence_no, kind, payload_json, status,
                   attempt_count, idempotency_key
            from telegram_outbox_claim_next(%s, %s, %s)
            """,
            (owner, OUTBOX_LEASE_SECONDS, binding_id),
        )
        return _row_to_delivery(row)

    async def mark_delivery_sent(self, outbox_id: str) -> None:
        await _execute("select telegram_outbox_mark_sent(%s::uuid)", (outbox_id,))

    async def mark_delivery_retryable(self, outbox_id: str, error_code: str) -> str:
        row = await _fetch_one(
            "select telegram_outbox_mark_retryable(%s::uuid, %s, %s, %s) as status",
            (outbox_id, safe_error_summary(error_code)[:80], OUTBOX_MAX_ATTEMPTS, 2),
        )
        return str((row or {}).get("status") or "retryable_failed")

    async def mark_delivery_unknown(self, outbox_id: str, error_code: str) -> None:
        await _execute(
            "select telegram_outbox_mark_unknown(%s::uuid, %s)",
            (outbox_id, safe_error_summary(error_code)[:80]),
        )

    def snapshot_deliveries(self) -> list[DeliveryOutboxRecord]:
        raise RuntimeError("postgres outbox snapshot is test-only")

    def snapshot_rows(self) -> list[InboxRecord]:
        raise RuntimeError("postgres inbox snapshot is test-only")


_store: InboxStore | None = None


def get_inbox_store() -> InboxStore:
    if _store is not None:
        return _store
    return PostgresInboxStore()


def set_inbox_store_for_tests(store: InboxStore | None) -> None:
    global _store
    _store = store


def _row_to_record(row: dict[str, Any] | None) -> InboxRecord | None:
    if not row:
        return None
    payload = row.get("payload") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    return InboxRecord(
        inbox_id=str(row["inbox_id"]),
        binding_id=str(row["binding_id"]),
        tenant_id=str(row["tenant_id"]),
        telegram_update_id=int(row["telegram_update_id"]),
        payload=dict(payload),
        state=str(row.get("state") or "leased"),
        received_at=utcnow(),
        retry_count=int(row.get("retry_count") or 0),
    )


def _row_to_delivery(row: dict[str, Any] | None) -> DeliveryOutboxRecord | None:
    if not row:
        return None
    payload = row.get("payload_json") or row.get("payload") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)
    return DeliveryOutboxRecord(
        outbox_id=str(row["outbox_id"]),
        inbox_id=str(row["inbox_id"]),
        binding_id=str(row["binding_id"]),
        tenant_id=str(row["tenant_id"]),
        telegram_update_id=int(row["telegram_update_id"]),
        sequence_no=int(row["sequence_no"]),
        kind=str(row["kind"]),
        payload=dict(payload),
        status=str(row.get("status") or "leased"),
        idempotency_key=str(row.get("idempotency_key") or ""),
        attempt_count=int(row.get("attempt_count") or 0),
    )


async def _fetch_one(query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
    from app.db import fetch_one, get_pool
    from app.settings import get_settings

    pool = get_pool()
    async with pool.connection(timeout=get_settings().database_timeout_sec) as conn:
        async with conn.transaction():
            return await fetch_one(conn, query, params)


async def _fetch_all(query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    from app.db import fetch_all, get_pool
    from app.settings import get_settings

    pool = get_pool()
    async with pool.connection(timeout=get_settings().database_timeout_sec) as conn:
        async with conn.transaction():
            return await fetch_all(conn, query, params)


async def _execute(query: str, params: tuple[Any, ...]) -> None:
    from app.db import get_pool
    from app.settings import get_settings

    pool = get_pool()
    async with pool.connection(timeout=get_settings().database_timeout_sec) as conn:
        async with conn.transaction():
            async with conn.cursor() as cur:
                await cur.execute(query, params)
