"""Structured sync safety helpers (local tests + health script). Mirrors n8n Code node contracts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

PROJECT_ID = "whieda"
STRUCTURED_SYNC_WORKFLOW_ID = "9roEvXNsDpnwqjzH"
ERROR_WORKFLOW_ID_PLACEHOLDER = "__WHIEDA_SYNC_ERROR_WORKFLOW_ID__"
ADVISORY_LOCK_KEY1 = 0x574849  # WHI
ADVISORY_LOCK_KEY2 = 0x4441  # DA — tenant whieda structured sync

FRESHNESS_HEALTHY_MAX_MINUTES = 30

REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(password|passwd|pwd)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)(token|api[_-]?key|secret|bearer)\s*[=:]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]{8,}\b"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\bsk-[A-Za-z0-9]{16,}\b"), "sk-[REDACTED]"),
    (re.compile(r"\b\d{8,12}\b"), "[USER_ID_REDACTED]"),
    (re.compile(r"(?i)(INSERT|UPDATE|DELETE|SELECT)\s+(INTO|FROM|SET)?\s*[\w.\"]+"), "[SQL_REDACTED]"),
    (re.compile(r"https?://[^\s]+"), "[URL_REDACTED]"),
)

STRUCTURED_ROW_COUNT_TABLES: tuple[tuple[str, str], ...] = (
    ("rows_products", "advisor_structured_products"),
    ("rows_aliases", "advisor_structured_aliases"),
    ("rows_resources", "advisor_structured_resources"),
    ("rows_product_cards", "advisor_structured_product_cards"),
    ("rows_product_details", "advisor_structured_product_details"),
    ("rows_product_comparisons", "advisor_structured_product_comparisons"),
    ("rows_users_access", "advisor_structured_users_access"),
    ("rows_structure_owners", "advisor_structured_structure_owners"),
    ("rows_business_objections", "advisor_structured_business_objections"),
    ("rows_business_faq", "advisor_structured_business_faq"),
    ("rows_promotions", "advisor_promotions"),
    ("rows_recommendation_rules", "advisor_product_recommendation_rules"),
    ("rows_starter_basket_templates", "advisor_starter_basket_templates"),
    ("rows_events", "advisor_whieda_events"),
    ("rows_community_resources", "advisor_whieda_community_resources"),
    ("rows_intent_registry", "advisor_structured_intent_registry"),
    ("rows_clarification_prompts", "advisor_structured_clarification_prompts"),
    ("rows_capability_responses", "advisor_structured_capability_responses"),
    ("rows_canonical_questions", "advisor_structured_canonical_questions"),
)


def redact_sync_error(message: str | None, *, max_len: int = 500) -> str | None:
    if message is None:
        return None
    text = str(message).strip()
    if not text:
        return None
    for pattern, replacement in REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text or "sync_error"


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(int(value))
    escaped = str(value).replace("'", "''")
    return f"'{escaped}'"


def advisory_lock_sql() -> str:
    return f"""
SET LOCAL lock_timeout = '10s';
SELECT pg_advisory_xact_lock({ADVISORY_LOCK_KEY1}, {ADVISORY_LOCK_KEY2});""".strip()


def audit_ddl_sql() -> str:
    from pathlib import Path

    patch = Path(__file__).resolve().parents[1] / "patches" / "advisor_structured_sync_runs_v2_2026-08-10.sql"
    return patch.read_text(encoding="utf-8")


def build_record_running_sql(
    sync_run_uuid: str,
    *,
    started_at: str,
    workflow_execution_id: str | None,
    row_counts: Mapping[str, int],
) -> str:
    columns = [
        "sync_run_uuid",
        "project_id",
        "started_at",
        "finished_at",
        "status",
        "workflow_execution_id",
    ] + [name for name, _ in STRUCTURED_ROW_COUNT_TABLES]
    values = [
        sql_literal(sync_run_uuid),
        sql_literal(PROJECT_ID),
        f"{sql_literal(started_at)}::timestamptz",
        "NULL",
        sql_literal("running"),
        sql_literal(workflow_execution_id),
    ] + [str(int(row_counts.get(name, 0))) for name, _ in STRUCTURED_ROW_COUNT_TABLES]
    return f"""
{audit_ddl_sql()}

