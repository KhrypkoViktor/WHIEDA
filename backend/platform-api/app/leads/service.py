from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

from app.crm.service import add_lead_card
from app.db import fetch_one, tenant_connection
from app.jobs.outbox import enqueue_outbox_event
from app.leads.meeting_leads import (
    CARD_SOURCE,
    KIND_MEETING,
    MEETING_PRODUCT_NAME,
    NAME_MAX,
    URL_MAX,
    card_note,
    card_tags,
    clean_messenger,
    clean_source_tag,
    clean_utm,
    find_person_anchor,
    lookup_meeting,
    normalize_meeting_phone,
    notify_partner,
    origin_text,
    parse_meeting_id,
)
from app.settings import get_settings

HONEYPOT_FIELD = "website"
FORBIDDEN_OWNER_FIELDS = {
    "owner_id",
    "assigned_owner_id",
    "attributed_owner_id",
    "watchers",
}
SUBDOMAIN_TO_REF = {
    "elena": "onlineelena",
    "samtsova": "olga-samtsova",
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
    # Отдельная необязательная галочка «Хочу получать новости и предложения»
    # (38-ФЗ ст. 18, 26.09.2026). Не согласие на обработку ПДн и не условие заявки.
    marketing_consent: bool = False
    # Запись на Живую встречу (kind = "meeting", V44). У заявок других видов — значения по умолчанию.
    kind: str = "request"
    phone_e164: str | None = None
    messenger: str | None = None
    meeting_id: int | None = None
    utm: dict[str, str] = field(default_factory=dict)
    source_tag: str | None = None


def clean(value: Any, max_len: int = 1000) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:max_len]


_TRUE_WORDS = {"true", "1", "on", "yes", "да"}


