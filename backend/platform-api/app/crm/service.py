"""Partner diary storage: accounts, contacts, notes, export, the site-lead card.

Every query runs in ``tenant_connection`` (RLS by ``app.tenant_id``) and names
the tenant and the account explicitly: a contact of another account is simply
not found (404), never «forbidden». Local dates come from Postgres
(``now() at time zone <account timezone>``) — the image does not need tzdata.
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

from app.academy.service import preview_admin_ids
from app.crm.rules import (
    EXPORT_HEADER,
    STATUSES,
    STATUS_TITLES,
    STEP_TITLES,
    CrmRuleError,
    CrmViewer,
    access_lock_reason,
    clean_name,
    clean_note,
    clean_source,
    export_row,
    group_today,
    lead_note_text,
    looks_like_timezone,
    phone_from_lead_contact,
    plan_for_patch,
    split_phone,
)
from app.db import fetch_all, fetch_one, tenant_connection
from app.schema_requirements import parse_disabled_features
from app.settings import get_settings
from app.subscriptions.service import (
    resolve_partner_hostname,
    resolve_partner_subscription_by_telegram_user_id,
)

logger = logging.getLogger(__name__)

CONTACTS_LIMIT = 300
TODAY_LIMIT = 500

_CONTACT_COLUMNS = """
    c.contact_id::text as contact_id, c.name, c.phone_e164, c.phone_raw, c.source,
    c.status, c.next_step, c.next_at, c.meeting_at, c.lead_id::text as lead_id,
    c.created_at, c.updated_at
"""


class CrmError(Exception):
    def __init__(self, code: str, status: int, **extra: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.extra = extra


def crm_feature_enabled() -> bool:
    try:
        return "crm" not in parse_disabled_features(get_settings().disabled_features)
    except ValueError:
        return False


# ---- who is looking -----------------------------------------------------------


async def load_viewer(tenant_id: str, telegram_user_id: int) -> CrmViewer:
    subscription = await resolve_partner_subscription_by_telegram_user_id(
        tenant_id, int(telegram_user_id), on_ambiguous="best"
    )
    return CrmViewer(
        telegram_user_id=int(telegram_user_id),
        is_preview_admin=int(telegram_user_id) in preview_admin_ids(),
        partner_paid=bool(subscription and subscription.get("partner_paid")),
        ref_code=(subscription or {}).get("ref_code"),
        public_profile=(subscription or {}).get("public_profile"),
    )


def lock_reason(viewer: CrmViewer) -> str | None:
    return access_lock_reason(viewer, get_settings().parsed_crm_pilot_telegram_ids())


def site_host(viewer: CrmViewer) -> str:
    """The partner's own site (the cookie of `.wwc.best` works there too)."""
    if viewer.ref_code:
        try:
            return resolve_partner_hostname(viewer.ref_code, viewer.public_profile)
        except Exception:  # reserved/invalid subdomain → the main site
            logger.info("crm_partner_host_fallback", extra={"ref_code": viewer.ref_code})
    base = str(get_settings().platform_academy_site_base or "https://wwc.best")
    return urlsplit(base).hostname or "wwc.best"


def crm_url(viewer: CrmViewer, fragment: str = "") -> str:
    return f"https://{site_host(viewer)}/crm/" + (f"#{fragment}" if fragment else "")


# ---- account ----------------------------------------------------------------------


def _account_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": row["account_id"],
        "telegram_user_id": int(row["telegram_user_id"]),
        "timezone": row["timezone"],
        "today": row["today"],
    }


async def _select_account(conn: Any, tenant_id: str, telegram_user_id: int) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select account_id::text as account_id, telegram_user_id, timezone,
               (now() at time zone timezone)::date as today
        from platform_accounts
        where tenant_id = %s and telegram_user_id = %s
        """,
        (tenant_id, int(telegram_user_id)),
    )


async def get_or_create_account(tenant_id: str, telegram_user_id: int) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        row = await _select_account(conn, tenant_id, telegram_user_id)
        if row is None:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    insert into platform_accounts (tenant_id, telegram_user_id)
                    values (%s, %s)
                    on conflict (tenant_id, telegram_user_id) do nothing
                    """,
                    (tenant_id, int(telegram_user_id)),
                )
            row = await _select_account(conn, tenant_id, telegram_user_id)
    if row is None:  # pragma: no cover - insert + select in one transaction
        raise CrmError("account_unavailable", 503)
    return _account_out(row)