INSERT INTO advisor_structured_sync_runs ({", ".join(columns)})
VALUES ({", ".join(values)})
RETURNING run_id, sync_run_uuid, status, started_at;
""".strip()


def build_apply_all_transaction(
    layer_sql_parts: Sequence[str],
    sync_run_uuid: str,
) -> str:
    body = "\n\n".join(part.strip() for part in layer_sql_parts if part and part.strip())
    success_update = f"""
UPDATE advisor_structured_sync_runs
SET status = 'success',
    finished_at = now()
WHERE sync_run_uuid = {sql_literal(sync_run_uuid)}
  AND status = 'running';
""".strip()
    return "\n\n".join(
        [
            "BEGIN;",
            advisory_lock_sql(),
            body,
            success_update,
            "COMMIT;",
        ]
    )


def build_failed_audit_sql(
    *,
    sync_run_uuid: str | None,
    started_at: str | None,
    workflow_execution_id: str | None,
    error_message: str,
    row_counts: Mapping[str, int] | None = None,
) -> str:
    """Legacy/direct failure path (e.g. circuit breaker before running row exists)."""
    result = build_error_trigger_failed_audit_sql(
        failed_workflow_id=STRUCTURED_SYNC_WORKFLOW_ID,
        original_execution_id=workflow_execution_id,
        error_message=error_message,
        started_at=started_at,
        row_counts=row_counts,
        sync_run_uuid_hint=sync_run_uuid,
    )
    return result["query_failed_audit"]


def parse_n8n_error_trigger_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    workflow = payload.get("workflow") or {}
    execution = payload.get("execution") or {}
    error_obj = execution.get("error") or payload.get("error") or {}
    return {
        "failed_workflow_id": str(workflow.get("id") or ""),
        "failed_workflow_name": str(workflow.get("name") or ""),
        "original_execution_id": str(execution.get("id") or "") or None,
        "error_message": str(
            error_obj.get("message")
            or error_obj.get("description")
            or payload.get("message")
            or "structured_sync_failed"
        ),
    }


def build_error_trigger_failed_audit_sql(
    *,
    failed_workflow_id: str,
    original_execution_id: str | None,
    error_message: str,
    started_at: str | None = None,
    row_counts: Mapping[str, int] | None = None,
    sync_run_uuid_hint: str | None = None,
) -> dict[str, Any]:
    """Build failed-audit SQL from n8n Error Trigger payload (read-only contract)."""
    if failed_workflow_id != STRUCTURED_SYNC_WORKFLOW_ID:
        return {
            "skip_failed_audit": True,
            "reason": "not_whieda_structured_sync",
            "query_failed_audit": "SELECT 1;",
        }

    summary = sql_literal(redact_sync_error(error_message))
    counts = row_counts or {}
    exec_id = sql_literal(original_execution_id)
    started_ts = sql_literal(started_at or datetime.now(timezone.utc).isoformat())

    if not original_execution_id:
        metadata = sql_literal(json.dumps({"unmatched_error_trigger": True}))
        query = f"""
{audit_ddl_sql()}

INSERT INTO advisor_structured_sync_runs (
  sync_run_uuid, project_id, started_at, finished_at, status,
  workflow_execution_id, error_summary, error_metadata
)
VALUES (
  {sql_literal(sync_run_uuid_hint)}, {sql_literal(PROJECT_ID)}, {started_ts}::timestamptz, now(), 'failed',
  NULL, {summary}, {metadata}::jsonb
)
RETURNING run_id, sync_run_uuid, workflow_execution_id, status, error_summary;
""".strip()
        return {
            "skip_failed_audit": False,
            "unmatched_error_trigger": True,
            "query_failed_audit": query,
            "error_summary": redact_sync_error(error_message),
        }

    metadata_unmatched = sql_literal(json.dumps({"unmatched_error_trigger": True}))
    query = f"""
{audit_ddl_sql()}

