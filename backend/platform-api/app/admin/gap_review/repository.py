"""SQL access for advisor gap review queue."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.db import fetch_all, fetch_one


async def aggregate_gap_events(conn, tenant_id: str) -> list[dict[str, Any]]:
    return await fetch_all(
        conn,
        """
        select
          payload->>'gap_kind' as gap_kind,
          payload->>'question_normalized' as question_normalized,
          nullif(trim(payload->>'detected_product'), '') as detected_product,
          sum(coalesce((payload->>'repeat_count')::int, 1))::int as event_count,
          min(
            coalesce(
              nullif(payload->>'first_seen_at', '')::timestamptz,
              created_at
            )
          ) as first_seen_at,
          max(
            coalesce(
              nullif(payload->>'last_seen_at', '')::timestamptz,
              created_at
            )
          ) as last_seen_at,
          max(payload->>'trace_id') as latest_trace_id
        from interaction_events
        where tenant_id = %s and event_type = 'advisor_gap'
        group by 1, 2, 3
        order by 6 desc nulls last, 2
        """,
        (tenant_id,),
    )


async def fetch_item_by_dedup(conn, tenant_id: str, dedup_key: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select *
        from advisor_gap_review_items
        where tenant_id = %s and dedup_key = %s
        """,
        (tenant_id, dedup_key),
    )


async def insert_review_item(
    conn,
    *,
    tenant_id: str,
    dedup_key: str,
    gap_kind: str,
    question_normalized: str,
    detected_product: str | None,
    event_count: int,
    first_seen_at: datetime,
    last_seen_at: datetime,
    priority: str,
    owner_role: str | None,
    candidate_type: str,
    evidence_ref: str | None,
) -> dict[str, Any]:
    row = await fetch_one(
        conn,
        """
        insert into advisor_gap_review_items (
          tenant_id, dedup_key, gap_kind, question_normalized, detected_product,
          event_count, first_seen_at, last_seen_at, priority, owner_role,
          candidate_type, evidence_ref
        ) values (
          %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s,
          %s, %s
        )
        returning *
        """,
        (
            tenant_id,
            dedup_key,
            gap_kind,
            question_normalized,
            detected_product,
            event_count,
            first_seen_at,
            last_seen_at,
            priority,
            owner_role,
            candidate_type,
            evidence_ref,
        ),
    )
    assert row is not None
    return row


async def refresh_existing_counts(
    conn,
    *,
    item_id: str,
    event_count: int,
    first_seen_at: datetime,
    last_seen_at: datetime,
    evidence_ref: str | None,
) -> dict[str, Any]:
    row = await fetch_one(
        conn,
        """
        update advisor_gap_review_items
        set
          event_count = %s,
          first_seen_at = least(first_seen_at, %s),
          last_seen_at = greatest(last_seen_at, %s),
          evidence_ref = coalesce(%s, evidence_ref),
          updated_at = now()
        where id = %s::uuid
        returning *
        """,
        (event_count, first_seen_at, last_seen_at, evidence_ref, item_id),
    )
    assert row is not None
    return row


async def list_review_items(
    conn,
    tenant_id: str,
    *,
    gap_kind: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    clauses = ["tenant_id = %s"]
    params: list[Any] = [tenant_id]
    if gap_kind:
        clauses.append("gap_kind = %s")
        params.append(gap_kind)
    if status:
        clauses.append("status = %s")
        params.append(status)
    if priority:
        clauses.append("priority = %s")
        params.append(priority)
    if from_ts:
        clauses.append("last_seen_at >= %s")
        params.append(from_ts)
    if to_ts:
        clauses.append("last_seen_at <= %s")
        params.append(to_ts)
    where = " and ".join(clauses)
    total_row = await fetch_one(
        conn,
        f"select count(*)::int as total from advisor_gap_review_items where {where}",
        tuple(params),
    )
    rows = await fetch_all(
        conn,
        f"""
        select *
        from advisor_gap_review_items
        where {where}
        order by
          case priority when 'p0' then 0 when 'p1' then 1 when 'p2' then 2 else 3 end,
          last_seen_at desc
        limit %s offset %s
        """,
        tuple(params + [limit, offset]),
    )
    return rows, int((total_row or {}).get("total") or 0)


async def fetch_review_item(conn, tenant_id: str, item_id: str) -> dict[str, Any] | None:
    return await fetch_one(
        conn,
        """
        select *
        from advisor_gap_review_items
        where tenant_id = %s and id = %s::uuid
        """,
        (tenant_id, item_id),
    )


async def update_review_item(
    conn,
    *,
    tenant_id: str,
    item_id: str,
    fields: dict[str, Any],
) -> dict[str, Any] | None:
    if not fields:
        return await fetch_review_item(conn, tenant_id, item_id)
    set_parts = []
    params: list[Any] = []
    for key, value in fields.items():
        set_parts.append(f"{key} = %s")
        params.append(value)
    set_parts.append("updated_at = now()")
    params.extend([tenant_id, item_id])
    return await fetch_one(
        conn,
        f"""
        update advisor_gap_review_items
        set {", ".join(set_parts)}
        where tenant_id = %s and id = %s::uuid
        returning *
        """,
        tuple(params),
    )


async def insert_mutation(
    conn,
    *,
    tenant_id: str,
    item_id: str,
    actor_principal_id: str | None,
    old_values: dict[str, Any],
    new_values: dict[str, Any],
) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            insert into advisor_gap_review_mutations (
              tenant_id, item_id, actor_principal_id, old_values, new_values
            ) values (%s, %s::uuid, %s::uuid, %s::jsonb, %s::jsonb)
            """,
            (
                tenant_id,
                item_id,
                actor_principal_id,
                json.dumps(old_values, ensure_ascii=False),
                json.dumps(new_values, ensure_ascii=False),
            ),
        )


async def fetch_mutations(conn, tenant_id: str, item_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    return await fetch_all(
        conn,
        """
        select mutation_id, actor_principal_id, old_values, new_values, created_at
        from advisor_gap_review_mutations
        where tenant_id = %s and item_id = %s::uuid
        order by created_at desc
        limit %s
        """,
        (tenant_id, item_id, limit),
    )


async def summary_stats(conn, tenant_id: str) -> dict[str, Any]:
    totals = await fetch_one(
        conn,
        """
        select
          count(*)::int as unique_gaps,
          coalesce(sum(event_count), 0)::int as total_repeats,
          min(first_seen_at) as first_seen,
          max(last_seen_at) as last_seen
        from advisor_gap_review_items
        where tenant_id = %s
        """,
        (tenant_id,),
    )
    by_kind = await fetch_all(
        conn,
        """
        select gap_kind, count(*)::int as item_count, sum(event_count)::int as repeat_count
        from advisor_gap_review_items
        where tenant_id = %s
        group by gap_kind
        order by repeat_count desc
        """,
        (tenant_id,),
    )
    by_status = await fetch_all(
        conn,
        """
        select status, count(*)::int as item_count
        from advisor_gap_review_items
        where tenant_id = %s
        group by status
        order by item_count desc
        """,
        (tenant_id,),
    )
    top_questions = await fetch_all(
        conn,
        """
        select question_normalized, gap_kind, event_count, last_seen_at, status, priority
        from advisor_gap_review_items
        where tenant_id = %s and status not in ('resolved', 'rejected')
        order by event_count desc, last_seen_at desc
        limit 20
        """,
        (tenant_id,),
    )
    return {
        "totals": totals or {},
        "by_gap_kind": by_kind,
        "by_status": by_status,
        "top_unresolved": top_questions,
    }