async def set_timezone(tenant_id: str, account: dict[str, Any], timezone_name: Any) -> dict[str, Any]:
    name = str(timezone_name or "").strip()
    if not looks_like_timezone(name):
        raise CrmError("invalid_timezone", 400)
    async with tenant_connection(tenant_id) as conn:
        known = await fetch_one(
            conn, "select exists (select 1 from pg_timezone_names where name = %s) as ok", (name,)
        )
        if not known or not known["ok"]:
            raise CrmError("invalid_timezone", 400)
        row = await fetch_one(
            conn,
            """
            update platform_accounts
            set timezone = %s, updated_at = now()
            where tenant_id = %s and account_id = %s::uuid
            returning account_id::text as account_id, telegram_user_id, timezone,
                      (now() at time zone timezone)::date as today
            """,
            (name, tenant_id, account["account_id"]),
        )
    if row is None:
        raise CrmError("account_unavailable", 503)
    return _account_out(row)


# ---- contacts -----------------------------------------------------------------------


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def contact_out(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row["status"])
    step = row.get("next_step")
    return {
        "id": row["contact_id"],
        "name": row["name"],
        "phone": row.get("phone_e164") or row.get("phone_raw") or "",
        "phone_e164": row.get("phone_e164"),
        "phone_raw": row.get("phone_raw"),
        "source": row.get("source") or "",
        "status": status,
        "status_title": STATUS_TITLES.get(status, status),
        "next_step": step,
        "next_step_title": STEP_TITLES.get(step) if step else None,
        "next_at": _iso(row.get("next_at")),
        "meeting_at": _iso(row.get("meeting_at")),
        "from_site": bool(row.get("lead_id")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def note_out(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["note_id"], "body": row["body"], "created_at": _iso(row["created_at"])}


def _contact_uuid(contact_id: str) -> str:
    try:
        return str(uuid.UUID(str(contact_id)))
    except (TypeError, ValueError):
        raise CrmError("contact_not_found", 404) from None


async def _load_contact(conn: Any, tenant_id: str, account_id: str, contact_id: str) -> dict[str, Any]:
    row = await fetch_one(
        conn,
        f"""
        select {_CONTACT_COLUMNS}
        from crm_contacts c
        where c.tenant_id = %s and c.account_id = %s::uuid and c.contact_id = %s::uuid
        """,
        (tenant_id, account_id, _contact_uuid(contact_id)),
    )
    if row is None:
        raise CrmError("contact_not_found", 404)
    return row


async def _duplicate_of(
    conn: Any, tenant_id: str, account_id: str, phone_e164: str | None, *, except_id: str | None = None
) -> str | None:
    if not phone_e164:
        return None
    row = await fetch_one(
        conn,
        """
        select contact_id::text as contact_id
        from crm_contacts
        where tenant_id = %s and account_id = %s::uuid and phone_e164 = %s
          and (%s::uuid is null or contact_id <> %s::uuid)
        order by created_at
        limit 1
        """,
        (tenant_id, account_id, phone_e164, except_id, except_id),
    )
    return row["contact_id"] if row else None


async def today_view(tenant_id: str, account: dict[str, Any]) -> dict[str, Any]:
    today = account["today"]
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            f"""
            select {_CONTACT_COLUMNS}
            from crm_contacts c
            where c.tenant_id = %s and c.account_id = %s::uuid
              and c.next_at is not null and c.next_at <= %s
            order by c.next_at, c.updated_at desc
            limit %s
            """,
            (tenant_id, account["account_id"], today, TODAY_LIMIT),
        )
    view = group_today(rows, today)
    for group in view["groups"]:
        group["contacts"] = [contact_out(row) for row in group["contacts"]]
    return view


async def list_contacts(
    tenant_id: str, account: dict[str, Any], *, q: str | None = None, status: str | None = None
) -> list[dict[str, Any]]:
    status_filter = (status or "").strip() or None
    if status_filter is not None and status_filter not in STATUSES:
        raise CrmError("invalid_status", 400)
    needle = " ".join(str(q or "").split()).lower()[:100]
    digits = "".join(ch for ch in needle if ch.isdigit())
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            f"""
            select {_CONTACT_COLUMNS}
            from crm_contacts c
            where c.tenant_id = %(tenant_id)s and c.account_id = %(account_id)s::uuid
              and (%(status)s::text is null or c.status = %(status)s::text)
              and (
                %(needle)s::text = ''
                or strpos(lower(c.name), %(needle)s::text) > 0
                or strpos(lower(c.source), %(needle)s::text) > 0
                or (length(%(digits)s::text) >= 3 and (
                      strpos(coalesce(c.phone_e164, ''), %(digits)s::text) > 0
                   or strpos(regexp_replace(coalesce(c.phone_raw, ''), '[^0-9]', '', 'g'), %(digits)s::text) > 0))
              )
            order by c.updated_at desc
            limit %(limit)s
            """,
            {
                "tenant_id": tenant_id,
                "account_id": account["account_id"],
                "status": status_filter,
                "needle": needle,
                "digits": digits,
                "limit": CONTACTS_LIMIT,
            },
        )
    return [contact_out(row) for row in rows]


