"""WWC CRM storage: accounts, contacts, notes, the card history, export, the site-lead card.

Every query runs in ``tenant_connection`` (RLS by ``app.tenant_id``) and names
the tenant and the account explicitly: a contact of another account is simply
not found (404), never «forbidden». Local dates come from Postgres
(``now() at time zone <account timezone>``) — the image does not need tzdata.

CRM v2 (V20): a deleted card is only marked (``deleted_at``) and can be restored
for RESTORE_WINDOW; every read skips it, the worker erases it afterwards. Each
change a person makes writes one row of the card history (``crm_activities``)
in the same transaction. Lists with cursors live in ``app.crm.queries``,
message templates in ``app.crm.templates``.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit

from app.academy.service import preview_admin_ids
from app.crm.rules import (
    EXPORT_HEADER,
    RESTORE_WINDOW,
    STATUSES,
    STATUS_TITLES,
    STEP_TITLES,
    CrmRuleError,
    CrmViewer,
    access_lock_reason,
    clean_log,
    clean_name,
    clean_note,
    clean_priority,
    clean_source,
    clean_tags,
    export_row,
    group_today,
    lead_note_text,
    looks_like_timezone,
    phone_from_lead_contact,
    plan_done,
    plan_for_patch,
    snooze_until,
    split_phone,
    today_sections,
)
from app.db import fetch_all, fetch_one, tenant_connection
from app.schema_requirements import parse_disabled_features
from app.settings import get_settings
from app.subscriptions.service import (
    resolve_partner_hostname,
    resolve_partner_subscription_by_telegram_user_id,
    subscription_state,
)

logger = logging.getLogger(__name__)

TODAY_LIMIT = 500
BULK_LIMIT = 500  # contacts per import request (phone book, .vcf, .csv)

CONTACT_COLUMNS = """
    c.contact_id::text as contact_id, c.name, c.phone_e164, c.phone_raw, c.source,
    c.status, c.next_step, c.next_at, c.meeting_at, c.lead_id::text as lead_id,
    c.tags, c.priority, c.last_touch_at, c.created_at, c.updated_at
"""
# Kept for callers of v1 (the name had a leading underscore there).
_CONTACT_COLUMNS = CONTACT_COLUMNS


class CrmError(Exception):
    def __init__(self, code: str, status: int, **extra: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.extra = extra


def safe_error(exc: BaseException) -> dict[str, Any]:
    """What may go to the logs: the class and SQLSTATE, never the message — a
    psycopg message carries the failing row (a person's name and phone)."""
    return {"error_class": type(exc).__name__, "sqlstate": getattr(exc, "sqlstate", None)}


def crm_feature_enabled() -> bool:
    try:
        return "crm" not in parse_disabled_features(get_settings().disabled_features)
    except ValueError:
        return False


def _rule(exc: CrmRuleError) -> CrmError:
    return CrmError(exc.code, exc.status)


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


def viewer_from_row(telegram_user_id: int, ref_code: Any, public_profile: Any, paid_until: Any) -> CrmViewer:
    """The same viewer as load_viewer, from a row the caller already has (digest)."""
    state = subscription_state(paid_until) if ref_code else "no_subscription"
    return CrmViewer(
        telegram_user_id=int(telegram_user_id),
        is_preview_admin=int(telegram_user_id) in preview_admin_ids(),
        partner_paid=state in {"active", "grace"},
        ref_code=ref_code,
        public_profile=public_profile,
    )


def lock_reason(viewer: CrmViewer) -> str | None:
    return access_lock_reason(viewer, get_settings().parsed_crm_pilot())


def viewer_display_name(viewer: CrmViewer) -> str:
    """The partner's public name for «{мое_имя}» in templates; '' when unknown."""
    profile = viewer.public_profile if isinstance(viewer.public_profile, dict) else {}
    return " ".join(str(profile.get("display_name") or "").split())[:100]


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


def crm_contact_url(viewer: CrmViewer, contact_id: str) -> str:
    """A card from the bot. The id rides in the query too: a sign-in link replaces
    the fragment with #wwc-login=…, and after signing in the site keeps only
    path + query, so ``#contact/<id>`` alone would be lost."""
    return f"https://{site_host(viewer)}/crm/?contact={contact_id}#contact/{contact_id}"


# ---- account ----------------------------------------------------------------------

_ACCOUNT_COLUMNS = """
    account_id::text as account_id, telegram_user_id, timezone,
    (now() at time zone timezone)::date as today,
    crm_install_hint_dismissed_at is not null as install_hint_dismissed
"""


def _account_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": row["account_id"],
        "telegram_user_id": int(row["telegram_user_id"]),
        "timezone": row["timezone"],
        "today": row["today"],
        "install_hint_dismissed": bool(row.get("install_hint_dismissed")),
    }