WITH updated AS (
  UPDATE advisor_structured_sync_runs
  SET status = 'failed',
      finished_at = now(),
      error_summary = {summary},
      error_metadata = NULL
  WHERE workflow_execution_id = {exec_id}
    AND status = 'running'
  RETURNING run_id, sync_run_uuid, workflow_execution_id
)
INSERT INTO advisor_structured_sync_runs (
  sync_run_uuid, project_id, started_at, finished_at, status,
  workflow_execution_id, error_summary, error_metadata,
  rows_products, rows_aliases, rows_resources, rows_product_cards
)
SELECT
  {sql_literal(sync_run_uuid_hint)}, {sql_literal(PROJECT_ID)}, {started_ts}::timestamptz, now(), 'failed',
  {exec_id}, {summary}, {metadata_unmatched}::jsonb,
  {int(counts.get('rows_products', 0))}, {int(counts.get('rows_aliases', 0))},
  {int(counts.get('rows_resources', 0))}, {int(counts.get('rows_product_cards', 0))}
WHERE NOT EXISTS (SELECT 1 FROM updated)
  AND NOT EXISTS (
    SELECT 1 FROM advisor_structured_sync_runs
    WHERE workflow_execution_id = {exec_id}
      AND status IN ('running', 'failed')
  )
RETURNING run_id, sync_run_uuid, workflow_execution_id, status, error_summary;
""".strip()
    return {
        "skip_failed_audit": False,
        "unmatched_error_trigger": False,
        "query_failed_audit": query,
        "error_summary": redact_sync_error(error_message),
        "original_execution_id": original_execution_id,
    }


def build_error_trigger_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    parsed = parse_n8n_error_trigger_payload(payload)
    return build_error_trigger_failed_audit_sql(
        failed_workflow_id=parsed["failed_workflow_id"],
        original_execution_id=parsed["original_execution_id"],
        error_message=parsed["error_message"],
    )


def prepare_main_workflow_settings(error_workflow_id: str) -> dict[str, Any]:
    return {
        "executionOrder": "v1",
        "saveManualExecutions": True,
        "saveExecutionProgress": True,
        "errorWorkflow": error_workflow_id,
    }


def artifact_main_workflow_settings() -> dict[str, Any]:
    return prepare_main_workflow_settings(ERROR_WORKFLOW_ID_PLACEHOLDER)


@dataclass(frozen=True)
class FreshnessSnapshot:
    status: str
    last_success_at: datetime | None
    age_minutes: float | None
    last_failure_at: datetime | None
    last_failure_summary: str | None
    row_counts: dict[str, int]


def compute_freshness_status(
    *,
    last_success_at: datetime | None,
    last_failure_at: datetime | None,
    last_failure_summary: str | None,
    row_counts: Mapping[str, int],
    now: datetime | None = None,
) -> FreshnessSnapshot:
    now = now or datetime.now(timezone.utc)
    if last_success_at and last_success_at.tzinfo is None:
        last_success_at = last_success_at.replace(tzinfo=timezone.utc)
    if last_failure_at and last_failure_at.tzinfo is None:
        last_failure_at = last_failure_at.replace(tzinfo=timezone.utc)

    age_minutes: float | None = None
    if last_success_at:
        age_minutes = (now - last_success_at).total_seconds() / 60.0

    has_cache = any(int(value) > 0 for value in row_counts.values())
    if not last_success_at and not has_cache:
        status = "never_synced"
    elif last_failure_at and (not last_success_at or last_failure_at > last_success_at):
        status = "failed"
    elif age_minutes is not None and age_minutes <= FRESHNESS_HEALTHY_MAX_MINUTES:
        status = "healthy"
    elif last_success_at:
        status = "stale"
    else:
        status = "never_synced"

    return FreshnessSnapshot(
        status=status,
        last_success_at=last_success_at,
        age_minutes=age_minutes,
        last_failure_at=last_failure_at,
        last_failure_summary=last_failure_summary,
        row_counts=dict(row_counts),
    )


def future_sync_status_api_shape(snapshot: FreshnessSnapshot) -> dict[str, Any]:
    """Document-only contract for a future /v1/admin/sync-status endpoint."""
    return {
        "structured_sync": {
            "status": snapshot.status,
            "last_success_at": snapshot.last_success_at.isoformat() if snapshot.last_success_at else None,
            "age_minutes": round(snapshot.age_minutes, 2) if snapshot.age_minutes is not None else None,
            "last_failure_at": snapshot.last_failure_at.isoformat() if snapshot.last_failure_at else None,
            "last_failure_summary": snapshot.last_failure_summary,
            "row_counts": snapshot.row_counts,
            "healthy_threshold_minutes": FRESHNESS_HEALTHY_MAX_MINUTES,
        }
    }
