#!/usr/bin/env python3
"""Read-only WHIEDA master snapshot vs production runtime integrity auditor."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "n8n" / "current"))

from whieda_master_integrity_lib import (  # noqa: E402
    EXPECTED_LAYERS,
    LAYER_ID_FIELDS,
    PROJECT_ID,
    RUNTIME_TABLES,
    assert_connection_readonly,
    build_runtime_hash_sql,
    compare_master_runtime_v2,
    detect_tenant_discriminator,
    find_newest_valid_snapshot,
    format_runtime_integrity_markdown,
    load_manifest,
    redact_sensitive_text,
    validate_snapshot_dir,
)

DEFAULT_SNAPSHOT_ROOT = ROOT / "n8n" / "live-exports" / "structured-master"
DEFAULT_REPORT_ROOT = ROOT / "n8n" / "live-exports" / "master-runtime-integrity"
READONLY_DSN_ENV = "WHIEDA_RUNTIME_READONLY_DSN"


def _connect_readonly(db_url: str):
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("psycopg is required for master/runtime integrity") from exc
    conn = psycopg.connect(db_url)
    assert_connection_readonly(conn)
    return conn


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


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = %s
        LIMIT 1
        """,
        (table,),
    )
    return cur.fetchone() is not None


def fetch_runtime_layers(conn) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with conn.cursor() as cur:
        for layer in EXPECTED_LAYERS:
            if layer == "partners_ref":
                continue
            if layer not in RUNTIME_TABLES:
                continue
            table, fields = RUNTIME_TABLES[layer]
            if not _table_exists(cur, table):
                rows[layer] = {
                    "status": "runtime_missing",
                    "reasons": ["runtime_table_missing"],
                    "row_count": 0,
                }
                continue

            columns = _table_columns(cur, table)
            tenant_column = detect_tenant_discriminator(columns)
            missing_fields = [field for field in fields if field.lower() not in columns]
            id_field = LAYER_ID_FIELDS.get(layer, [fields[0]])[0]
            if id_field.lower() not in columns:
                missing_fields.append(id_field)
            if missing_fields:
                rows[layer] = {
                    "status": "schema_mismatch",
                    "reasons": [f"missing_columns:{','.join(sorted(set(missing_fields)))}"],
                    "tenant_discriminator": tenant_column,
                    "row_count": 0,
                }
                continue

            query = build_runtime_hash_sql(
                table,
                fields,
                tenant_column=tenant_column,
                tenant_value=PROJECT_ID,
                id_field=id_field,
            )
            cur.execute(query)
            count, content_hash, ids = cur.fetchone()
            rows[layer] = {
                "row_count": int(count),
                "content_hash": str(content_hash),
                "ids": [str(item) for item in (ids or []) if item],
                "tenant_discriminator": tenant_column,
            }
    return rows


def fetch_sync_freshness(conn) -> dict[str, Any]:
    with conn.cursor() as cur:
        columns = _table_columns(cur, "advisor_structured_sync_runs")
        tenant_column = detect_tenant_discriminator(columns) or "project_id"
        cur.execute(
            f"""
            SELECT finished_at
            FROM advisor_structured_sync_runs
            WHERE {tenant_column} = %s AND status = 'success'
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
            WHERE {tenant_column} = %s AND status = 'failed'
            ORDER BY finished_at DESC NULLS LAST
            LIMIT 1
            """,
            (PROJECT_ID,),
        )
        failure_row = cur.fetchone()
    return {
        "status": "available",
        "last_success_at": last_success_at.isoformat() if last_success_at else None,
        "last_failure_at": failure_row[0].isoformat() if failure_row and failure_row[0] else None,
        "last_failure_summary": redact_sensitive_text(str(failure_row[1])) if failure_row and failure_row[1] else None,
        "tenant_discriminator": tenant_column,
    }


