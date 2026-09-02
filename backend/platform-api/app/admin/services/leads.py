from __future__ import annotations

from datetime import datetime
from typing import Any

from app.admin.dto import gap_meta, pick_lead_metadata
from app.admin.masking import mask_contact
from app.admin.repositories import leads as leads_repo


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _lead_list_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "lead_id": str(row["lead_id"]),
        "public_id": row.get("public_id"),
        "status": row.get("status"),
        "delivery_status": row.get("delivery_status"),
        "name": row.get("name"),
        "contact_masked": mask_contact(row.get("contact")),
        "product_name": row.get("product_name"),
        "product_sku": row.get("product_sku"),
        "initial_ref_code": row.get("initial_ref_code"),
        "first_ref_code": row.get("first_ref_code"),
        "active_ref_code": row.get("active_ref_code"),
        "attributed_owner_id": row.get("attributed_owner_id"),
        "assigned_owner_id": row.get("assigned_owner_id"),
        "country_code": row.get("country_code"),
        "city": row.get("city"),
        "metadata": pick_lead_metadata(row.get("metadata")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "meta": gap_meta("last_activity_at"),
    }


def _lead_detail_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "lead_id": str(row["lead_id"]),
        "public_id": row.get("public_id"),
        "status": row.get("status"),
        "delivery_status": row.get("delivery_status"),
        "name": row.get("name"),
        "contact_masked": mask_contact(row.get("contact")),
        "product_name": row.get("product_name"),
        "product_sku": row.get("product_sku"),
        "product_variant": row.get("product_variant"),
        "page_url": row.get("page_url"),
        "initial_ref_code": row.get("initial_ref_code"),
        "first_ref_code": row.get("first_ref_code"),
        "active_ref_code": row.get("active_ref_code"),
        "attributed_owner_id": row.get("attributed_owner_id"),
        "assigned_owner_id": row.get("assigned_owner_id"),
        "country_code": row.get("country_code"),
        "city": row.get("city"),
        "service_location_id": str(row["service_location_id"]) if row.get("service_location_id") else None,
        "metadata": pick_lead_metadata(row.get("metadata")),
        "consent_version": row.get("consent_version"),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "meta": gap_meta("last_activity_at"),
    }


async def build_leads_list(
    tenant_id: str,
    *,
    limit: int,
    offset: int,
    status: str | None = None,
    ref_code: str | None = None,
    country_code: str | None = None,
    assignee_id: str | None = None,
    product_sku: str | None = None,
) -> dict[str, Any]:
    rows, total = await leads_repo.list_leads(
        tenant_id,
        limit=limit,
        offset=offset,
        status=status,
        ref_code=ref_code.lower() if ref_code else None,
        country_code=country_code,
        assignee_id=assignee_id,
        product_sku=product_sku,
    )
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "items": [_lead_list_item(row) for row in rows],
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }


async def build_lead_detail(tenant_id: str, lead_id: str, *, role: str) -> dict[str, Any]:
    bundle = await leads_repo.get_lead(tenant_id, lead_id)
    if not bundle:
        return {"ok": False, "error": "lead_not_found"}
    lead = bundle["lead"]
    return {
        "ok": True,
        "tenant_id": tenant_id,
        "lead": _lead_detail_item(lead),
        "attribution": {
            "attributed_owner_id": lead.get("attributed_owner_id"),
            "assigned_owner_id": lead.get("assigned_owner_id"),
            "watchers": [
                {
                    "watcher_actor_id": w.get("watcher_actor_id"),
                    "added_by_actor_id": w.get("added_by_actor_id"),
                    "scope": w.get("scope"),
                    "created_at": _iso(w.get("created_at")),
                }
                for w in bundle["watchers"]
            ],
        },
        "status_history": [
            {
                "old_status": h.get("old_status"),
                "new_status": h.get("new_status"),
                "changed_by_actor_id": h.get("changed_by_actor_id"),
                "reason": h.get("reason"),
                "created_at": _iso(h.get("created_at")),
            }
            for h in bundle["status_history"]
        ],
        "owner_history": [
            {
                "owner_id": h.get("owner_id"),
                "action": h.get("action"),
                "changed_by": h.get("changed_by"),
                "reason": h.get("reason"),
                "created_at": _iso(h.get("created_at")),
            }
            for h in bundle["owner_history"]
        ],
        "delivery_history": [
            {
                "delivery_id": str(h.get("delivery_id")) if h.get("delivery_id") else None,
                "channel": h.get("channel"),
                "status": h.get("status"),
                "error_text": h.get("error_text"),
                "created_at": _iso(h.get("created_at")),
                "sent_at": _iso(h.get("sent_at")),
            }
            for h in bundle["delivery_history"]
        ],
    }