async def _select_account(conn: Any, tenant_id: str, telegram_user_id: int) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        f"""
        select {_ACCOUNT_COLUMNS}
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


async def update_account(
    tenant_id: str,
    account: dict[str, Any],
    *,
    timezone_name: Any = None,
    install_hint_dismissed: bool | None = None,
) -> dict[str, Any]:
    """PATCH /me: the timezone (an IANA name Postgres knows) and/or the
    «Установить приложение» banner flag. Only what is given changes."""
    assignments: list[str] = []
    params: dict[str, Any] = {"tenant_id": tenant_id, "account_id": account["account_id"]}
    async with tenant_connection(tenant_id) as conn:
        if timezone_name is not None:
            name = str(timezone_name or "").strip()
            if not looks_like_timezone(name):
                raise CrmError("invalid_timezone", 400)
            known = await fetch_one(
                conn, "select exists (select 1 from pg_timezone_names where name = %s) as ok", (name,)
            )
            if not known or not known["ok"]:
                raise CrmError("invalid_timezone", 400)
            assignments.append("timezone = %(timezone)s")
            params["timezone"] = name
        if install_hint_dismissed is not None:
            assignments.append(
                "crm_install_hint_dismissed_at = case when %(dismissed)s "
                "then coalesce(crm_install_hint_dismissed_at, now()) end"
            )
            params["dismissed"] = bool(install_hint_dismissed)
        if not assignments:
            raise CrmError("nothing_to_update", 400)
        row = await fetch_one(
            conn,
            f"""
            update platform_accounts
            set {", ".join(assignments)}, updated_at = now()
            where tenant_id = %(tenant_id)s and account_id = %(account_id)s::uuid
            returning {_ACCOUNT_COLUMNS}
            """,
            params,
        )
    if row is None:
        raise CrmError("account_unavailable", 503)
    return _account_out(row)


async def set_timezone(tenant_id: str, account: dict[str, Any], timezone_name: Any) -> dict[str, Any]:
    if timezone_name is None:
        raise CrmError("invalid_timezone", 400)
    return await update_account(tenant_id, account, timezone_name=timezone_name)


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
        "tags": list(row.get("tags") or []),
        "priority": int(row.get("priority") or 0),
        "last_touch_at": _iso(row.get("last_touch_at")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def note_out(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["note_id"], "body": row["body"], "created_at": _iso(row["created_at"])}


def activity_out(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    out = {
        "id": row["activity_id"],
        "kind": row["kind"],
        "payload": payload,
        "created_at": _iso(row.get("created_at")),
    }
    if row["kind"] == "note":
        out["note"] = {"id": payload.get("note_id"), "body": row.get("note_body")}
    return out


def _uuid_or_404(value: str, code: str = "contact_not_found") -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        raise CrmError(code, 404) from None


def _contact_uuid(contact_id: str) -> str:
    return _uuid_or_404(contact_id)


async def load_contact(
    conn: Any, tenant_id: str, account_id: str, contact_id: str, *, for_update: bool = False
) -> dict[str, Any]:
    """A live card of this account (404 for another account's, a deleted one, a bad id)."""
    row = await fetch_one(
        conn,
        f"""
        select {CONTACT_COLUMNS}
        from crm_contacts c
        where c.tenant_id = %s and c.account_id = %s::uuid and c.contact_id = %s::uuid
          and c.deleted_at is null
        {"for update" if for_update else ""}
        """,
        (tenant_id, account_id, _contact_uuid(contact_id)),
    )
    if row is None:
        raise CrmError("contact_not_found", 404)
    return row


_load_contact = load_contact


async def add_activity(
    conn: Any,
    *,
    tenant_id: str,
    account_id: str,
    contact_id: str,
    kind: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """One history row. ``clock_timestamp()``: two rows of one transaction keep their order."""
    return await fetch_one(
        conn,
        """
        insert into crm_activities (tenant_id, account_id, contact_id, kind, payload, created_at)
        values (%s, %s::uuid, %s::uuid, %s, %s::jsonb, clock_timestamp())
        on conflict do nothing
        returning activity_id::text as activity_id, kind, payload, created_at
        """,
        (tenant_id, account_id, contact_id, kind, json.dumps(payload or {}, ensure_ascii=False)),
    )


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
          and deleted_at is null
          and (%s::uuid is null or contact_id <> %s::uuid)
        order by created_at
        limit 1
        """,
        (tenant_id, account_id, phone_e164, except_id, except_id),
    )
    return row["contact_id"] if row else None


async def today_view(tenant_id: str, account: dict[str, Any]) -> dict[str, Any]:
    """«Сегодня»: ``groups`` by step (v1 site) and ``sections`` by meaning —
    meetings today, call, remind, overdue (v2 app, the same split as the bot's
    morning message). Starred cards first."""
    today = account["today"]
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            f"""
            select {CONTACT_COLUMNS},
                   coalesce(c.meeting_at >= (%(today)s::date)::timestamp at time zone %(tz)s
                        and c.meeting_at < (%(today)s::date + 1)::timestamp at time zone %(tz)s, false)
                     as meeting_today
            from crm_contacts c
            where c.tenant_id = %(tenant_id)s and c.account_id = %(account_id)s::uuid
              and c.deleted_at is null
              and (
                (c.next_at is not null and c.next_at <= %(today)s)
                or (c.meeting_at >= (%(today)s::date)::timestamp at time zone %(tz)s
                    and c.meeting_at < (%(today)s::date + 1)::timestamp at time zone %(tz)s)
              )
            order by c.priority desc, c.next_at nulls last, c.updated_at desc
            limit %(limit)s
            """,
            {
                "tenant_id": tenant_id,
                "account_id": account["account_id"],
                "today": today,
                "tz": account["timezone"],
                "limit": TODAY_LIMIT,
            },
        )
    view = group_today(rows, today)
    for group in view["groups"]:
        group["contacts"] = [contact_out(row) for row in group["contacts"]]
    view["sections"] = [
        {**section, "contacts": [contact_out(row) for row in section["contacts"]]}
        for section in today_sections(rows, today)
    ]
    return view


async def create_contact(
    tenant_id: str,
    account: dict[str, Any],
    *,
    name: Any,
    phone: Any = None,
    source: Any = None,
    tags: Any = None,
    priority: Any = None,
) -> dict[str, Any]:
    try:
        clean = clean_name(name)
        clean_tag_list = clean_tags(tags)
        star = clean_priority(priority) if priority is not None else 0
    except CrmRuleError as exc:
        raise _rule(exc) from exc
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
                status, next_step, next_at, tags, priority
              )
              values (%s, %s::uuid, %s, %s, %s, %s, 'new', 'invite', %s, %s::text[], %s)
              returning *
            )
            select {CONTACT_COLUMNS} from created c
            """,
            (
                tenant_id,
                account["account_id"],
                clean,
                phone_e164,
                phone_raw,
                clean_source(source),
                account["today"],
                clean_tag_list,
                star,
            ),
        )
        await add_activity(
            conn,
            tenant_id=tenant_id,
            account_id=account["account_id"],
            contact_id=row["contact_id"],
            kind="created",
            payload={"source": "manual"},
        )
    return contact_out(row)


async def _existing_by_phone(
    conn: Any, tenant_id: str, account_id: str, phones: list[str]
) -> dict[str, str]:
    """phone_e164 → the oldest live card of this account with that number."""
    if not phones:
        return {}
    rows = await fetch_all(
        conn,
        """
        select phone_e164, contact_id::text as contact_id
        from crm_contacts
        where tenant_id = %s and account_id = %s::uuid and phone_e164 = any(%s::text[])
          and deleted_at is null
        order by created_at, contact_id
        """,
        (tenant_id, account_id, phones),
    )
    found: dict[str, str] = {}
    for row in rows:
        found.setdefault(row["phone_e164"], row["contact_id"])
    return found


async def bulk_create_contacts(
    tenant_id: str, account: dict[str, Any], items: list[dict[str, Any]]
) -> dict[str, Any]:
    """Import from the phone book, a .vcf or a .csv: one transaction for the batch.

    Each item is ``{name, phone?, source?}`` cleaned like a single create. A row
    whose number is already in this account's diary — or earlier in the same
    batch — is skipped and reported with the existing card; an empty name is
    skipped with ``name_required``. Rows without a recognised number are never
    duplicates of each other. A row the database refuses raises and rolls the
    whole batch back (the route answers 400): nothing is half-imported.
    """
    if len(items) > BULK_LIMIT:
        raise CrmError("too_many_contacts", 400, limit=BULK_LIMIT)
    cleaned: list[dict[str, Any]] = []
    for item in items:
        phone_e164, phone_raw = split_phone(item.get("phone"))
        try:
            name, error = clean_name(item.get("name")), None
        except CrmRuleError as exc:
            name, error = "", exc.code
        cleaned.append({
            "name": name, "error": error, "phone_e164": phone_e164, "phone_raw": phone_raw,
            "source": clean_source(item.get("source")),
        })
    phones = sorted({row["phone_e164"] for row in cleaned if row["phone_e164"]})

    skipped: list[dict[str, Any]] = []
    created: list[tuple[str, dict[str, Any]]] = []
    async with tenant_connection(tenant_id) as conn:
        seen = await _existing_by_phone(conn, tenant_id, account["account_id"], phones)
        for row in cleaned:
            shown_phone = row["phone_raw"] or ""
            if row["error"]:
                skipped.append({"name": row["name"], "phone": shown_phone, "contact_id": None, "reason": row["error"]})
                continue
            phone = row["phone_e164"]
            if phone and phone in seen:
                skipped.append({"name": row["name"], "phone": shown_phone, "contact_id": seen[phone], "reason": "duplicate"})
                continue
            contact_id = str(uuid.uuid4())
            if phone:
                seen[phone] = contact_id
            created.append((contact_id, row))
        if created:
            ids = [contact_id for contact_id, _ in created]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    insert into crm_contacts (
                      tenant_id, contact_id, account_id, name, phone_e164, phone_raw, source,
                      status, next_step, next_at
                    )
                    select %(tenant_id)s, t.contact_id::uuid, %(account_id)s::uuid,
                           t.name, t.phone_e164, t.phone_raw, t.source, 'new', 'invite', %(today)s
                    from unnest(
                      %(ids)s::text[], %(names)s::text[], %(e164)s::text[], %(raws)s::text[], %(sources)s::text[]
                    ) as t(contact_id, name, phone_e164, phone_raw, source)
                    """,
                    {
                        "tenant_id": tenant_id,
                        "account_id": account["account_id"],
                        "today": account["today"],
                        "ids": ids,
                        "names": [row["name"] for _, row in created],
                        "e164": [row["phone_e164"] for _, row in created],
                        "raws": [row["phone_raw"] for _, row in created],
                        "sources": [row["source"] for _, row in created],
                    },
                )
                await cur.execute(
                    """
                    insert into crm_activities (tenant_id, account_id, contact_id, kind, payload)
                    select %(tenant_id)s, %(account_id)s::uuid, t.contact_id::uuid, 'created',
                           '{"source": "import"}'::jsonb
                    from unnest(%(ids)s::text[]) as t(contact_id)
                    on conflict do nothing
                    """,
                    {"tenant_id": tenant_id, "account_id": account["account_id"], "ids": ids},
                )
    return {"created": len(created), "skipped": skipped}


async def get_contact(tenant_id: str, account: dict[str, Any], contact_id: str) -> dict[str, Any]:
    async with tenant_connection(tenant_id) as conn:
        row = await load_contact(conn, tenant_id, account["account_id"], contact_id)
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


async def _apply_update(
    conn: Any,
    tenant_id: str,
    account_id: str,
    contact_id: str,
    values: dict[str, Any],
    *,
    touched: bool = False,
    reset_meeting_reminder: bool = False,
) -> dict[str, Any]:
    casts = {"tags": "::text[]"}
    assignments = [f"{column} = %({column})s{casts.get(column, '')}" for column in values]
    if touched:
        assignments.append("last_touch_at = now()")
    if reset_meeting_reminder:
        assignments.append("meeting_reminded_at = null")
    assignments.append("updated_at = now()")
    row = await fetch_one(
        conn,
        f"""
        with changed as (
          update crm_contacts
          set {", ".join(assignments)}
          where tenant_id = %(tenant_id)s and account_id = %(account_id)s::uuid
            and contact_id = %(contact_id)s::uuid and deleted_at is null
          returning *
        )
        select {CONTACT_COLUMNS} from changed c
        """,
        {**values, "tenant_id": tenant_id, "account_id": account_id, "contact_id": contact_id},
    )
    if row is None:
        raise CrmError("contact_not_found", 404)
    return row


def _date_iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, date) else None


async def update_contact(
    tenant_id: str, account: dict[str, Any], contact_id: str, changes: dict[str, Any]
) -> dict[str, Any]:
    """``changes`` holds only the fields the client sent (null clears a field).

    History: a status change → «status» {from, to}; a step or date the partner
    set → «step» {action: set}; a new meeting time → «meeting» (and it counts as
    a touch); a moved or cleared meeting gets a new reminder.
    """
    account_id = account["account_id"]
    async with tenant_connection(tenant_id) as conn:
        current = await load_contact(conn, tenant_id, account_id, contact_id, for_update=True)
        values: dict[str, Any] = {}
        try:
            if "name" in changes:
                values["name"] = clean_name(changes["name"])
            if "source" in changes:
                values["source"] = clean_source(changes["source"])
            if "tags" in changes:
                values["tags"] = clean_tags(changes["tags"])
            if "priority" in changes:
                values["priority"] = clean_priority(changes["priority"] if changes["priority"] is not None else 0)
            if "phone" in changes:
                phone_e164, phone_raw = split_phone(changes["phone"])
                duplicate = await _duplicate_of(
                    conn, tenant_id, account_id, phone_e164, except_id=current["contact_id"]
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
            # Смена статуса на «Приглашён» требует время встречи в том же запросе:
            # старая встреча из прошлого круга дала бы дату шага в прошлом.

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
            raise _rule(exc) from exc
        if new_status is not None:
            values["status"] = new_status
        values["next_step"], values["next_at"] = plan.next_step, plan.next_at

        meeting_changed = meeting_given and values["meeting_at"] != current.get("meeting_at")
        row = await _apply_update(
            conn,
            tenant_id,
            account_id,
            current["contact_id"],
            values,
            touched=meeting_changed and values["meeting_at"] is not None,
            reset_meeting_reminder=meeting_changed,
        )

        async def history(kind: str, payload: dict[str, Any]) -> None:
            await add_activity(
                conn, tenant_id=tenant_id, account_id=account_id, contact_id=row["contact_id"], kind=kind, payload=payload
            )

        if meeting_changed:
            if values["meeting_at"] is not None:
                await history("meeting", {"action": "set", "at": _iso(values["meeting_at"])})
            else:
                await history("meeting", {"action": "cancel"})
        if new_status is not None and new_status != current["status"]:
            await history("status", {"from": current["status"], "to": new_status})
        elif ("next_step" in changes or "next_at" in changes) and (
            (plan.next_step, plan.next_at) != (current.get("next_step"), current.get("next_at"))
        ):
            await history("step", {"action": "set", "step": plan.next_step, "at": _date_iso(plan.next_at)})
    return contact_out(row)


async def done_contact(
    tenant_id: str,
    account: dict[str, Any],
    contact_id: str,
    *,
    meeting_at: datetime | None = None,
    next_at: date | None = None,
    next_at_given: bool = False,
) -> dict[str, Any]:
    """«Сделано»: the current step is done → the next one by ``rules.plan_done``.

    «Пригласить» needs the meeting: sent here, or a future one already on the card.
    History: «step» {action: done, step}, then «status» {from, to} when it changes
    and «meeting» when a new time came with the request.
    """
    account_id = account["account_id"]
    async with tenant_connection(tenant_id) as conn:
        current = await load_contact(conn, tenant_id, account_id, contact_id, for_update=True)
        new_meeting: datetime | None = None
        meeting_date: date | None = None
        if meeting_at is not None:
            new_meeting, meeting_date = await _meeting_local(conn, account["timezone"], meeting_at)
        elif current.get("meeting_at") is not None:
            ahead = await fetch_one(
                conn,
                "select %(ts)s::timestamptz > now() as ahead, (%(ts)s::timestamptz at time zone %(tz)s)::date as day",
                {"ts": current["meeting_at"], "tz": account["timezone"]},
            )
            if ahead and ahead["ahead"]:
                meeting_date = ahead["day"]
        try:
            plan = plan_done(
                status=current["status"],
                next_step=current.get("next_step"),
                today=account["today"],
                meeting_date=meeting_date,
                next_at=next_at,
                next_at_given=next_at_given,
            )
        except CrmRuleError as exc:
            raise _rule(exc) from exc
        values: dict[str, Any] = {"status": plan.status, "next_step": plan.next_step, "next_at": plan.next_at}
        meeting_changed = new_meeting is not None and new_meeting != current.get("meeting_at")
        if meeting_changed:
            values["meeting_at"] = new_meeting
        row = await _apply_update(
            conn,
            tenant_id,
            account_id,
            current["contact_id"],
            values,
            touched=meeting_changed,
            reset_meeting_reminder=meeting_changed,
        )
        await add_activity(
            conn, tenant_id=tenant_id, account_id=account_id, contact_id=row["contact_id"], kind="step",
            payload={"action": "done", "step": current.get("next_step"),
                     "next_step": plan.next_step, "at": _date_iso(plan.next_at)},
        )
        if plan.status != current["status"]:
            await add_activity(
                conn, tenant_id=tenant_id, account_id=account_id, contact_id=row["contact_id"], kind="status",
                payload={"from": current["status"], "to": plan.status},
            )
        if meeting_changed:
            await add_activity(
                conn, tenant_id=tenant_id, account_id=account_id, contact_id=row["contact_id"], kind="meeting",
                payload={"action": "set", "at": _iso(new_meeting)},
            )
    return contact_out(row)


async def snooze_contact(
    tenant_id: str,
    account: dict[str, Any],
    contact_id: str,
    *,
    days: Any = None,
    on: date | None = None,
) -> dict[str, Any]:
    """«Перенести»: the same step on another day (tomorrow, +3, a week, a date)."""
    account_id = account["account_id"]
    async with tenant_connection(tenant_id) as conn:
        current = await load_contact(conn, tenant_id, account_id, contact_id, for_update=True)
        try:
            new_date = snooze_until(account["today"], days=days, on=on)
        except CrmRuleError as exc:
            raise _rule(exc) from exc
        step = current.get("next_step") or "ping"
        row = await _apply_update(
            conn, tenant_id, account_id, current["contact_id"], {"next_step": step, "next_at": new_date}
        )
        await add_activity(
            conn, tenant_id=tenant_id, account_id=account_id, contact_id=row["contact_id"], kind="step",
            payload={"action": "snooze", "step": step, "from": _date_iso(current.get("next_at")),
                     "at": new_date.isoformat()},
        )
    return contact_out(row)


async def log_contact(
    tenant_id: str,
    account: dict[str, Any],
    contact_id: str,
    *,
    kind: Any,
    channel: Any = None,
    template_id: Any = None,
) -> dict[str, Any]:
    """A tap on «Позвонить» / WhatsApp / Telegram / Viber / MAX (maybe with a
    template): one «call» or «message» row and ``last_touch_at = now()``."""
    try:
        kind, channel = clean_log(kind, channel)
    except CrmRuleError as exc:
        raise _rule(exc) from exc
    account_id = account["account_id"]
    payload: dict[str, Any] = {"channel": channel}
    async with tenant_connection(tenant_id) as conn:
        current = await load_contact(conn, tenant_id, account_id, contact_id, for_update=True)
        if template_id not in (None, ""):
            template = await fetch_one(
                conn,
                """
                select template_id::text as template_id, title
                from crm_templates
                where tenant_id = %s and account_id = %s::uuid and template_id = %s::uuid
                """,
                (tenant_id, account_id, _uuid_or_404(template_id, "template_not_found")),
            )
            if template is None:
                raise CrmError("template_not_found", 404)
            payload.update(template_id=template["template_id"], template_title=template["title"])
        row = await _apply_update(conn, tenant_id, account_id, current["contact_id"], {}, touched=True)
        activity = await add_activity(
            conn, tenant_id=tenant_id, account_id=account_id, contact_id=row["contact_id"], kind=kind, payload=payload
        )
    return {"contact": contact_out(row), "activity": activity_out(activity) if activity else None}


async def delete_contact(tenant_id: str, account: dict[str, Any], contact_id: str) -> None:
    """Soft delete: the card disappears everywhere, «Вернуть» works for RESTORE_WINDOW."""
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            update crm_contacts set deleted_at = now()
            where tenant_id = %s and account_id = %s::uuid and contact_id = %s::uuid
              and deleted_at is null
            returning contact_id
            """,
            (tenant_id, account["account_id"], _contact_uuid(contact_id)),
        )
    if row is None:
        raise CrmError("contact_not_found", 404)