def build_report(
    snapshot_dir: Path,
    *,
    validation: dict[str, Any],
    runtime_rows: dict[str, dict[str, Any]] | None,
    sync_freshness: dict[str, Any] | None,
    read_only_proof: dict[str, Any] | None,
    run_id: str,
    dry_run: bool,
) -> dict[str, Any]:
    manifest = load_manifest(snapshot_dir)
    if dry_run or runtime_rows is None:
        layer_shell = {
            layer: {"status": "dry_run", "master_rows": validation.get("layers", {}).get(layer, {}).get("rows")}
            for layer in EXPECTED_LAYERS
        }
        if layer_shell.get("partners_ref"):
            layer_shell["partners_ref"] = {
                "status": "not_runtime_backed",
                "reason": "legacy_partner_path_only_no_structured_runtime_table",
            }
        report = {
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dry_run": True,
            "snapshot": str(snapshot_dir),
            "snapshot_id": snapshot_dir.name,
            "snapshot_captured_at": manifest.get("captured_at"),
            "snapshot_validation": {
                "valid": validation.get("valid"),
                "selected_reason": validation.get("selected_reason"),
            },
            "read_only_proof": read_only_proof or {"transaction_read_only": "not_checked"},
            "sync_freshness": sync_freshness or {"status": "not_checked"},
            "layers": layer_shell,
            "overall_status": "dry_run",
        }
        return report

    core = compare_master_runtime_v2(snapshot_dir, runtime_rows)
    report = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": False,
        "snapshot_validation": {
            "valid": validation.get("valid"),
            "selected_reason": validation.get("selected_reason"),
            "skipped_newer_invalid": validation.get("skipped_newer_invalid", []),
        },
        "read_only_proof": read_only_proof or {},
        "sync_freshness": sync_freshness or {"status": "not_checked"},
        **core,
    }
    return report


def write_outputs(report: dict[str, Any], json_out: Path, markdown_out: Path) -> None:
    json_out.parent.mkdir(parents=True, exist_ok=True)
    markdown_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    markdown_out.write_text(format_runtime_integrity_markdown(report), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA master/runtime integrity auditor (read-only)")
    parser.add_argument("--snapshot-root", type=Path, default=DEFAULT_SNAPSHOT_ROOT)
    parser.add_argument("--snapshot", type=Path, default=None, help="Explicit snapshot directory")
    parser.add_argument("--report-root", type=Path, default=DEFAULT_REPORT_ROOT)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--markdown-out", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true", help="Validate snapshot only; no DB session")
    args = parser.parse_args()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_dir = args.report_root / run_id
    json_out = args.json_out or (report_dir / "report.json")
    markdown_out = args.markdown_out or (report_dir / "report.md")

    try:
        if args.snapshot:
            validation = validate_snapshot_dir(args.snapshot)
            if not validation["valid"]:
                raise ValueError(
                    f"snapshot invalid: {'; '.join(validation.get('issues', []))}"
                )
            snapshot_dir = args.snapshot
            validation["selected_reason"] = "explicit"
        else:
            snapshot_dir, validation = find_newest_valid_snapshot(args.snapshot_root)
    except Exception as exc:
        payload = {
            "error": redact_sensitive_text(str(exc)),
            "overall_status": "blocking",
            "run_id": run_id,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    db_url = os.environ.get(READONLY_DSN_ENV)
    if args.dry_run:
        report = build_report(
            snapshot_dir,
            validation=validation,
            runtime_rows=None,
            sync_freshness=None,
            read_only_proof={"transaction_read_only": "not_checked", "dry_run": True},
            run_id=run_id,
            dry_run=True,
        )
        write_outputs(report, json_out, markdown_out)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    if not db_url:
        payload = {
            "error": f"{READONLY_DSN_ENV} is required for runtime comparison",
            "overall_status": "blocking",
            "snapshot_id": snapshot_dir.name,
            "run_id": run_id,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2

    try:
        conn = _connect_readonly(db_url)
        read_only_proof = {"transaction_read_only": "on", "begin_mode": "READ ONLY"}
        try:
            runtime_rows = fetch_runtime_layers(conn)
            sync_freshness = fetch_sync_freshness(conn)
        finally:
            conn.close()
        report = build_report(
            snapshot_dir,
            validation=validation,
            runtime_rows=runtime_rows,
            sync_freshness=sync_freshness,
            read_only_proof=read_only_proof,
            run_id=run_id,
            dry_run=False,
        )
    except Exception as exc:
        payload = {
            "error": redact_sensitive_text(str(exc)),
            "overall_status": "blocking",
            "snapshot_id": snapshot_dir.name,
            "run_id": run_id,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    write_outputs(report, json_out, markdown_out)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("overall_status") == "safe" else 2


if __name__ == "__main__":
    raise SystemExit(main())
