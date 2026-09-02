"""Admin API payloads for advisor gap review queue."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import HTTPException

from app.admin.audit import write_audit_log
from app.admin.gap_review.constants import (
    CANDIDATE_TYPES,
    NOTE_MAX_LEN,
    OWNER_ROLES,
    PATCHABLE_FIELDS,
    PRIORITIES,
    STATUSES,
)
from app.admin.gap_review.export import export_csv, export_markdown
from app.admin.gap_review.refresh import refresh_advisor_gap_review_queue
from app.admin.gap_review import repository as repo
from app.db import tenant_connection


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _public_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "gap_kind": row.get("gap_kind"),
        "question_normalized": row.get("question_normalized"),
        "detected_product": row.get("detected_product"),
        "event_count": int(row.get("event_count") or 0),
        "first_seen_at": _iso(row.get("first_seen_at")),
        "last_seen_at": _iso(row.get("last_seen_at")),
        "status": row.get("status"),
        "priority": row.get("priority"),
        "owner_role": row.get("owner_role"),
        "owner_name": row.get("owner_name"),
        "operator_note": row.get("operator_note"),
        "candidate_type": row.get("candidate_type"),
        "evidence_ref": row.get("evidence_ref"),
        "resolved_at": _iso(row.get("resolved_at")),
    }


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


async def build_summary(tenant_id: str) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        stats = await repo.summary_stats(conn, tenant_id)
    totals = stats.get("totals") or {}
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "unique_gaps": int(totals.get("unique_gaps") or 0),
        "total_repeats": int(totals.get("total_repeats") or 0),
        "first_seen_at": _iso(totals.get("first_seen")),
        "last_seen_at": _iso(totals.get("last_seen")),
        "by_gap_kind": stats.get("by_gap_kind") or [],
        "by_status": stats.get("by_status") or [],
        "top_unresolved": [
            {
                "question_normalized": r.get("question_normalized"),
                "gap_kind": r.get("gap_kind"),
                "event_count": int(r.get("event_count") or 0),
                "last_seen_at": _iso(r.get("last_seen_at")),
                "status": r.get("status"),
                "priority": r.get("priority"),
            }
            for r in stats.get("top_unresolved") or []
        ],
    }


async def build_list(
    tenant_id: str,
    *,
    gap_kind: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        rows, total = await repo.list_review_items(
            conn,
            tenant_id,
            gap_kind=gap_kind,
            status=status,
            priority=priority,
            from_ts=_parse_dt(from_ts),
            to_ts=_parse_dt(to_ts),
            limit=limit,
            offset=offset,
        )
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_public_item(row) for row in rows],
    }


async def build_detail(tenant_id: str, item_id: str) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        row = await repo.fetch_review_item(conn, tenant_id, item_id)
        if not row:
            return {"ok": False, "error": "gap_item_not_found"}
        mutations = await repo.fetch_mutations(conn, tenant_id, item_id)
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "item": _public_item(row),
        "audit_history": [
            {
                "mutation_id": int(m["mutation_id"]),
                "created_at": _iso(m.get("created_at")),
                "old_values": m.get("old_values") or {},
                "new_values": m.get("new_values") or {},
            }
            for m in mutations
        ],
    }


def _validate_patch(body: dict[str, Any]) -> dict[str, Any]:
    unknown = set(body.keys()) - PATCHABLE_FIELDS
    if unknown:
        raise HTTPException(
            status_code=400,
            detail={"ok": False, "error": "invalid_fields", "fields": sorted(unknown)},
        )
    if not body:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "empty_patch"})
    updates: dict[str, Any] = {}
    if "status" in body:
        status = str(body["status"])
        if status not in STATUSES:
            raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid_status"})
        updates["status"] = status
    if "priority" in body:
        priority = str(body["priority"])
        if priority not in PRIORITIES:
            raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid_priority"})
        updates["priority"] = priority
    if "owner_role" in body:
        role = body["owner_role"]
        if role is not None and str(role) not in OWNER_ROLES:
            raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid_owner_role"})
        updates["owner_role"] = role
    if "owner_name" in body:
        name = body["owner_name"]
        updates["owner_name"] = str(name).strip()[:120] if name is not None else None
    if "operator_note" in body:
        note = str(body.get("operator_note") or "")
        if len(note) > NOTE_MAX_LEN:
            raise HTTPException(status_code=400, detail={"ok": False, "error": "note_too_long"})
        updates["operator_note"] = note or None
    if "candidate_type" in body:
        candidate = str(body["candidate_type"])
        if candidate not in CANDIDATE_TYPES:
            raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid_candidate_type"})
        updates["candidate_type"] = candidate
    if updates.get("status") == "resolved":
        updates["resolved_at"] = datetime.now().astimezone()
    elif "status" in updates and updates["status"] != "resolved":
        updates["resolved_at"] = None
    return updates


async def patch_item(
    tenant_id: str,
    item_id: str,
    body: dict[str, Any],
    *,
    actor_principal_id: str | None,
) -> dict[str, Any]:
    updates = _validate_patch(body)
    async with tenant_connection(tenant_id) as conn:
        existing = await repo.fetch_review_item(conn, tenant_id, item_id)
        if not existing:
            return {"ok": False, "error": "gap_item_not_found"}
        old_snapshot = {k: existing.get(k) for k in PATCHABLE_FIELDS}
        old_snapshot["resolved_at"] = _iso(existing.get("resolved_at"))
        new_row = await repo.update_review_item(
            conn,
            tenant_id=tenant_id,
            item_id=item_id,
            fields=updates,
        )
        if not new_row:
            return {"ok": False, "error": "gap_item_not_found"}
        new_snapshot = {k: new_row.get(k) for k in PATCHABLE_FIELDS}
        new_snapshot["resolved_at"] = _iso(new_row.get("resolved_at"))
        await repo.insert_mutation(
            conn,
            tenant_id=tenant_id,
            item_id=item_id,
            actor_principal_id=actor_principal_id,
            old_values=old_snapshot,
            new_values=new_snapshot,
        )
    await write_audit_log(
        principal_id=actor_principal_id,
        action="admin_advisor_gap_patch",
        target_tenant_id=tenant_id,
        object_type="advisor_gap_review_item",
        object_id=item_id,
        details={"fields": sorted(updates.keys())},
    )
    return {"ok": True, "tenant_id": tenant_id, "item": _public_item(new_row)}


async def build_export(tenant_id: str, *, fmt: str) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        rows, _total = await repo.list_review_items(conn, tenant_id, limit=500, offset=0)
    if fmt == "csv":
        content = export_csv(rows)
        content_type = "text/csv; charset=utf-8"
    elif fmt == "md":
        content = export_markdown(rows, tenant_id=tenant_id)
        content_type = "text/markdown; charset=utf-8"
    else:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "invalid_format"})
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "format": fmt,
        "content_type": content_type,
        "content": content,
    }


async def run_refresh(tenant_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    result = await refresh_advisor_gap_review_queue(tenant_id, dry_run=dry_run)
    return {"ok": True, "tenant_id": tenant_id, "dry_run": dry_run, **result.as_dict()}
