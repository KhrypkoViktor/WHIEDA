"""WWC CRM lists read by the app: people (search, filters, sort, cursor), the
pipeline (columns by status), the account's tags and a card's history.

Keyset pagination (``app.crm.paging``): every list reads ``limit + 1`` rows in a
fixed order that ends with the row id, so a page boundary never skips or
repeats a card even when many share a date. Search is ILIKE on name and
source plus digits of the phone — no pg_trgm on the shared database. Deleted
cards are never listed.
"""

from __future__ import annotations

from typing import Any

from app.crm.paging import (
    CONTACT_SORTS,
    DEFAULT_LIMIT,
    PIPELINE_FIRST_PAGE,
    clamp_limit,
    decode_cursor,
    page,
)
from app.crm.rules import STATUSES, STATUS_TITLES, CrmRuleError, clean_tag
from app.crm.service import CONTACT_COLUMNS, CrmError, activity_out, contact_out, load_contact
from app.db import fetch_all, fetch_one, tenant_connection

SEARCH_MAX = 100
TAGS_LIST_LIMIT = 200

# sort → (ORDER BY, keyset condition after the cursor, key of a row for the cursor)
_ORDER = {
    "updated": "c.updated_at desc, c.contact_id desc",
    "name": "lower(c.name) asc, c.contact_id asc",
    "next": "c.next_at asc nulls last, c.contact_id asc",
}
_AFTER = {
    "updated": "(c.updated_at, c.contact_id) < (%(k0)s::timestamptz, %(k1)s::uuid)",
    "name": "(lower(c.name), c.contact_id) > (%(k0)s::text, %(k1)s::uuid)",
}
_PIPELINE_ORDER = "c.priority desc, c.updated_at desc, c.contact_id desc"


def _like_pattern(needle: str) -> str:
    """ILIKE '%…%' with ! as the escape: a typed % or _ is a letter, not a wildcard."""
    escaped = needle.replace("!", "!!").replace("%", "!%").replace("_", "!_")
    return f"%{escaped}%"


def _rule_error(exc: CrmRuleError) -> CrmError:
    return CrmError(exc.code, exc.status)


def _contact_filters(
    params: dict[str, Any], *, q: str | None, status: str | None, tag: str | None
) -> list[str]:
    where = [
        "c.tenant_id = %(tenant_id)s",
        "c.account_id = %(account_id)s::uuid",
        "c.deleted_at is null",
    ]
    status_filter = (status or "").strip() or None
    if status_filter is not None:
        if status_filter not in STATUSES:
            raise CrmError("invalid_status", 400)
        where.append("c.status = %(status)s")
        params["status"] = status_filter
    tag_filter = clean_tag(tag)
    if tag_filter:
        where.append("%(tag)s = any(c.tags)")
        params["tag"] = tag_filter
    needle = " ".join(str(q or "").split())[:SEARCH_MAX]
    if needle:
        params["pattern"] = _like_pattern(needle)
        params["digits"] = "".join(ch for ch in needle if ch.isdigit())
        where.append(
            """(
              c.name ilike %(pattern)s escape '!'
              or c.source ilike %(pattern)s escape '!'
              or c.phone_raw ilike %(pattern)s escape '!'
              or (length(%(digits)s::text) >= 3 and (
                    strpos(coalesce(c.phone_e164, ''), %(digits)s::text) > 0
                 or strpos(regexp_replace(coalesce(c.phone_raw, ''), '[^0-9]', '', 'g'), %(digits)s::text) > 0))
            )"""
        )
    return where


def _after_cursor(sort: str, key: list[Any], params: dict[str, Any]) -> str:
    params["k0"], params["k1"] = key[0], key[1]
    if sort == "next":
        if key[0] is None:
            return "(c.next_at is null and c.contact_id > %(k1)s::uuid)"
        return (
            "(c.next_at > %(k0)s::date or (c.next_at = %(k0)s::date and c.contact_id > %(k1)s::uuid)"
            " or c.next_at is null)"
        )
    return _AFTER[sort]


def _row_key(sort: str):
    if sort == "updated":
        return lambda row: (row["updated_at"], row["contact_id"])
    if sort == "name":
        return lambda row: (row["contact_id"],)  # the name stays on the server (see paging)
    return lambda row: (row["next_at"], row["contact_id"])


