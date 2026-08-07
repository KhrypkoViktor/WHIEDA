from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.db import fetch_one, tenant_connection
from app.jobs.outbox import enqueue_outbox_event
from app.settings import get_settings

HONEYPOT_FIELD = "website"
FORBIDDEN_OWNER_FIELDS = {
    "owner_id",
    "assigned_owner_id",
    "attributed_owner_id",
    "watchers",
}


@dataclass
class LeadInput:
    tenant_id: str
    name: str
    contact: str
    product_name: str
    product_sku: str
    product_variant: str
    comment: str
    page_url: str
    first_ref_code: str
    active_ref_code: str
    visitor_session_id: str
    idempotency_key: str
    country_code: str
    consent_version: str
    metadata: dict[str, Any]


def clean(value: Any, max_len: int = 1000) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:max_len]


def parse_lead_body(body: dict[str, Any], tenant_id: str) -> LeadInput:
    for field in FORBIDDEN_OWNER_FIELDS:
        if body.get(field):
            raise HTTPException(status_code=400, detail={"ok": False, "error": "forbidden_field"})

    honey = clean(body.get(HONEYPOT_FIELD), 160)
    if honey:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "spam"})

    name = clean(body.get("name"), 160)
    contact = clean(body.get("contact"), 240)
    product = clean(body.get("product") or body.get("product_name"), 360)
    idempotency = clean(body.get("idempotency_key"), 160)
    errors: list[str] = []
    if not name:
        errors.append("name")
    if not contact:
        errors.append("contact")
    if not product:
        errors.append("product")
    if not idempotency:
        errors.append("idempotency")
    if errors:
        raise HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "message": "Проверьте имя, контакт и выбранный товар.",
                "errors": errors,
            },
        )

    country = clean(body.get("country_code"), 2).upper()
    if country not in {"RU", "BY"}:
        country = "RU"

    settings = get_settings()
    consent = clean(body.get("consent_version"), 80) or settings.lead_consent_version

    ref = clean(body.get("initial_ref") or body.get("ref"), 64).lower()
    active_ref = clean(body.get("active_ref") or body.get("ref"), 64).lower()
    visitor_session_id = clean(body.get("visitor_session_id"), 64)

    return LeadInput(
        tenant_id=tenant_id,
        name=name,
        contact=contact,
        product_name=product,
        product_sku=clean(body.get("product_sku"), 80),
        product_variant=clean(body.get("product_variant"), 240),
        comment=clean(body.get("comment"), 2000),
        page_url=clean(body.get("page_url"), 1000),
        first_ref_code=ref,
        active_ref_code=active_ref,
        visitor_session_id=visitor_session_id,
        idempotency_key=idempotency,
        country_code=country,
        consent_version=consent,
        metadata={
            "routing_version": "platform-core-v1",
            "country_source": clean(body.get("country_source"), 80),
            "visitor_session_id": visitor_session_id or None,
            "journey_type": clean(body.get("journey_type"), 32) or None,
            "route": clean(body.get("route"), 64) or None,
        },
    )


