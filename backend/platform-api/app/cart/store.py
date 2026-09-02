"""Cart session persistence: memory for tests, Postgres for runtime."""

from __future__ import annotations

import copy
from typing import Any

from app.db import fetch_one, tenant_connection


def _clone(value: Any) -> Any:
    return copy.deepcopy(value)


class MemoryCartStore:
    def __init__(self) -> None:
        self._carts: dict[tuple[str, str], dict[str, Any]] = {}
        self._snapshots: dict[tuple[str, str], dict[str, Any]] = {}

    async def get(self, tenant_id: str, cart_session_id: str) -> dict[str, Any] | None:
        row = self._carts.get((tenant_id, cart_session_id))
        return _clone(row) if row else None

    async def save(self, record: dict[str, Any]) -> None:
        key = (str(record["tenant_id"]), str(record["cart_session_id"]))
        self._carts[key] = _clone(record)

    async def save_snapshot(self, tenant_id: str, token: str, payload: dict[str, Any]) -> None:
        self._snapshots[(tenant_id, token)] = _clone(payload)

    async def get_snapshot(self, tenant_id: str, token: str) -> dict[str, Any] | None:
        row = self._snapshots.get((tenant_id, token))
        return _clone(row) if row else None


class PostgresCartStore:
    async def get(self, tenant_id: str, cart_session_id: str) -> dict[str, Any] | None:
        async with tenant_connection(tenant_id) as conn:
            row = await fetch_one(
                conn,
                """
                select tenant_id, cart_session_id, market_id, price_mode,
                       first_ref, active_ref, visitor_session_id, items, item_idempotency
                from platform_cart_sessions
                where tenant_id = %s and cart_session_id = %s
                limit 1
                """,
                (tenant_id, cart_session_id),
            )
        if not row:
            return None
        return {
            "tenant_id": row["tenant_id"],
            "cart_session_id": row["cart_session_id"],
            "market_id": row["market_id"],
            "price_mode": row["price_mode"],
            "first_ref": row.get("first_ref") or "",
            "ref": row.get("active_ref") or "",
            "visitor_session_id": row.get("visitor_session_id") or "",
            "items": list(row.get("items") or []),
            "item_idempotency": dict(row.get("item_idempotency") or {}),
        }

    async def save(self, record: dict[str, Any]) -> None:
        async with tenant_connection(record["tenant_id"]) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    insert into platform_cart_sessions (
                      tenant_id, cart_session_id, market_id, price_mode,
                      first_ref, active_ref, visitor_session_id, items, item_idempotency, updated_at
                    ) values (
                      %s, %s, %s, %s,
                      %s, %s, %s, %s::jsonb, %s::jsonb, now()
                    )
                    on conflict (tenant_id, cart_session_id) do update set
                      market_id = excluded.market_id,
                      price_mode = excluded.price_mode,
                      active_ref = excluded.active_ref,
                      visitor_session_id = excluded.visitor_session_id,
                      items = excluded.items,
                      item_idempotency = excluded.item_idempotency,
                      updated_at = now()
                    """,
                    (
                        record["tenant_id"],
                        record["cart_session_id"],
                        record["market_id"],
                        record["price_mode"],
                        record.get("first_ref") or None,
                        record.get("ref") or None,
                        record.get("visitor_session_id") or None,
                        _json(record.get("items") or []),
                        _json(record.get("item_idempotency") or {}),
                    ),
                )

    async def save_snapshot(self, tenant_id: str, token: str, payload: dict[str, Any]) -> None:
        token_hash = _hash_token(token)
        async with tenant_connection(tenant_id) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    insert into platform_cart_snapshots (
                      tenant_id, snapshot_token_hash, cart_session_id, payload
                    ) values (%s, %s, %s, %s::jsonb)
                    on conflict (tenant_id, snapshot_token_hash) do update set
                      payload = excluded.payload
                    """,
                    (
                        tenant_id,
                        token_hash,
                        payload.get("cart_session_id"),
                        _json(payload),
                    ),
                )

    async def get_snapshot(self, tenant_id: str, token: str) -> dict[str, Any] | None:
        async with tenant_connection(tenant_id) as conn:
            row = await fetch_one(
                conn,
                """
                select payload
                from platform_cart_snapshots
                where tenant_id = %s and snapshot_token_hash = %s
                limit 1
                """,
                (tenant_id, _hash_token(token)),
            )
        if not row:
            return None
        payload = row.get("payload")
        return dict(payload) if isinstance(payload, dict) else None


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


def _hash_token(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


_default_store = PostgresCartStore()


def get_store() -> MemoryCartStore | PostgresCartStore:
    return _default_store
