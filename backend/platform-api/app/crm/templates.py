"""WWC CRM message templates: «Написать по шаблону» on a card.

The site substitutes ``{имя}`` / ``{мое_имя}`` and opens the messenger; Core only
keeps the partner's texts. The six defaults of ``rules.DEFAULT_TEMPLATES`` are
put once, on the first visit of any templates call: the account row is marked
(``crm_templates_seeded_at``) in the same transaction, so two parallel first
requests do not double them and templates the partner deleted do not return.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.crm.rules import (
    DEFAULT_TEMPLATES,
    TEMPLATES_MAX,
    CrmRuleError,
    clean_template_body,
    clean_template_title,
)
from app.crm.service import CrmError, _iso
from app.db import fetch_all, fetch_one, tenant_connection

_COLUMNS = "template_id::text as template_id, title, body, position, created_at, updated_at"
POSITION_MAX = 10_000


def template_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["template_id"],
        "title": row["title"],
        "body": row["body"],
        "position": int(row["position"]),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _template_uuid(template_id: Any) -> str:
    try:
        return str(uuid.UUID(str(template_id)))
    except (TypeError, ValueError):
        raise CrmError("template_not_found", 404) from None


def _position(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= POSITION_MAX:
        raise CrmError("invalid_position", 400)
    return value


async def _seed_defaults(conn: Any, tenant_id: str, account_id: str) -> None:
    claimed = await fetch_one(
        conn,
        """
        update platform_accounts set crm_templates_seeded_at = now()
        where tenant_id = %s and account_id = %s::uuid and crm_templates_seeded_at is null
        returning account_id
        """,
        (tenant_id, account_id),
    )
    if claimed is None:
        return
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into crm_templates (tenant_id, account_id, title, body, position)
            select %(tenant_id)s, %(account_id)s::uuid, t.title, t.body, t.position
            from unnest(%(titles)s::text[], %(bodies)s::text[], %(positions)s::int[]) as t(title, body, position)
            """,
            {
                "tenant_id": tenant_id,
                "account_id": account_id,
                "titles": [title for title, _ in DEFAULT_TEMPLATES],
                "bodies": [body for _, body in DEFAULT_TEMPLATES],
                "positions": list(range(1, len(DEFAULT_TEMPLATES) + 1)),
            },
        )


async def list_templates(tenant_id: str, account: dict[str, Any]) -> list[dict[str, Any]]:
    async with tenant_connection(tenant_id) as conn:
        await _seed_defaults(conn, tenant_id, account["account_id"])
        rows = await fetch_all(
            conn,
            f"""
            select {_COLUMNS} from crm_templates
            where tenant_id = %s and account_id = %s::uuid
            order by position, created_at, template_id
            """,
            (tenant_id, account["account_id"]),
        )
    return [template_out(row) for row in rows]


async def create_template(
    tenant_id: str, account: dict[str, Any], *, title: Any, body: Any, position: Any = None
) -> dict[str, Any]:
    try:
        clean_title, clean_body = clean_template_title(title), clean_template_body(body)
    except CrmRuleError as exc:
        raise CrmError(exc.code, exc.status) from exc
    explicit_position = _position(position) if position is not None else None
    async with tenant_connection(tenant_id) as conn:
        await _seed_defaults(conn, tenant_id, account["account_id"])
        stats = await fetch_one(
            conn,
            """
            select count(*)::int as n, coalesce(max(position), 0) as last
            from crm_templates where tenant_id = %s and account_id = %s::uuid
            """,
            (tenant_id, account["account_id"]),
        )
        if int(stats["n"]) >= TEMPLATES_MAX:
            raise CrmError("too_many_templates", 400, limit=TEMPLATES_MAX)
        row = await fetch_one(
            conn,
            f"""
            insert into crm_templates (tenant_id, account_id, title, body, position)
            values (%s, %s::uuid, %s, %s, %s)
            returning {_COLUMNS}
            """,
            (
                tenant_id,
                account["account_id"],
                clean_title,
                clean_body,
                explicit_position if explicit_position is not None else min(int(stats["last"]) + 1, POSITION_MAX),
            ),
        )
    return template_out(row)


async def update_template(
    tenant_id: str, account: dict[str, Any], template_id: Any, changes: dict[str, Any]
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    try:
        if "title" in changes:
            values["title"] = clean_template_title(changes["title"])
        if "body" in changes:
            values["body"] = clean_template_body(changes["body"])
    except CrmRuleError as exc:
        raise CrmError(exc.code, exc.status) from exc
    if "position" in changes:
        values["position"] = _position(changes["position"])
    assignments = ", ".join([f"{column} = %({column})s" for column in values] + ["updated_at = now()"])
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            f"""
            update crm_templates set {assignments}
            where tenant_id = %(tenant_id)s and account_id = %(account_id)s::uuid
              and template_id = %(template_id)s::uuid
            returning {_COLUMNS}
            """,
            {
                **values,
                "tenant_id": tenant_id,
                "account_id": account["account_id"],
                "template_id": _template_uuid(template_id),
            },
        )
    if row is None:
        raise CrmError("template_not_found", 404)
    return template_out(row)


async def delete_template(tenant_id: str, account: dict[str, Any], template_id: Any) -> None:
    async with tenant_connection(tenant_id) as conn:
        row = await fetch_one(
            conn,
            """
            delete from crm_templates
            where tenant_id = %s and account_id = %s::uuid and template_id = %s::uuid
            returning template_id
            """,
            (tenant_id, account["account_id"], _template_uuid(template_id)),
        )
    if row is None:
        raise CrmError("template_not_found", 404)
