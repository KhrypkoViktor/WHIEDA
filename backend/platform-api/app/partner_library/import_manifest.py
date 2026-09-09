from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.db import close_pool, init_pool, tenant_connection
from app.partner_library.storage import get_storage_backend, validate_storage_key

ALLOWED_CATEGORIES = {
    "presentations",
    "training",
    "business-cards",
    "print-materials",
    "work-documents",
    "repeat-prices",
}
ALLOWED_KINDS = {"file", "video", "course", "template"}
ALLOWED_STATUSES = {"draft", "published", "archived"}
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")


def _canonical_json(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def manifest_sha(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def normalize_manifest(payload: dict[str, Any], *, tenant_id: str) -> dict[str, Any]:
    if payload.get("version") != 1 or payload.get("tenant_id") != tenant_id:
        raise ValueError("manifest version or tenant_id mismatch")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("manifest items must be a non-empty list")
    normalized: list[dict[str, Any]] = []
    seen_slugs: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("manifest item must be an object")
        slug = str(raw.get("slug") or "").strip()
        category = str(raw.get("category") or "").strip()
        kind = str(raw.get("kind") or "").strip()
        status = str(raw.get("status") or "draft").strip()
        title = str(raw.get("title") or "").strip()
        mime_type = str(raw.get("mime_type") or "").strip()
        storage_key = validate_storage_key(str(raw.get("storage_key") or ""), tenant_id=tenant_id)
        if not SLUG_RE.fullmatch(slug):
            raise ValueError(f"invalid slug: {slug!r}")
        if category not in ALLOWED_CATEGORIES:
            raise ValueError(f"invalid category: {category!r}")
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"invalid kind: {kind!r}")
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        if not title or not mime_type:
            raise ValueError(f"title and mime_type are required for {slug!r}")
        if slug in seen_slugs:
            raise ValueError("duplicate slug in manifest")
        seen_slugs.add(slug)
        normalized.append(
            {
                "slug": slug,
                "category": category,
                "title": title,
                "description": str(raw.get("description") or "").strip() or None,
                "kind": kind,
                "storage_key": storage_key,
                "mime_type": mime_type,
                "status": status,
                "sort_order": int(raw.get("sort_order") or 0),
            }
        )
    return {"version": 1, "tenant_id": tenant_id, "items": normalized}


async def inspect_storage(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    storage = get_storage_backend()
    inspected: list[dict[str, Any]] = []
    for item in manifest["items"]:
        exists = await asyncio.to_thread(storage.exists, item["storage_key"])
        if not exists:
            raise FileNotFoundError(item["storage_key"])
        stat = await asyncio.to_thread(storage.stat, item["storage_key"])
        inspected.append({**item, "size_bytes": stat.size_bytes})
    return inspected


async def apply_manifest(tenant_id: str, items: list[dict[str, Any]]) -> int:
    async with tenant_connection(tenant_id) as conn:
        async with conn.cursor() as cur:
            for item in items:
                await cur.execute(
                    """
                    insert into partner_library_items (
                      tenant_id, slug, category, title, description, kind,
                      storage_key, mime_type, size_bytes, status, sort_order,
                      published_at, updated_at
                    ) values (
                      %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      case when %s = 'published' then now() else null end,
                      now()
                    )
                    on conflict (tenant_id, slug) do update set
                      category = excluded.category,
                      title = excluded.title,
                      description = excluded.description,
                      kind = excluded.kind,
                      storage_key = excluded.storage_key,
                      mime_type = excluded.mime_type,
                      size_bytes = excluded.size_bytes,
                      status = excluded.status,
                      sort_order = excluded.sort_order,
                      published_at = case
                        when excluded.status = 'published'
                          then coalesce(partner_library_items.published_at, now())
                        else null
                      end,
                      updated_at = now()
                    """,
                    (
                        tenant_id,
                        item["slug"],
                        item["category"],
                        item["title"],
                        item["description"],
                        item["kind"],
                        item["storage_key"],
                        item["mime_type"],
                        item["size_bytes"],
                        item["status"],
                        item["sort_order"],
                        item["status"],
                    ),
                )
    return len(items)


async def run(args: argparse.Namespace) -> None:
    raw = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    normalized = normalize_manifest(raw, tenant_id=args.tenant)
    digest = manifest_sha(normalized)
    inspected = await inspect_storage(normalized)
    report = {
        "ok": True,
        "mode": "apply" if args.apply else "dry-run",
        "tenant_id": args.tenant,
        "manifest_sha256": digest,
        "item_count": len(inspected),
        "items": [
            {
                "slug": item["slug"],
                "category": item["category"],
                "storage_key": item["storage_key"],
                "status": item["status"],
                "size_bytes": item["size_bytes"],
            }
            for item in inspected
        ],
    }
    if args.apply:
        if args.expected_sha != digest:
            raise ValueError("--expected-sha must match the current dry-run manifest SHA")
        await init_pool()
        try:
            report["changed"] = await apply_manifest(args.tenant, inspected)
        finally:
            await close_pool()
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate or import partner library manifest")
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-sha")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