def parse_marketing_consent(value: Any) -> bool:
    """Checkbox value from the form: only an explicit yes counts (unchecked → False)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value or "").strip().lower() in _TRUE_WORDS


def ref_from_host(hostname: str) -> str:
    host = clean(hostname, 253).lower()
    if not host.endswith(".wwc.best") or host in {"wwc.best", "www.wwc.best"}:
        return ""
    sub = host.split(".")[0]
    return SUBDOMAIN_TO_REF.get(sub, sub)


def ref_from_page_url(page_url: str) -> str:
    raw = clean(page_url, 1000)
    if not raw:
        return ""
    parsed = urlparse(raw)
    query_ref = (parse_qs(parsed.query).get("ref") or [""])[0]
    query_ref = clean(query_ref, 64).lower()
    if query_ref:
        return query_ref
    return ref_from_host(parsed.hostname or "")


def resolve_lead_refs(body: dict[str, Any]) -> tuple[str, str]:
    first = clean(body.get("initial_ref") or body.get("first_ref") or body.get("ref"), 64).lower()
    active = clean(body.get("active_ref") or body.get("ref"), 64).lower()
    from_url = ref_from_page_url(str(body.get("page_url") or body.get("landing_url") or ""))
    if not active:
        active = from_url
    if not first:
        first = from_url or active
    return first, active


def _uuid_or_none(value: Any) -> str | None:
    """id сессии посетителя уходит в запрос как uuid: негодная строка уронила бы всю заявку."""
    try:
        return str(uuid.UUID(clean(value, 64))) if value else None
    except ValueError:
        return None


def _resolve_country(body: dict[str, Any]) -> str:
    country = clean(body.get("country_code"), 2).upper()
    if not country:
        iso = clean(body.get("country_iso"), 8).upper()
        if iso == "BY":
            country = "BY"
        elif iso == "RU":
            country = "RU"
        elif iso in {"", "*"}:
            country = "RU"
        else:
            country = "RU"
    elif country not in {"RU", "BY"}:
        country = "RU"
    return country


def parse_meeting_body(body: dict[str, Any], tenant_id: str) -> LeadInput:
    """Запись на Живую встречу: ``kind = "meeting"`` (V44). Имя 1-160, телефон в E.164,
    мессенджер telegram|max, согласие на обработку данных и ключ идемпотентности обязательны -
    иначе 422 со списком полей в ``errors``. Метки рекламы и площадки, адреса страниц и id
    встречи необязательны: негодное отбрасывается, заявка принимается."""
    name = clean(body.get("name"), NAME_MAX)
    phone = normalize_meeting_phone(body.get("phone"))
    messenger = clean_messenger(body.get("messenger"))
    consent = body.get("consent")
    consent = consent if isinstance(consent, dict) else {}
    idempotency = clean(body.get("idempotency_key"), 160)
    errors: list[str] = []
    if not name:
        errors.append("name")
    if phone is None:
        errors.append("phone")
    if messenger is None:
        errors.append("messenger")
    if not parse_marketing_consent(consent.get("accepted")):
        errors.append("consent")
    if not idempotency:
        errors.append("idempotency_key")
    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "error": "validation_failed",
                "message": "Проверьте имя, телефон, мессенджер и согласие на обработку данных.",
                "errors": errors,
            },
        )

    metadata: dict[str, Any] = {
        "routing_version": "platform-core-v1",
        "kind": KIND_MEETING,
        "landing_url": clean(body.get("landing_url"), URL_MAX) or None,
        "visitor_session_id": _uuid_or_none(body.get("visitor_session_id")),
    }
    raw_meeting_id = body.get("meeting_id")
    meeting_id = parse_meeting_id(raw_meeting_id)
    if meeting_id is None and raw_meeting_id not in (None, ""):
        metadata["meeting_id_invalid"] = clean(raw_meeting_id, 40)
    raw_tag = clean(body.get("source_tag"), 160)
    source_tag = clean_source_tag(raw_tag)
    if raw_tag and source_tag is None:
        metadata["source_tag_invalid"] = raw_tag[:64]

    first, active_ref = resolve_lead_refs(body)
    settings = get_settings()
    return LeadInput(
        tenant_id=tenant_id,
        name=name,
        contact=phone,
        product_name=MEETING_PRODUCT_NAME,
        product_sku="",
        product_variant="",
        comment="",
        page_url=clean(body.get("page_url"), URL_MAX),
        first_ref_code=first,
        active_ref_code=active_ref,
        visitor_session_id=metadata["visitor_session_id"] or "",
        idempotency_key=idempotency,
        country_code=_resolve_country(body),
        consent_version=clean(consent.get("version"), 80) or settings.lead_consent_version,
        marketing_consent=parse_marketing_consent(body.get("marketing_consent")),
        metadata=metadata,
        kind=KIND_MEETING,
        phone_e164=phone,
        messenger=messenger,
        meeting_id=meeting_id,
        utm=clean_utm(body.get("utm")),
        source_tag=source_tag,
    )


def parse_lead_body(body: dict[str, Any], tenant_id: str) -> LeadInput:
    for owner_field in FORBIDDEN_OWNER_FIELDS:
        if body.get(owner_field):
            raise HTTPException(status_code=400, detail={"ok": False, "error": "forbidden_field"})

    honey = clean(body.get(HONEYPOT_FIELD), 160)
    if honey:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "spam"})

    if clean(body.get("kind"), 16).lower() == KIND_MEETING:
        return parse_meeting_body(body, tenant_id)

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

    country = _resolve_country(body)

    settings = get_settings()
    consent = clean(body.get("consent_version"), 80) or settings.lead_consent_version

    first, active_ref = resolve_lead_refs(body)
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
        first_ref_code=first,
        active_ref_code=active_ref,
        visitor_session_id=visitor_session_id,
        idempotency_key=idempotency,
        country_code=country,
        consent_version=consent,
        marketing_consent=parse_marketing_consent(body.get("marketing_consent")),
        metadata={
            "routing_version": "platform-core-v1",
            "country_source": clean(body.get("country_source"), 80),
            "visitor_session_id": visitor_session_id or None,
            "journey_type": clean(body.get("journey_type"), 32) or None,
            "route": clean(body.get("route"), 64) or None,
            "market_id": clean(body.get("market_id"), 16) or None,
            "country_iso": clean(body.get("country_iso"), 8) or None,
            "country_name": clean(body.get("country"), 120) or None,
            "city_selected": clean(body.get("city_selected") or body.get("city"), 160) or None,
            "center_id": clean(body.get("center_id"), 80) or None,
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
        supplied.first_ref as first_ref_code,
        case
          when active_profile.ref_code is not null
           and not (supplied.requested_country <> 'BY' and active_profile.owner_id = 'harold')
          then supplied.active_ref
          else null
        end as active_ref_code,
        case
          when supplied.requested_country <> 'BY'
           and coalesce(active_profile.owner_id, first_profile.owner_id) = 'harold'
          then %(organic_owner_id)s
          else coalesce(active_profile.owner_id, first_profile.owner_id, %(organic_owner_id)s)
        end as attributed_owner_id,
        coalesce(%(locked_owner_id)s::text, case
          when supplied.requested_country <> 'BY'
           and coalesce(active_profile.owner_id, first_profile.owner_id) = 'harold'
          then %(organic_owner_id)s
          else coalesce(active_profile.owner_id, first_profile.owner_id, %(organic_owner_id)s)
        end) as assigned_owner_id,
        supplied.requested_country,
        coalesce(active_profile.profile_version, first_profile.profile_version) as ref_profile_version
      from supplied
      left join referral_profiles first_profile
        on first_profile.ref_code = supplied.first_ref
       and first_profile.tenant_id = %(tenant_id)s
       and first_profile.enabled = true
       and exists (
         select 1
         from partner_subscriptions first_subscription
         where first_subscription.tenant_id = first_profile.tenant_id
           and first_subscription.ref_code = first_profile.ref_code
           and partner_subscription_state(first_subscription.paid_until, now()) in ('active', 'grace')
       )
      left join referral_profiles active_profile
        on active_profile.ref_code = supplied.active_ref
       and active_profile.tenant_id = %(tenant_id)s
       and active_profile.enabled = true
       and exists (
         select 1
         from partner_subscriptions active_subscription
         where active_subscription.tenant_id = active_profile.tenant_id
           and active_subscription.ref_code = active_profile.ref_code
           and partner_subscription_state(active_subscription.paid_until, now()) in ('active', 'grace')
       )
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
        service_location_id, country_code, city, idempotency_key, consent_version, metadata,
        marketing_consent, marketing_consent_at/*EXTRA_COLUMNS*/
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
        %(first_ref)s,
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
        %(metadata)s::jsonb,
        %(marketing_consent)s,
        case when %(marketing_consent)s then now() else null end/*EXTRA_VALUES*/
      from routing
      left join service_route on true
      on conflict (tenant_id, idempotency_key) do update
        set updated_at = now()
      returning lead_id, public_id, (xmax = 0) as created,
                attributed_owner_id, assigned_owner_id, first_ref_code, active_ref_code
    ),
    ownership as (
      insert into website_lead_owner_history (
        lead_id, tenant_id, owner_id, action, changed_by, reason
      )
      select lead_id, %(tenant_id)s, assigned_owner_id,
             'created', 'platform-core-v1', %(owner_reason)s::text
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
    select public_id, created, attributed_owner_id, assigned_owner_id, lead_id,
           first_ref_code, active_ref_code
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
        "organic_owner_id": get_settings().platform_organic_owner_id,
        "idempotency_key": lead.idempotency_key,
        "consent_version": lead.consent_version,
        "metadata": json.dumps(lead.metadata),
        "marketing_consent": bool(lead.marketing_consent),
        "locked_owner_id": None,
        "owner_reason": "Server-side referral routing",
    }
    meeting = lead.kind == KIND_MEETING
    if not meeting:
        # Заявка других видов идёт прежним запросом: дополнительные колонки только у записи на встречу.
        query = query.replace("/*EXTRA_COLUMNS*/", "").replace("/*EXTRA_VALUES*/", "")

    async with tenant_connection(lead.tenant_id) as conn:
        meeting_row: dict[str, Any] | None = None
        if meeting:
            query, params, meeting_row = await _meeting_save_plan(conn, lead, query, params)
        row = await fetch_one(conn, query, params)
        if not row:
            raise HTTPException(status_code=500, detail={"ok": False, "error": "lead_save_failed"})

        if row["created"] and meeting:
            await _after_meeting_lead(conn, lead, row, meeting_row)
        elif row["created"]:
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
            # Ежедневник партнёра: заявка → карточка «Новый контакт» у владельца,
            # если он уже открывал ежедневник. Сбой здесь не роняет заявку.
            await add_lead_card(
                conn,
                tenant_id=lead.tenant_id,
                lead_id=str(row["lead_id"]),
                owner_actor_id=row.get("assigned_owner_id"),
                name=lead.name,
                contact=lead.contact,
                product_name=lead.product_name,
                comment=lead.comment,
            )
        if row["created"]:
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


_MEETING_EXTRA_COLUMNS = (
    ", lead_kind, phone_e164, messenger_pref, utm, source_tag, meeting_id, person_lead_id"
)
_MEETING_EXTRA_VALUES = (
    ", 'meeting', %(phone_e164)s, %(messenger)s, %(utm)s::jsonb, %(source_tag)s, %(meeting_id)s, %(person_lead_id)s::uuid"
)


async def _meeting_save_plan(
    conn: Any, lead: LeadInput, query: str, params: dict[str, Any]
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Запись на встречу до вставки: встреча (живая, запланированная, в будущем — иначе без
    привязки и с пометкой в metadata) и «первый партнёр навсегда» по телефону.

    Возвращает запрос и параметры с дополнительными колонками и строку встречи."""
    organic_owner_id = str(params["organic_owner_id"])
    meeting_row, rejected = await lookup_meeting(conn, lead.tenant_id, lead.meeting_id)
    person_lead_id, locked_owner_id = await find_person_anchor(
        conn, lead.tenant_id, str(lead.phone_e164), organic_owner_id
    )
    metadata = dict(lead.metadata)
    if lead.meeting_id is not None and meeting_row is None:
        metadata["meeting_id_requested"] = lead.meeting_id
        metadata["meeting_id_rejected"] = rejected
    if locked_owner_id:
        metadata["owner_locked_by_phone"] = True
    plan = {
        **params,
        "metadata": json.dumps(metadata, ensure_ascii=False),
        "locked_owner_id": locked_owner_id,
        "owner_reason": (
            "First partner kept: the same phone already has a lead" if locked_owner_id else params["owner_reason"]
        ),
        "phone_e164": lead.phone_e164,
        "messenger": lead.messenger,
        "utm": json.dumps(lead.utm, ensure_ascii=False),
        "source_tag": lead.source_tag,
        "meeting_id": int(meeting_row["meeting_id"]) if meeting_row else None,
        "person_lead_id": person_lead_id,
    }
    query = query.replace("/*EXTRA_COLUMNS*/", _MEETING_EXTRA_COLUMNS).replace("/*EXTRA_VALUES*/", _MEETING_EXTRA_VALUES)
    return query, plan, meeting_row