async def _name_key(conn: Any, tenant_id: str, account_id: str, contact_id: str) -> list[Any]:
    """«By name» cursor → (lower(name), id) of that card, read here so the name
    never travels in a URL. A card erased since then — 400 invalid_cursor."""
    row = await fetch_one(
        conn,
        """
        select lower(name) as sort_name
        from crm_contacts
        where tenant_id = %s and account_id = %s::uuid and contact_id = %s::uuid
        """,
        (tenant_id, account_id, contact_id),
    )
    if row is None:
        raise CrmError("invalid_cursor", 400)
    return [row["sort_name"], contact_id]


async def list_contacts(
    tenant_id: str,
    account: dict[str, Any],
    *,
    q: str | None = None,
    status: str | None = None,
    tag: str | None = None,
    cursor: str | None = None,
    limit: Any = None,
    sort: str | None = None,
    default_limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """``{items, next_cursor, total}``; ``total`` counts every match, not the page."""
    sort_key = (sort or "updated").strip() or "updated"
    if sort_key not in CONTACT_SORTS:
        raise CrmError("invalid_sort", 400)
    try:
        size = clamp_limit(limit, default_limit) if limit is not None else default_limit
        key = decode_cursor(cursor, sort_key)
    except CrmRuleError as exc:
        raise _rule_error(exc) from exc
    params: dict[str, Any] = {"tenant_id": tenant_id, "account_id": account["account_id"]}
    where = _contact_filters(params, q=q, status=status, tag=tag)
    page_where = list(where)
    async with tenant_connection(tenant_id) as conn:
        if key is not None:
            if sort_key == "name":
                key = await _name_key(conn, tenant_id, account["account_id"], key[0])
            page_where.append(_after_cursor(sort_key, key, params))
        rows = await fetch_all(
            conn,
            f"""
            select {CONTACT_COLUMNS}
            from crm_contacts c
            where {" and ".join(page_where)}
            order by {_ORDER[sort_key]}
            limit %(limit)s
            """,
            {**params, "limit": size + 1},
        )
        total = await fetch_one(
            conn,
            f"select count(*)::int as n from crm_contacts c where {' and '.join(where)}",
            params,
        )
    items, next_cursor = page(rows, size, sort_key, _row_key(sort_key))
    return {
        "items": [contact_out(row) for row in items],
        "next_cursor": next_cursor,
        "total": int(total["n"]) if total else 0,
    }


def _pipeline_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (int(row["priority"] or 0), row["updated_at"], row["contact_id"])


def _tag_filter(tag: str | None) -> str | None:
    """``?tag=`` of the pipeline: the same exact match as /contacts; empty — no filter."""
    return clean_tag(tag) or None


async def pipeline(tenant_id: str, account: dict[str, Any], *, tag: str | None = None) -> dict[str, Any]:
    """Every status column: its count and the first PIPELINE_FIRST_PAGE cards
    (starred first, then recently changed) — one query. ``tag`` narrows every
    column and its count to the cards with that tag."""
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            f"""
            select * from (
              select {CONTACT_COLUMNS},
                     row_number() over (partition by c.status order by {_PIPELINE_ORDER}) as rn,
                     count(*) over (partition by c.status) as n
              from crm_contacts c
              where c.tenant_id = %(tenant_id)s and c.account_id = %(account_id)s::uuid
                and c.deleted_at is null
                and (%(tag)s::text is null or %(tag)s::text = any(c.tags))
            ) ranked
            where rn <= %(first)s
            order by status, rn
            """,
            {
                "tenant_id": tenant_id,
                "account_id": account["account_id"],
                "tag": _tag_filter(tag),
                "first": PIPELINE_FIRST_PAGE + 1,
            },
        )
    by_status: dict[str, list[dict[str, Any]]] = {status: [] for status in STATUSES}
    counts: dict[str, int] = {status: 0 for status in STATUSES}
    for row in rows:
        if row["status"] in by_status:
            by_status[row["status"]].append(row)
            counts[row["status"]] = int(row["n"])
    columns = []
    for status in STATUSES:
        items, next_cursor = page(by_status[status], PIPELINE_FIRST_PAGE, "pipeline", _pipeline_key)
        columns.append({
            "status": status,
            "title": STATUS_TITLES[status],
            "count": counts[status],
            "items": [contact_out(row) for row in items],
            "next_cursor": next_cursor,
        })
    return {"columns": columns, "total": sum(counts.values())}