async def create_contact(
    tenant_id: str, account: dict[str, Any], *, name: Any, phone: Any = None, source: Any = None
) -> dict[str, Any]:
    try:
        clean = clean_name(name)
    except CrmRuleError as exc:
        raise CrmError(exc.code, exc.status) from exc
    phone_e164, phone_raw = split_phone(phone)
    async with tenant_connection(tenant_id) as conn:
        duplicate = await _duplicate_of(conn, tenant_id, account["account_id"], phone_e164)
        if duplicate:
            raise CrmError("duplicate", 409, contact_id=duplicate)
        row = await fetch_one(
            conn,
            f"""
            with created as (
              insert into crm_contacts (
                tenant_id, account_id, name, phone_e164, phone_raw, source,
                status, next_step, next_at
              )
              values (%s, %s::uuid, %s, %s, %s, %s, 'new', 'invite', %s)
              returning *
            )
            select {_CONTACT_COLUMNS} from created c
            """,
            (
                tenant_id,
                account["account_id"],
                clean,
                phone_e164,
                phone_raw,
                clean_source(source),
                account["today"],
            ),
        )
    return contact_out(row)


async def get_contact(tenant_id: str, account: dict[str, Any], contact_id: str) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        row = await _load_contact(conn, tenant_id, account["account_id"], contact_id)
        notes = await fetch_all(
            conn,
            """
            select note_id::text as note_id, body, created_at
            from crm_notes
            where tenant_id = %s and contact_id = %s::uuid
            order by created_at desc, note_id
            """,
            (tenant_id, row["contact_id"]),
        )
    return {**contact_out(row), "notes": [note_out(note) for note in notes]}


async def _meeting_local(
    conn: Any, timezone_name: str, meeting_at: datetime
) -> tuple[datetime, date]:
    """Stored timestamptz + its date in the account timezone. A time without an
    offset is the account's local time."""
    if meeting_at.tzinfo is None:
        row = await fetch_one(
            conn,
            """
            select m as meeting_at, (m at time zone %(tz)s)::date as meeting_date
            from (select (%(ts)s::timestamp at time zone %(tz)s) as m) x
            """,
            {"ts": meeting_at, "tz": timezone_name},
        )
    else:
        row = await fetch_one(
            conn,
            "select %(ts)s::timestamptz as meeting_at, (%(ts)s::timestamptz at time zone %(tz)s)::date as meeting_date",
            {"ts": meeting_at, "tz": timezone_name},
        )
    return row["meeting_at"], row["meeting_date"]


