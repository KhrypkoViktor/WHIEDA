from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from app.db import fetch_all, fetch_one, tenant_connection
from app.partner_library.storage import StorageBackend, validate_storage_key


def public_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_id": str(row["item_id"]),
        "slug": row["slug"],
        "category": row["category"],
        "title": row["title"],
        "description": row.get("description"),
        "kind": row["kind"],
        "mime_type": row["mime_type"],
        "size_bytes": row.get("size_bytes"),
        "published_at": (
            row["published_at"].isoformat()
            if hasattr(row.get("published_at"), "isoformat")
            else row.get("published_at")
        ),
    }


async def list_published_items(tenant_id: str) -> list[dict[str, Any]]:
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select item_id, slug, category, title, description, kind,
                   mime_type, size_bytes, published_at
            from partner_library_items
            where tenant_id = %s and status = 'published'
            order by category, sort_order, title, item_id
            """,
            (tenant_id,),
        )
    return [public_item(row) for row in rows]


async def load_published_item(tenant_id: str, item_id: UUID) -> dict[str, Any] | None:
    async with tenant_connection(tenant_id) as conn:
        return await fetch_one(
            conn,
            """
            select item_id, slug, category, title, description, kind,
                   storage_key, mime_type, size_bytes, published_at
            from partner_library_items
            where tenant_id = %s and item_id = %s and status = 'published'
            """,
            (tenant_id, item_id),
        )


async def create_download(
    tenant_id: str,
    item_id: UUID,
    *,
    storage: StorageBackend,
    ttl: int,
) -> dict[str, Any] | None:
    row = await load_published_item(tenant_id, item_id)
    if not row:
        return None
    storage_key = validate_storage_key(str(row["storage_key"]), tenant_id=tenant_id)
    try:
        await asyncio.to_thread(storage.stat, storage_key)
        url = await asyncio.to_thread(storage.signed_url, storage_key, ttl)
    except FileNotFoundError:
        return None
    return {
        "ok": True,
        "item_id": str(row["item_id"]),
        "url": url,
        "expires_in": ttl,
    }