async def _after_meeting_lead(
    conn: Any, lead: LeadInput, row: dict[str, Any], meeting_row: dict[str, Any] | None
) -> None:
    """Новая запись на встречу: карточка в WWC CRM владельца (источник «встреча», заметка,
    метки «записан дд.мм» и «бот не открыл») и сообщение партнёру в Telegram через
    platform_outbox. Событие lead_created для n8n НЕ ставится: партнёру пишет само Core,
    иначе он получил бы два сообщения. Сбои карточки и уведомления заявку не роняют."""
    starts_at = meeting_row["starts_at"] if meeting_row else None
    origin = origin_text(
        ref_code=row.get("active_ref_code") or row.get("first_ref_code"),
        source_tag=lead.source_tag,
        utm_source=lead.utm.get("source"),
    )
    phone = str(lead.phone_e164)
    card_id = await add_lead_card(
        conn,
        tenant_id=lead.tenant_id,
        lead_id=str(row["lead_id"]),
        owner_actor_id=row.get("assigned_owner_id"),
        name=lead.name,
        contact=lead.contact,
        product_name=lead.product_name,
        source=CARD_SOURCE,
        note=card_note(phone_e164=phone, messenger=lead.messenger, starts_at=starts_at, origin=origin),
        tags=card_tags(starts_at),
        phone_e164=phone,
        phone_raw=phone,
    )
    await notify_partner(
        conn,
        tenant_id=lead.tenant_id,
        lead_id=str(row["lead_id"]),
        owner_actor_id=row.get("assigned_owner_id"),
        name=lead.name,
        phone_e164=phone,
        messenger=lead.messenger,
        starts_at=starts_at,
        origin=origin,
        page_url=lead.page_url,
        card_id=card_id,
    )


async def validate_lead_shadow(lead: LeadInput) -> dict[str, Any]:
    organic_owner_id = get_settings().platform_organic_owner_id
    async with tenant_connection(lead.tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            select ref_code, owner_id
            from referral_profiles
            where tenant_id = %s and ref_code = %s and enabled = true
              and exists (
                select 1
                from partner_subscriptions ps
                where ps.tenant_id = referral_profiles.tenant_id
                  and ps.ref_code = referral_profiles.ref_code
                  and partner_subscription_state(ps.paid_until, now()) in ('active', 'grace')
              )
            limit 1
            """,
            (lead.tenant_id, lead.first_ref_code or lead.active_ref_code),
        )
    return {
        "valid": True,
        "ref_found": bool(row),
        "would_assign_owner": row["owner_id"] if row else organic_owner_id,
    }
