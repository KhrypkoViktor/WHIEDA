"""Refresh advisor_gap events into tenant review queue projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.admin.gap_review.constants import DEFAULT_TRIAGE, GAP_KINDS
from app.admin.gap_review.dedup import build_dedup_key
from app.admin.gap_review import repository as repo
from app.db import tenant_connection


def _evidence_ref(trace_id: str | None) -> str | None:
    cleaned = str(trace_id or "").strip()
    if not cleaned:
        return None
    return f"trace:{cleaned[:24]}"


def _defaults(gap_kind: str) -> dict[str, str | None]:
    base = DEFAULT_TRIAGE.get(gap_kind, DEFAULT_TRIAGE["unknown_followup"])
    return {
        "priority": base["priority"],
        "owner_role": base.get("owner_role"),
        "candidate_type": base["candidate_type"],
    }


@dataclass
class RefreshResult:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
        }


async def refresh_advisor_gap_review_queue(
    tenant_id: str,
    *,
    dry_run: bool = False,
) -> RefreshResult:
    result = RefreshResult()
    async with tenant_connection(tenant_id) as conn:
        aggregates = await repo.aggregate_gap_events(conn, tenant_id)
        if dry_run:
            for row in aggregates:
                gap_kind = str(row.get("gap_kind") or "")
                if gap_kind not in GAP_KINDS:
                    result.skipped += 1
                    continue
                dedup_key = build_dedup_key(
                    gap_kind=gap_kind,
                    question_normalized=str(row.get("question_normalized") or ""),
                    detected_product=row.get("detected_product"),
                )
                existing = await repo.fetch_item_by_dedup(conn, tenant_id, dedup_key)
                if existing:
                    if (
                        int(existing.get("event_count") or 0) == int(row.get("event_count") or 0)
                        and existing.get("last_seen_at") == row.get("last_seen_at")
                    ):
                        result.unchanged += 1
                    else:
                        result.updated += 1
                else:
                    result.inserted += 1
            return result

        async with conn.transaction():
            for row in aggregates:
                gap_kind = str(row.get("gap_kind") or "")
                question = str(row.get("question_normalized") or "").strip()
                if gap_kind not in GAP_KINDS or not question:
                    result.skipped += 1
                    continue
                detected = row.get("detected_product")
                dedup_key = build_dedup_key(
                    gap_kind=gap_kind,
                    question_normalized=question,
                    detected_product=detected,
                )
                event_count = int(row.get("event_count") or 1)
                first_seen = row.get("first_seen_at")
                last_seen = row.get("last_seen_at")
                if not isinstance(first_seen, datetime) or not isinstance(last_seen, datetime):
                    result.skipped += 1
                    continue
                evidence = _evidence_ref(row.get("latest_trace_id"))
                existing = await repo.fetch_item_by_dedup(conn, tenant_id, dedup_key)
                if existing:
                    before_count = int(existing.get("event_count") or 0)
                    before_last = existing.get("last_seen_at")
                    await repo.refresh_existing_counts(
                        conn,
                        item_id=str(existing["id"]),
                        event_count=event_count,
                        first_seen_at=first_seen,
                        last_seen_at=last_seen,
                        evidence_ref=evidence,
                    )
                    if before_count == event_count and before_last == last_seen:
                        result.unchanged += 1
                    else:
                        result.updated += 1
                    continue
                defaults = _defaults(gap_kind)
                await repo.insert_review_item(
                    conn,
                    tenant_id=tenant_id,
                    dedup_key=dedup_key,
                    gap_kind=gap_kind,
                    question_normalized=question,
                    detected_product=detected,
                    event_count=event_count,
                    first_seen_at=first_seen,
                    last_seen_at=last_seen,
                    priority=str(defaults["priority"]),
                    owner_role=defaults.get("owner_role"),
                    candidate_type=str(defaults["candidate_type"]),
                    evidence_ref=evidence,
                )
                result.inserted += 1
    return result