async def update_contact(
    tenant_id: str, account: dict[str, Any], contact_id: str, changes: dict[str, Any]
) -> dict[str, Any]:
    """``changes`` holds only the fields the client sent (null clears a field)."""
    async with tenant_connection(tenant_id) as conn:
        current = await _load_contact(conn, tenant_id, account["account_id"], contact_id)
        values: dict[str, Any] = {}
        try:
            if "name" in changes:
                values["name"] = clean_name(changes["name"])
            if "source" in changes:
                values["source"] = clean_source(changes["source"])
            if "phone" in changes:
                phone_e164, phone_raw = split_phone(changes["phone"])
                duplicate = await _duplicate_of(
                    conn, tenant_id, account["account_id"], phone_e164, except_id=current["contact_id"]
                )
                if duplicate:
                    raise CrmError("duplicate", 409, contact_id=duplicate)
                values["phone_e164"], values["phone_raw"] = phone_e164, phone_raw

            meeting_given = "meeting_at" in changes
            meeting_date: date | None = None
            if meeting_given and changes["meeting_at"] is not None:
                values["meeting_at"], meeting_date = await _meeting_local(
                    conn, account["timezone"], changes["meeting_at"]
                )
            elif meeting_given:
                values["meeting_at"] = None
            elif current.get("meeting_at") is not None:
                meeting_row = await fetch_one(
                    conn,
                    "select (%s::timestamptz at time zone %s)::date as d",
                    (current["meeting_at"], account["timezone"]),
                )
                meeting_date = meeting_row["d"] if meeting_row else None

            new_status = changes.get("status") if "status" in changes else None
            if "status" in changes and new_status not in STATUSES:
                raise CrmRuleError("invalid_status")
            plan = plan_for_patch(
                current_status=current["status"],
                new_status=new_status,
                today=account["today"],
                meeting_date=meeting_date,
                meeting_given=meeting_given,
                next_step=changes.get("next_step"),
                next_step_given="next_step" in changes,
                next_at=changes.get("next_at"),
                next_at_given="next_at" in changes,
                current_next_step=current.get("next_step"),
                current_next_at=current.get("next_at"),
            )
        except CrmRuleError as exc:
            raise CrmError(exc.code, exc.status) from exc
        if new_status is not None:
            values["status"] = new_status
        values["next_step"], values["next_at"] = plan.next_step, plan.next_at

        assignments = ", ".join(f"{column} = %({column})s" for column in values)
        row = await fetch_one(
            conn,
            f"""
            with changed as (
              update crm_contacts
              set {assignments}, updated_at = now()
              where tenant_id = %(tenant_id)s and account_id = %(account_id)s::uuid
                and contact_id = %(contact_id)s::uuid
              returning *
            )
            select {_CONTACT_COLUMNS} from changed c
            """,
            {
                **values,
                "tenant_id": tenant_id,
                "account_id": account["account_id"],
                "contact_id": current["contact_id"],
            },
        )
    if row is None:
        raise CrmError("contact_not_found", 404)
    return contact_out(row)


async def delete_contact(tenant_id: str, account: dict[str, Any], contact_id: str) -> None:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            delete from crm_contacts
            where tenant_id = %s and account_id = %s::uuid and contact_id = %s::uuid
            returning contact_id
            """,
            (tenant_id, account["account_id"], _contact_uuid(contact_id)),
        )
    if row is None:
        raise CrmError("contact_not_found", 404)


async def add_note(tenant_id: str, account: dict[str, Any], contact_id: str, body: Any) -> dict[str, Any]:
    try:
        text = clean_note(body)
    except CrmRuleError as exc:
        raise CrmError(exc.code, exc.status) from exc
    async with tenant_connection(tenant_id) as conn:
        contact = await _load_contact(conn, tenant_id, account["account_id"], contact_id)
        row = await fetch_one(
            conn,
            """
            insert into crm_notes (tenant_id, contact_id, body)
            values (%s, %s::uuid, %s)
            returning note_id::text as note_id, body, created_at
            """,
            (tenant_id, contact["contact_id"], text),
        )
        async with conn.cursor() as cur:
            await cur.execute(
                "update crm_contacts set updated_at = now() where tenant_id = %s and contact_id = %s::uuid",
                (tenant_id, contact["contact_id"]),
            )
    return note_out(row)


async def delete_note(tenant_id: str, account: dict[str, Any], contact_id: str, note_id: str) -> None:
    async with tenant_connection(tenant_id) as conn:
        contact = await _load_contact(conn, tenant_id, account["account_id"], contact_id)
        row = await fetch_one(
            conn,
            """
            delete from crm_notes
            where tenant_id = %s and contact_id = %s::uuid and note_id = %s::uuid
            returning note_id
            """,
            (tenant_id, contact["contact_id"], _contact_uuid(note_id)),
        )
    if row is None:
        raise CrmError("note_not_found", 404)


async def export_csv(tenant_id: str, account: dict[str, Any]) -> str:
    """UTF-8 with BOM, «;» between columns: Excel with a Russian locale opens it
    as a table with Cyrillic intact."""
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select c.name, c.phone_e164, c.phone_raw, c.source, c.status, c.next_step, c.next_at,
                   coalesce(
                     string_agg(
                       to_char(n.created_at at time zone %(tz)s, 'DD.MM.YYYY') || ': ' || n.body,
                       E'\\n' order by n.created_at desc
                     ),
                     ''
                   ) as notes
            from crm_contacts c
            left join crm_notes n on n.tenant_id = c.tenant_id and n.contact_id = c.contact_id
            where c.tenant_id = %(tenant_id)s and c.account_id = %(account_id)s::uuid
            group by c.contact_id
            order by c.name, c.created_at
            """,
            {"tenant_id": tenant_id, "account_id": account["account_id"], "tz": account["timezone"]},
        )
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(EXPORT_HEADER)
    for row in rows:
        writer.writerow(export_row(row))
    return "﻿" + buffer.getvalue()