async def restore_contact(tenant_id: str, account: dict[str, Any], contact_id: str) -> dict[str, Any]:
    """«Вернуть» within RESTORE_WINDOW. A live card answers as is (a double tap);
    later or never deleted by this account — 404; the number now belongs to
    another live card — 409 duplicate."""
    account_id = account["account_id"]
    contact_uuid = _contact_uuid(contact_id)
    async with tenant_connection(tenant_id) as conn:
        found = await fetch_one(
            conn,
            """
            select phone_e164, deleted_at is null as live, deleted_at > now() - %s as restorable
            from crm_contacts
            where tenant_id = %s and account_id = %s::uuid and contact_id = %s::uuid
            for update
            """,
            (RESTORE_WINDOW, tenant_id, account_id, contact_uuid),
        )
        if found is None or not (found["live"] or found["restorable"]):
            raise CrmError("contact_not_found", 404)
        if not found["live"]:
            duplicate = await _duplicate_of(conn, tenant_id, account_id, found["phone_e164"], except_id=contact_uuid)
            if duplicate:
                raise CrmError("duplicate", 409, contact_id=duplicate)
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update crm_contacts set deleted_at = null
                    where tenant_id = %s and account_id = %s::uuid and contact_id = %s::uuid
                    """,
                    (tenant_id, account_id, contact_uuid),
                )
        row = await load_contact(conn, tenant_id, account_id, contact_uuid)
    return contact_out(row)


async def purge_deleted_contacts(tenant_id: str) -> int:
    """Erase cards deleted longer than RESTORE_WINDOW ago (notes and history go
    by cascade): v1 promised a real deletion, the mark only serves «Вернуть»."""
    async with tenant_connection(tenant_id) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                delete from crm_contacts
                where tenant_id = %s and deleted_at is not null and deleted_at <= now() - %s
                """,
                (tenant_id, RESTORE_WINDOW),
            )
            return int(cur.rowcount or 0)


