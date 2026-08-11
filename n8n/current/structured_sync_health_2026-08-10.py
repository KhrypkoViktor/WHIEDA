#!/usr/bin/env python3
"""Read-only structured sync freshness / readiness probe (local or staging)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from whieda_master_integrity_lib import detect_tenant_discriminator  # noqa: E402
from whieda_structured_sync_safety_lib import (  # noqa: E402
    PROJECT_ID,
    STRUCTURED_ROW_COUNT_TABLES,
    compute_freshness_status,
    future_sync_status_api_shape,
    redact_sync_error,
)

READONLY_DSN_ENV = "WHIEDA_RUNTIME_READONLY_DSN"


def _connect(db_url: str):
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("psycopg is required for structured_sync_health") from exc
    return psycopg.connect(db_url)


def _table_columns(cur, table: str) -> set[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        """,
        (table,),
    )
    return {str(row[0]).lower() for row in cur.fetchall()}


def fetch_health(db_url: str) -> dict:
    with _connect(db_url) as conn:
        with conn.cursor() as cur:
            sync_columns = _table_columns(cur, "advisor_structured_sync_runs")
            sync_tenant = detect_tenant_discriminator(sync_columns) or "project_id"

            cur.execute(
                f"""
                SELECT finished_at
                FROM advisor_structured_sync_runs
                WHERE {sync_tenant} = %s AND status = 'success'
                ORDER BY finished_at DESC NULLS LAST
                LIMIT 1
                """,
                (PROJECT_ID,),
            )
            success_row = cur.fetchone()
            last_success_at = success_row[0] if success_row else None

            cur.execute(
                f"""
                SELECT finished_at, error_summary
                FROM advisor_structured_sync_runs
                WHERE {sync_tenant} = %s AND status = 'failed'
                ORDER BY finished_at DESC NULLS LAST
                LIMIT 1
                """,
                (PROJECT_ID,),
            )
            failure_row = cur.fetchone()
            last_failure_at = failure_row[0] if failure_row else None
            last_failure_summary = (
                redact_sync_error(str(failure_row[1])) if failure_row and failure_row[1] else None
            )

            row_counts: dict[str, int] = {}
            for field, table in STRUCTURED_ROW_COUNT_TABLES:
                columns = _table_columns(cur, table)
                tenant_column = detect_tenant_discriminator(columns)
                if tenant_column:
                    cur.execute(
                        f"SELECT count(*) FROM {table} WHERE {tenant_column} = %s",
                        (PROJECT_ID,),
                    )
                else:
                    cur.execute(f"SELECT count(*) FROM {table}")
                row_counts[field] = int(cur.fetchone()[0])

    snapshot = compute_freshness_status(
        last_success_at=last_success_at,
        last_failure_at=last_failure_at,
        last_failure_summary=last_failure_summary,
        row_counts=row_counts,
        now=datetime.now(timezone.utc),
    )
    payload = future_sync_status_api_shape(snapshot)
    payload["checked_at"] = datetime.now(timezone.utc).isoformat()
    payload["probe"] = {
        "sync_tenant_discriminator": sync_tenant,
        "configured_via": READONLY_DSN_ENV,
    }
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Structured sync freshness probe")
    parser.add_argument("--db-url", default=None, help="Explicit read-only DSN (secondary to env)")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args()

    db_url = os.environ.get(READONLY_DSN_ENV) or args.db_url
    if not db_url:
        print(
            json.dumps(
                {
                    "error": f"runtime DSN is required: set {READONLY_DSN_ENV} or pass --db-url",
                    "status": "probe_not_configured",
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    try:
        payload = fetch_health(db_url)
    except Exception as exc:
        print(
            json.dumps(
                {"error": redact_sync_error(str(exc)), "status": "probe_failed"},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1

    if args.format == "text":
        sync = payload["structured_sync"]
        print(f"status={sync['status']} age_minutes={sync['age_minutes']} last_success={sync['last_success_at']}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