async def save_lead(lead: LeadInput) -> dict[str, Any]:
    query = """
    with supplied as (
      select
        %(first_ref)s::text as first_ref,
        %(active_ref)s::text as active_ref,
        %(requested_country)s::text as requested_country
    ),
    routing as (
      select
        case
          when first_profile.ref_code is not null
           and not (supplied.requested_country <> 'BY' and first_profile.owner_id = 'harold')
          then supplied.first_ref
          else null
        end as first_ref_code,
        case
          when active_profile.ref_code is not null
           and not (supplied.requested_country <> 'BY' and active_profile.owner_id = 'harold')
          then supplied.active_ref
          else null
        end as active_ref_code,
        case
          when supplied.requested_country <> 'BY' and first_profile.owner_id = 'harold'
          then 'viktor'
          else coalesce(first_profile.owner_id, 'viktor')
        end as attributed_owner_id,
        case
          when supplied.requested_country <> 'BY' and first_profile.owner_id = 'harold'
          then 'viktor'
          else coalesce(first_profile.owner_id, 'viktor')
        end as assigned_owner_id,
        supplied.requested_country,
        first_profile.profile_version as ref_profile_version
      from supplied
      left join referral_profiles first_profile
        on first_profile.ref_code = supplied.first_ref
       and first_profile.tenant_id = %(tenant_id)s
       and first_profile.enabled = true
      left join referral_profiles active_profile
        on active_profile.ref_code = supplied.active_ref
       and active_profile.tenant_id = %(tenant_id)s
       and active_profile.enabled = true
    ),
    service_route as (
      select sl.service_location_id, sl.country_code, sl.city
      from routing
      join service_locations sl
        on sl.tenant_id = %(tenant_id)s
       and sl.operator_actor_id = routing.assigned_owner_id
       and sl.enabled = true
      order by sl.verified_at desc nulls last, sl.created_at desc
      limit 1
    ),
    saved as (
      insert into website_leads (
        tenant_id, name, contact, comment, product_name, product_sku, product_variant,
        page_url, initial_ref_code, first_ref_code, active_ref_code,
        attributed_owner_id, assigned_owner_id, ref_profile_version,
        service_location_id, country_code, city, idempotency_key, consent_version, metadata
      )
      select
        %(tenant_id)s,
        %(name)s,
        %(contact)s,
        %(comment)s,
        %(product_name)s,
        %(product_sku)s,
        %(product_variant)s,
        %(page_url)s,
        routing.first_ref_code,
        routing.first_ref_code,
        routing.active_ref_code,
        routing.attributed_owner_id,
        routing.assigned_owner_id,
        routing.ref_profile_version,
        service_route.service_location_id,
        coalesce(service_route.country_code, routing.requested_country),
        service_route.city,
        %(idempotency_key)s,
        %(consent_version)s,
        %(metadata)s::jsonb
      from routing
      left join service_route on true
      on conflict (tenant_id, idempotency_key) do update
        set updated_at = now()
      returning lead_id, public_id, (xmax = 0) as created,
                attributed_owner_id, assigned_owner_id
    ),
    ownership as (
      insert into website_lead_owner_history (
        lead_id, tenant_id, owner_id, action, changed_by, reason
      )
      select lead_id, %(tenant_id)s, assigned_owner_id,
             'created', 'platform-core-v1', 'Server-side referral routing'
      from saved
      where created
    ),
    status_log as (
      insert into website_lead_status_history (
        lead_id, tenant_id, old_status, new_status, changed_by_actor_id, reason
      )
      select lead_id, %(tenant_id)s, null, 'new', 'platform-core-v1', 'Created from website form'
      from saved
      where created
    )
    select public_id, created, attributed_owner_id, assigned_owner_id, lead_id
    from saved
    """

    params = {
        "tenant_id": lead.tenant_id,
        "name": lead.name,
        "contact": lead.contact,
        "comment": lead.comment,
        "product_name": lead.product_name,
        "product_sku": lead.product_sku,
        "product_variant": lead.product_variant,
        "page_url": lead.page_url,
        "first_ref": lead.first_ref_code,
        "active_ref": lead.active_ref_code,
        "requested_country": lead.country_code,
        "idempotency_key": lead.idempotency_key,
        "consent_version": lead.consent_version,
        "metadata": json.dumps(lead.metadata),
    }

    async with tenant_connection(lead.tenant_id) as conn:
        row = await fetch_one(conn, query, params)
        if not row:
            raise HTTPException(status_code=500, detail={"ok": False, "error": "lead_save_failed"})

        if row["created"]:
            await enqueue_outbox_event(
                conn,
                tenant_id=lead.tenant_id,
                event_type="lead_created",
                idempotency_key=f"lead_delivery:{row['lead_id']}",
                payload={
                    "lead_id": str(row["lead_id"]),
                    "public_id": row["public_id"],
                },
            )
            if lead.visitor_session_id:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        insert into interaction_events (
                          tenant_id, session_id, event_type, idempotency_key, payload
                        ) values (
                          %s, %s::uuid, 'lead_created', %s, %s::jsonb
                        )
                        on conflict (tenant_id, idempotency_key) do nothing
                        """,
                        (
                            lead.tenant_id,
                            lead.visitor_session_id,
                            f"lead_created:{row['lead_id']}",
                            json.dumps(
                                {
                                    "lead_id": str(row["lead_id"]),
                                    "public_id": row["public_id"],
                                    "product_name": lead.product_name,
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    )
        return row


async def validate_lead_shadow(lead: LeadInput) -> dict[str, Any]:
    async with tenant_connection(lead.tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select ref_code, owner_id
            from referral_profiles
            where tenant_id = %s and ref_code = %s and enabled = true
            limit 1
            """,
            (lead.tenant_id, lead.first_ref_code or lead.active_ref_code),
        )
    return {
        "valid": True,
        "ref_found": bool(row),
        "would_assign_owner": row["owner_id"] if row else "viktor",
    }
