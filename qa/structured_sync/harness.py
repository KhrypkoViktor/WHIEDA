"""Postgres harness for structured sync safety local tests."""

from __future__ import annotations

import subprocess
import uuid
from datetime import datetime, timezone
from typing import Any

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
N8N_CURRENT = ROOT / "n8n" / "current"
sys.path.insert(0, str(N8N_CURRENT))

from whieda_structured_sync_safety_lib import (  # noqa: E402
    ADVISORY_LOCK_KEY1,
    ADVISORY_LOCK_KEY2,
    PROJECT_ID,
    STRUCTURED_ROW_COUNT_TABLES,
    STRUCTURED_SYNC_WORKFLOW_ID,
    advisory_lock_sql,
    audit_ddl_sql,
    build_apply_all_transaction,
    build_error_trigger_from_payload,
    build_failed_audit_sql,
    build_record_running_sql,
    redact_sync_error,
)

DOCKER_CONTAINER = "whieda-local-staging-postgres"
DOCKER_DB = "whieda_platform_local_core"
DOCKER_USER = "postgres"
DOCKER_PASSWORD = "local_staging_proof"


def docker_psql(sql: str, *, capture: bool = True) -> str:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGPASSWORD={DOCKER_PASSWORD}",
            DOCKER_CONTAINER,
            "psql",
            "-U",
            DOCKER_USER,
            "-d",
            DOCKER_DB,
            "-v",
            "ON_ERROR_STOP=1",
            "-tAc",
            sql,
        ],
        check=False,
        capture_output=capture,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "psql failed")
    return (result.stdout or "").strip()


def docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], check=True, capture_output=True)
        subprocess.run(
            ["docker", "inspect", DOCKER_CONTAINER],
            check=True,
            capture_output=True,
        )
        return True
    except Exception:
        return False


def ensure_audit_schema() -> None:
    docker_psql(audit_ddl_sql(), capture=False)


def seed_baseline_products(count: int = 3) -> None:
    docker_psql(
        f"""
        CREATE TABLE IF NOT EXISTS advisor_structured_products (
          client_id text NOT NULL,
          sku text NOT NULL,
          canonical_name text NOT NULL,
          category text,
          retail_price_rub text,
          retail_w text,
          retail_price_byn text,
          partner_price_rub text,
          partner_w text,
          partner_price_byn text,
          partner_points text,
          PRIMARY KEY (client_id, sku)
        );
        DELETE FROM advisor_structured_products WHERE client_id = '{PROJECT_ID}';
        INSERT INTO advisor_structured_products (client_id, sku, canonical_name, category)
        SELECT '{PROJECT_ID}', 'BASE-' || g, 'Baseline ' || g, 'seed'
        FROM generate_series(1, {count}) g;
        """
    )


def count_products() -> int:
    return int(docker_psql(f"SELECT count(*) FROM advisor_structured_products WHERE client_id = '{PROJECT_ID}';"))


def count_audit_status(status: str) -> int:
    return int(
        docker_psql(
            f"SELECT count(*) FROM advisor_structured_sync_runs WHERE project_id = '{PROJECT_ID}' AND status = '{status}';"
        )
    )


def latest_audit_error() -> str | None:
    value = docker_psql(
        f"""
        SELECT coalesce(error_summary, '')
        FROM advisor_structured_sync_runs
        WHERE project_id = '{PROJECT_ID}' AND status = 'failed'
        ORDER BY finished_at DESC NULLS LAST
        LIMIT 1;
        """
    )
    return value or None


def apply_valid_corpus_transaction(extra_layer_sql: list[str] | None = None) -> str:
    sync_uuid = str(uuid.uuid4())
    started = datetime.now(timezone.utc).isoformat()
    row_counts = {name: 1 for name, _ in STRUCTURED_ROW_COUNT_TABLES}
    row_counts["rows_products"] = 20
    docker_psql(build_record_running_sql(sync_uuid, started_at=started, workflow_execution_id="local-test", row_counts=row_counts))
    layer_sql = [
        f"""
        CREATE TABLE IF NOT EXISTS advisor_structured_products (
          client_id text NOT NULL, sku text NOT NULL, canonical_name text NOT NULL,
          category text, retail_price_rub text, retail_w text, retail_price_byn text,
          partner_price_rub text, partner_w text, partner_price_byn text, partner_points text,
          PRIMARY KEY (client_id, sku)
        );
        DELETE FROM advisor_structured_products WHERE client_id = '{PROJECT_ID}';
        INSERT INTO advisor_structured_products (client_id, sku, canonical_name, category)
        VALUES ('{PROJECT_ID}', 'NEW-001', 'Fresh Product', 'cat');
        """,
    ]
    if extra_layer_sql:
        layer_sql.extend(extra_layer_sql)
    docker_psql(build_apply_all_transaction(layer_sql, sync_uuid))
    return sync_uuid