async def pipeline_column(
    tenant_id: str,
    account: dict[str, Any],
    status: str,
    *,
    cursor: str | None = None,
    limit: Any = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """The rest of one column, in the order of ``pipeline`` (and with its ``tag``)."""
    if status not in STATUSES:
        raise CrmError("invalid_status", 400)
    try:
        size = clamp_limit(limit)
        key = decode_cursor(cursor, "pipeline")
    except CrmRuleError as exc:
        raise _rule_error(exc) from exc
    params: dict[str, Any] = {"tenant_id": tenant_id, "account_id": account["account_id"], "status": status}
    where = [
        "c.tenant_id = %(tenant_id)s",
        "c.account_id = %(account_id)s::uuid",
        "c.deleted_at is null",
        "c.status = %(status)s",
    ]
    tag_filter = _tag_filter(tag)
    if tag_filter:
        where.append("%(tag)s = any(c.tags)")
        params["tag"] = tag_filter
    page_where = list(where)
    if key is not None:
        params["k0"], params["k1"], params["k2"] = key
        page_where.append(
            "(c.priority, c.updated_at, c.contact_id) < (%(k0)s::smallint, %(k1)s::timestamptz, %(k2)s::uuid)"
        )
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            f"""
            select {CONTACT_COLUMNS}
            from crm_contacts c
            where {" and ".join(page_where)}
            order by {_PIPELINE_ORDER}
            limit %(limit)s
            """,
            {**params, "limit": size + 1},
        )
        total = await fetch_one(
            conn, f"select count(*)::int as n from crm_contacts c where {' and '.join(where)}", params
        )
    items, next_cursor = page(rows, size, "pipeline", _pipeline_key)
    return {
        "status": status,
        "title": STATUS_TITLES[status],
        "items": [contact_out(row) for row in items],
        "next_cursor": next_cursor,
        "total": int(total["n"]) if total else 0,
    }


async def list_tags(tenant_id: str, account: dict[str, Any]) -> list[dict[str, Any]]:
    """The account's tags with how many live cards carry each, most used first."""
    async with tenant_connection(tenant_id) as conn:
        rows = await fetch_all(
            conn,
            """
            select t.tag, count(*)::int as count
            from crm_contacts c
            cross join lateral unnest(c.tags) as t(tag)
            where c.tenant_id = %s and c.account_id = %s::uuid and c.deleted_at is null
            group by t.tag
            order by count(*) desc, lower(t.tag), t.tag
            limit %s
            """,
            (tenant_id, account["account_id"], TAGS_LIST_LIMIT),
        )
    return [{"tag": row["tag"], "count": int(row["count"])} for row in rows]


async def list_activities(
    tenant_id: str, account: dict[str, Any], contact_id: str, *, cursor: str | None = None, limit: Any = None
) -> dict[str, Any]:
    """A card's history, newest first; a note row carries the note text
    (crm_notes stays its source; a deleted note leaves no row)."""
    try:
        size = clamp_limit(limit)
        key = decode_cursor(cursor, "activities")
    except CrmRuleError as exc:
        raise _rule_error(exc) from exc
    async with tenant_connection(tenant_id) as conn:
        contact = await load_contact(conn, tenant_id, account["account_id"], contact_id)
        params: dict[str, Any] = {"tenant_id": tenant_id, "contact_id": contact["contact_id"], "limit": size + 1}
        after = ""
        if key is not None:
            params["k0"], params["k1"] = key
            after = "and (a.created_at, a.activity_id) < (%(k0)s::timestamptz, %(k1)s::uuid)"
        rows = await fetch_all(
            conn,
            f"""
            select a.activity_id::text as activity_id, a.kind, a.payload, a.created_at, n.body as note_body
            from crm_activities a
            left join crm_notes n
              on a.kind = 'note' and n.tenant_id = a.tenant_id and n.contact_id = a.contact_id
             and n.note_id::text = a.payload ->> 'note_id'
            where a.tenant_id = %(tenant_id)s and a.contact_id = %(contact_id)s::uuid
              and (a.kind <> 'note' or n.note_id is not null)
              {after}
            order by a.created_at desc, a.activity_id desc
            limit %(limit)s
            """,
            params,
        )
    items, next_cursor = page(rows, size, "activities", lambda row: (row["created_at"], row["activity_id"]))
    return {"items": [activity_out(row) for row in items], "next_cursor": next_cursor}