# ---- site lead → card (called from app.leads.service.save_lead) ---------------------


def _lead_card_name(name: Any, contact: Any) -> str:
    for candidate in (name, contact, "Заявка с сайта"):
        try:
            return clean_name(candidate)
        except CrmRuleError:
            continue
    return "Заявка с сайта"  # pragma: no cover


async def add_lead_card(
    conn: Any,
    *,
    tenant_id: str,
    lead_id: str,
    owner_actor_id: str | None,
    name: Any,
    contact: Any,
    product_name: Any = None,
    comment: Any = None,
) -> str | None:
    """A new site lead becomes a «Новый контакт» card of its owner — if the owner
    has already opened the diary (has a platform_accounts row). Runs inside the
    lead's transaction under a savepoint: any failure here is logged and the
    lead is saved as before. Returns the card id or None."""
    if not owner_actor_id or not crm_feature_enabled():
        return None
    try:
        async with conn.transaction():
            account = await fetch_one(
                conn,
                """
                select a.account_id::text as account_id, (now() at time zone a.timezone)::date as today
                from lead_actors la
                join platform_accounts a
                  on a.tenant_id = la.tenant_id and a.telegram_user_id = la.telegram_user_id
                where la.tenant_id = %s and la.actor_id = %s and la.telegram_user_id is not null
                limit 1
                """,
                (tenant_id, owner_actor_id),
            )
            if account is None:
                return None
            phone_e164, phone_raw = phone_from_lead_contact(contact)
            note = lead_note_text(
                contact=contact, product_name=product_name, comment=comment, phone_known=phone_e164 is not None
            )
            existing = await _duplicate_of(conn, tenant_id, account["account_id"], phone_e164)
            if existing:
                # Человек уже в ежедневнике — новая заявка становится заметкой, без дубля.
                contact_id = existing
            else:
                created = await fetch_one(
                    conn,
                    """
                    insert into crm_contacts (
                      tenant_id, account_id, name, phone_e164, phone_raw, source,
                      status, next_step, next_at, lead_id
                    )
                    values (%s, %s::uuid, %s, %s, %s, 'сайт', 'new', 'invite', %s, %s::uuid)
                    on conflict (tenant_id, lead_id) do nothing
                    returning contact_id::text as contact_id
                    """,
                    (
                        tenant_id,
                        account["account_id"],
                        _lead_card_name(name, contact),
                        phone_e164,
                        phone_raw,
                        account["today"],
                        lead_id,
                    ),
                )
                if created is None:
                    return None
                contact_id = created["contact_id"]
            async with conn.cursor() as cur:
                await cur.execute(
                    "insert into crm_notes (tenant_id, contact_id, body) values (%s, %s::uuid, %s)",
                    (tenant_id, contact_id, note),
                )
            return contact_id
    except Exception:
        logger.exception("crm_lead_card_failed", extra={"tenant_id": tenant_id, "lead_id": str(lead_id)})
        return None