def apply_with_mid_failure(sync_uuid: str | None = None) -> None:
    sync_uuid = sync_uuid or str(uuid.uuid4())
    started = datetime.now(timezone.utc).isoformat()
    row_counts = {"rows_products": 20, "rows_aliases": 60, "rows_resources": 25, "rows_product_cards": 10}
    docker_psql(
        build_record_running_sql(
            sync_uuid,
            started_at=started,
            workflow_execution_id="local-fail-test",
            row_counts=row_counts,
        )
    )
    layer_sql = [
        f"""
        DELETE FROM advisor_structured_products WHERE client_id = '{PROJECT_ID}';
        INSERT INTO advisor_structured_products (client_id, sku, canonical_name)
        VALUES ('{PROJECT_ID}', 'PARTIAL-001', 'Should Roll Back');
        """,
        "SELECT 1/0;",
    ]
    try:
        docker_psql(build_apply_all_transaction(layer_sql, sync_uuid))
    except RuntimeError:
        docker_psql(
            build_failed_audit_sql(
                sync_run_uuid=sync_uuid,
                started_at=started,
                workflow_execution_id="local-fail-test",
                error_message="division by zero",
                row_counts=row_counts,
            )
        )
        raise


def simulate_lock_contention() -> tuple[bool, bool]:
    holder_sql = f"""
    BEGIN;
    SELECT pg_advisory_xact_lock({ADVISORY_LOCK_KEY1}, {ADVISORY_LOCK_KEY2});
    SELECT pg_sleep(4);
    COMMIT;
    """
    contender_uuid = str(uuid.uuid4())
    started = datetime.now(timezone.utc).isoformat()
    row_counts = {"rows_products": 20}
    record_sql = build_record_running_sql(
        contender_uuid,
        started_at=started,
        workflow_execution_id="local-lock-test",
        row_counts=row_counts,
    )
    apply_sql = build_apply_all_transaction(
        [
            f"""
            CREATE TABLE IF NOT EXISTS advisor_structured_products (
              client_id text NOT NULL, sku text NOT NULL, canonical_name text NOT NULL,
              PRIMARY KEY (client_id, sku)
            );
            INSERT INTO advisor_structured_products (client_id, sku, canonical_name)
            VALUES ('{PROJECT_ID}', 'LOCK-001', 'Lock Test');
            """
        ],
        contender_uuid,
    )

    holder_proc = subprocess.Popen(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGPASSWORD={DOCKER_PASSWORD}",
            DOCKER_CONTAINER,
            "psql",
            "-U",
            DOCKER_USER,
            "-d",
            DOCKER_DB,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            holder_sql,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    import time

    time.sleep(0.8)
    contender_serialized = False
    try:
        docker_psql(record_sql)
        apply_started = time.monotonic()
        docker_psql(apply_sql)
        # The new transaction lock waits for the holder instead of allowing
        # two replacements to overlap. The holder sleeps for four seconds.
        contender_serialized = (time.monotonic() - apply_started) >= 2.5
    except RuntimeError:
        contender_serialized = True
        docker_psql(
            build_failed_audit_sql(
                sync_run_uuid=contender_uuid,
                started_at=started,
                workflow_execution_id="local-lock-test",
                error_message="structured_sync_lock_busy: another structured sync is already running for tenant whieda",
                row_counts=row_counts,
            )
        )
    holder_out, holder_err = holder_proc.communicate(timeout=15)
    holder_ok = holder_proc.returncode == 0
    return holder_ok, contender_serialized


def reset_audit_runs() -> None:
    docker_psql(f"DELETE FROM advisor_structured_sync_runs WHERE project_id = '{PROJECT_ID}';")


def row_counts_snapshot() -> dict[str, int]:
    counts: dict[str, int] = {}
    for field, table in STRUCTURED_ROW_COUNT_TABLES:
        counts[field] = int(docker_psql(f"SELECT count(*) FROM {table} WHERE client_id = '{PROJECT_ID}';"))
    return counts


def simulate_error_trigger_audit(payload: dict) -> dict[str, str | int | bool]:
    """Apply Error Trigger failed-audit SQL from a representative n8n payload."""
    result = build_error_trigger_from_payload(payload)
    before_failed = count_audit_status("failed")
    before_running = count_audit_status("running")
    if not result.get("skip_failed_audit"):
        docker_psql(result["query_failed_audit"])
    after_failed = count_audit_status("failed")
    after_running = count_audit_status("running")
    exec_id = str((payload.get("execution") or {}).get("id") or "")
    row = docker_psql(
        f"""
        SELECT coalesce(sync_run_uuid, ''), coalesce(workflow_execution_id, ''), coalesce(error_summary, ''), coalesce(status, '')
        FROM advisor_structured_sync_runs
        WHERE workflow_execution_id = '{exec_id.replace("'", "''")}'
        ORDER BY run_id DESC
        LIMIT 1;
        """
    )
    parts = row.split("|") if row else ["", "", "", ""]
    return {
        "skip": bool(result.get("skip_failed_audit")),
        "failed_delta": after_failed - before_failed,
        "running_delta": after_running - before_running,
        "sync_run_uuid": parts[0].strip(),
        "workflow_execution_id": parts[1].strip(),
        "error_summary": parts[2].strip(),
        "status": parts[3].strip(),
    }