async def purge_deleted_everywhere(tenant_ids: list[str]) -> int:
    """The worker step: erase in every tenant; one failing tenant does not stop the rest."""
    erased = 0
    for tenant_id in tenant_ids:
        try:
            erased += await purge_deleted_contacts(tenant_id)
        except Exception as exc:
            logger.warning("crm_purge_failed", extra={"tenant_id": tenant_id, **safe_error(exc)})
    return erased


async def add_note(tenant_id: str, account: dict[str, Any], contact_id: str, body: Any) -> dict[str, Any]:
    try:
        text = clean_note(body)
    except CrmRuleError as exc:
        raise _rule(exc) from exc
    account_id = account["account_id"]
    async with tenant_connection(tenant_id) as conn:
        contact = await load_contact(conn, tenant_id, account_id, contact_id, for_update=True)
        row = await fetch_one(
            conn,
            """
            insert into crm_notes (tenant_id, contact_id, body)
            values (%s, %s::uuid, %s)
            returning note_id::text as note_id, body, created_at
            """,
            (tenant_id, contact["contact_id"], text),
        )
        await add_activity(
            conn, tenant_id=tenant_id, account_id=account_id, contact_id=contact["contact_id"], kind="note",
            payload={"note_id": row["note_id"]},
        )
        async with conn.cursor() as cur:
            await cur.execute(
                """
                update crm_contacts set updated_at = now(), last_touch_at = now()
                where tenant_id = %s and contact_id = %s::uuid
                """,
                (tenant_id, contact["contact_id"]),
            )
    return note_out(row)


async def delete_note(tenant_id: str, account: dict[str, Any], contact_id: str, note_id: str) -> None:
    async with tenant_connection(tenant_id) as conn:
        contact = await load_contact(conn, tenant_id, account["account_id"], contact_id)
        row = await fetch_one(
            conn,
            """
            delete from crm_notes
            where tenant_id = %s and contact_id = %s::uuid and note_id = %s::uuid
            returning note_id::text as note_id
            """,
            (tenant_id, contact["contact_id"], _uuid_or_404(note_id, "note_not_found")),
        )
        if row is not None:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    delete from crm_activities
                    where tenant_id = %s and contact_id = %s::uuid and kind = 'note'
                      and payload ->> 'note_id' = %s
                    """,
                    (tenant_id, contact["contact_id"], row["note_id"]),
                )
    if row is None:
        raise CrmError("note_not_found", 404)


async def export_csv(tenant_id: str, account: dict[str, Any]) -> str:
    """UTF-8 with BOM, «;» between columns: Excel with a Russian locale opens it
    as a table with Cyrillic intact. Deleted cards are not exported."""
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
              and c.deleted_at is null
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
    lead is saved as before. Returns the card id or None.

    History: a new card gets «created» {source: site}, a repeat request on an
    existing card — «lead»; the note of the request — «note».

    Off unless PLATFORM_CRM_LEAD_CARDS is on (production API only): staging
    shares the database, and its test leads must not reach a live diary."""
    if not owner_actor_id or not crm_feature_enabled() or not get_settings().platform_crm_lead_cards:
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
            # Повтор той же заявки (ретрай n8n или сайта) — ничего не делаем: иначе
            # поиск по телефону находил человека и дописывал вторую заметку. Метка
            # обработанной заявки — в её же metadata (crm_card = id карточки).
            seen = await fetch_one(
                conn,
                """
                select 1 as seen from website_leads
                where tenant_id = %s and lead_id = %s::uuid and coalesce(metadata, '{}'::jsonb) ? 'crm_card'
                limit 1
                """,
                (tenant_id, lead_id),
            )
            if seen is not None:
                return None
            phone_e164, phone_raw = phone_from_lead_contact(contact)
            note = lead_note_text(
                contact=contact, product_name=product_name, comment=comment, phone_known=phone_e164 is not None
            )
            existing = await _duplicate_of(conn, tenant_id, account["account_id"], phone_e164)
            if existing:
                # Человек уже в ежедневнике — новая заявка становится заметкой, без дубля,
                # а карточка поднимается на «Сегодня» (шаг не позже сегодняшнего дня).
                contact_id = existing
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        update crm_contacts
                        set next_step = coalesce(next_step, 'invite'),
                            next_at = least(coalesce(next_at, %s), %s),
                            lead_id = coalesce(lead_id, %s::uuid),
                            updated_at = now()
                        where tenant_id = %s and contact_id = %s::uuid
                        """,
                        (account["today"], account["today"], lead_id, tenant_id, contact_id),
                    )
                event, event_payload = "lead", {}
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
                event, event_payload = "created", {"source": "site"}
            await add_activity(
                conn, tenant_id=tenant_id, account_id=account["account_id"], contact_id=contact_id,
                kind=event, payload=event_payload,
            )
            note_row = await fetch_one(
                conn,
                "insert into crm_notes (tenant_id, contact_id, body) values (%s, %s::uuid, %s) returning note_id::text as note_id",
                (tenant_id, contact_id, note),
            )
            await add_activity(
                conn, tenant_id=tenant_id, account_id=account["account_id"], contact_id=contact_id,
                kind="note", payload={"note_id": note_row["note_id"]},
            )
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    update website_leads
                    set metadata = coalesce(metadata, '{}'::jsonb) || jsonb_build_object('crm_card', %s::text)
                    where tenant_id = %s and lead_id = %s::uuid
                    """,
                    (contact_id, tenant_id, lead_id),
                )
            return contact_id
    except Exception as exc:
        logger.warning("crm_lead_card_failed", extra={"tenant_id": tenant_id, "lead_id": str(lead_id), **safe_error(exc)})
        return None


async def add_lead_card_for_public_id(tenant_id: str, public_id: str) -> dict[str, Any]:
    """Карточка для заявки, сохранённой не через save_lead.

    На бою заявки с сайта пишет n8n (workflow wwc-website-leads-p0), а не Core,
    поэтому хук в save_lead там не срабатывает. n8n после записи заявки зовёт
    внутренний роут, а он — тот же add_lead_card. Идемпотентно: карточка на
    заявку одна (on conflict do nothing), повтор по телефону — заметка.

    Заявки со staging-хостов не превращаем в карточки: staging делит базу с
    боем, а тестовая заявка не должна попасть в живой ежедневник.
    """
    async with tenant_connection(tenant_id) as conn:
        lead = await fetch_one(
            conn,
            """
            select lead_id::text as lead_id, name, contact, product_name, comment, page_url, assigned_owner_id
            from website_leads
            where tenant_id = %s and public_id = %s and deleted_at is null
            limit 1
            """,
            (tenant_id, public_id),
        )
        if lead is None:
            return {"ok": False, "error": "lead_not_found"}
        host = (urlsplit(str(lead.get("page_url") or "")).hostname or "").lower()
        if host.startswith("staging.") or host.startswith("admin") or host.startswith("cabinet."):
            return {"ok": True, "card_id": None, "reason": "staging_lead"}
        card_id = await add_lead_card(
            conn,
            tenant_id=tenant_id,
            lead_id=lead["lead_id"],
            owner_actor_id=lead.get("assigned_owner_id"),
            name=lead.get("name"),
            contact=lead.get("contact"),
            product_name=lead.get("product_name"),
            comment=lead.get("comment"),
        )
    return {"ok": True, "card_id": card_id, "reason": None if card_id else "skipped"}
