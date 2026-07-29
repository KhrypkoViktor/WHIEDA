#!/usr/bin/env python3
"""Release 3 Bundles: staging/review only; runtime publication is impossible here."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

TENANT_ID = "whieda"
REQUIRED = {"record_id", "название_ситуации", "основной_товар", "source_id", "publication_status"}
FAIL_PHASES = {"records", "review_queue"}


def normalized(value: str | None) -> str:
    return " ".join((value or "").strip().lower().replace("ё", "е").split())


def is_yes(value: str | None) -> bool:
    return normalized(value) in {"да", "yes", "true", "1"}


def sql_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_psql_file(sql_text: str, timeout_seconds: int = 75, tuples_only: bool = False) -> subprocess.CompletedProcess[str]:
    """Execute UTF-8 SQL from a file and always terminate the child on timeout."""
    fd, name = tempfile.mkstemp(prefix="whieda-bundles-", suffix=".sql")
    path = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(sql_text)
        process = subprocess.Popen(
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", *( ["-Atq"] if tuples_only else [] ), "-f", str(path)],
            # Windows psql emits diagnostics in the active console code page.
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="cp866", errors="replace",
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
            raise RuntimeError(f"psql timeout after {timeout_seconds}s; child terminated")
        if process.returncode:
            raise RuntimeError((stderr or stdout or "psql failed")[-3000:])
        return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    finally:
        path.unlink(missing_ok=True)


def run_psql_query(sql_text: str) -> str:
    result = run_psql_file(sql_text, 45, tuples_only=True)
    return result.stdout.strip()


def load_rows(path: Path) -> tuple[list[dict[str, str]], list[str], list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    missing = sorted(REQUIRED - set(rows[0] if rows else []))
    errors: list[str] = []
    seen: set[str] = set()
    for line, row in enumerate(rows, 2):
        record_id = (row.get("record_id") or "").strip()
        if not record_id:
            errors.append(f"row {line}: empty record_id")
        elif record_id in seen:
            errors.append(f"row {line}: duplicate record_id {record_id}")
        seen.add(record_id)
        for field, label in (("название_ситуации", "title"), ("основной_товар", "primary product"), ("source_id", "provenance")):
            if not (row.get(field) or "").strip():
                errors.append(f"row {line}: missing {label}")
    return rows, missing, errors


def make_payload(row: dict[str, str]) -> dict[str, object]:
    clean = {key: (value or "").strip() for key, value in row.items()}
    payload = {
        "external_record_id": clean["record_id"], "title": clean["название_ситуации"],
        "goal_text": clean["основной_запрос"], "primary_product": clean["основной_товар"],
        "additional_products": clean["доп_товары"], "bundle_logic": clean["логика_связки"],
        "application_order": clean["порядок_применения"], "restrictions_text": clean["ограничения"],
        "expected_result_text": clean["ожидаемый_результат"], "source_id": clean["source_id"],
        "source_locator": clean["source_locator"], "raw_quote": clean["raw_quote"],
        "source_status": clean["статус"], "publication_status": clean["publication_status"] or "blocked_raw",
        "medical_review_required": is_yes(clean["medical_review_required"]),
        "business_review_required": is_yes(clean["business_review_required"]),
        "owner_approved": is_yes(clean["owner_approved"]), "block_reason": clean["block_reason"],
    }
    review_types: list[str] = []
    if payload["medical_review_required"]:
        review_types.append("medical")
    if payload["business_review_required"]:
        review_types.append("business")
    if not payload["owner_approved"]:
        review_types.append("owner")
    if not payload["source_locator"]:
        review_types.append("provenance")
    # blocked_raw means unpublished, not dangerous. Safety requires an explicit future safety signal.
    payload["review_types"] = review_types
    payload["content_hash"] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return payload


def build_run_sql(run_id: str, payloads: list[dict[str, object]], fail_phase: str | None) -> str:
    encoded = sql_literal(json.dumps(payloads, ensure_ascii=False))
    fail_records = "RAISE EXCEPTION 'injected failure after records';" if fail_phase == "records" else ""
    fail_review = "RAISE EXCEPTION 'injected failure after review queue';" if fail_phase == "review_queue" else ""
    return f"""
SET lock_timeout = '10s';
SET statement_timeout = '45s';
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('whieda:bundle-staging-import'));
INSERT INTO advisor_bundle_import_runs(import_run_id, tenant_id, source_manifest)
VALUES ({sql_literal(run_id)}::uuid, {sql_literal(TENANT_ID)}, jsonb_build_array(jsonb_build_object('file','09_BUNDLE_CANDIDATES.tsv','rows',{len(payloads)})));
CREATE TEMP TABLE bundle_buffer(payload jsonb NOT NULL) ON COMMIT DROP;
INSERT INTO bundle_buffer(payload) SELECT value FROM jsonb_array_elements({encoded}::jsonb);
CREATE TEMP TABLE bundle_previous ON COMMIT DROP AS
SELECT r.external_record_id, r.content_hash, r.bundle_staging_id
FROM advisor_bundle_staging_records r
JOIN bundle_buffer b ON b.payload->>'external_record_id' = r.external_record_id
WHERE r.tenant_id={sql_literal(TENANT_ID)} AND r.version_state='current';
UPDATE advisor_bundle_staging_records old
SET version_state='superseded', superseded_by=NULL, updated_at=now()
FROM bundle_previous previous
JOIN bundle_buffer b ON b.payload->>'external_record_id'=previous.external_record_id
WHERE old.bundle_staging_id=previous.bundle_staging_id
  AND previous.content_hash<>b.payload->>'content_hash';
INSERT INTO advisor_bundle_staging_records(
 tenant_id,external_record_id,content_hash,source_id,source_locator,raw_quote,title,goal_text,primary_product,additional_products,bundle_logic,application_order,restrictions_text,expected_result_text,source_status,publication_status,medical_review_required,business_review_required,owner_approved,block_reason,first_seen_run_id,last_seen_run_id)
SELECT {sql_literal(TENANT_ID)},p->>'external_record_id',p->>'content_hash',p->>'source_id',NULLIF(p->>'source_locator',''),NULLIF(p->>'raw_quote',''),p->>'title',NULLIF(p->>'goal_text',''),p->>'primary_product',NULLIF(p->>'additional_products',''),NULLIF(p->>'bundle_logic',''),NULLIF(p->>'application_order',''),NULLIF(p->>'restrictions_text',''),NULLIF(p->>'expected_result_text',''),NULLIF(p->>'source_status',''),p->>'publication_status',(p->>'medical_review_required')::boolean,(p->>'business_review_required')::boolean,(p->>'owner_approved')::boolean,NULLIF(p->>'block_reason',''),{sql_literal(run_id)}::uuid,{sql_literal(run_id)}::uuid
FROM bundle_buffer
CROSS JOIN LATERAL (SELECT payload AS p) s
ON CONFLICT (tenant_id,external_record_id,content_hash) DO UPDATE
SET last_seen_run_id=EXCLUDED.last_seen_run_id,
    version_state='current',
    superseded_by=NULL,
    updated_at=now();
UPDATE advisor_bundle_staging_records old
SET version_state='superseded', superseded_by=fresh.bundle_staging_id, updated_at=now()
FROM bundle_previous previous
JOIN bundle_buffer b ON b.payload->>'external_record_id'=previous.external_record_id
JOIN advisor_bundle_staging_records fresh
  ON fresh.tenant_id={sql_literal(TENANT_ID)}
 AND fresh.external_record_id=previous.external_record_id
 AND fresh.content_hash=b.payload->>'content_hash'
 AND fresh.version_state='current'
WHERE old.bundle_staging_id=previous.bundle_staging_id
  AND previous.content_hash<>b.payload->>'content_hash';
UPDATE advisor_bundle_review_queue review
SET queue_status='superseded', updated_at=now()
FROM advisor_bundle_staging_records bundle
WHERE review.bundle_staging_id=bundle.bundle_staging_id
  AND bundle.version_state='superseded'
  AND review.queue_status='pending';
INSERT INTO advisor_bundle_run_records(import_run_id,bundle_staging_id,action)
SELECT {sql_literal(run_id)}::uuid,r.bundle_staging_id,
CASE WHEN previous.bundle_staging_id IS NOT NULL AND previous.content_hash<>r.content_hash THEN 'new_version'
     WHEN r.first_seen_run_id={sql_literal(run_id)}::uuid THEN 'inserted' ELSE 'reused' END
FROM bundle_buffer b
JOIN advisor_bundle_staging_records r ON r.tenant_id={sql_literal(TENANT_ID)} AND r.external_record_id=b.payload->>'external_record_id' AND r.content_hash=b.payload->>'content_hash' AND r.version_state='current'
LEFT JOIN bundle_previous previous ON previous.external_record_id=r.external_record_id
ON CONFLICT(import_run_id,bundle_staging_id) DO UPDATE SET action=EXCLUDED.action;
DO $$ BEGIN {fail_records} END $$;
INSERT INTO advisor_bundle_review_queue(bundle_staging_id,review_type,last_evaluated_run_id)
SELECT r.bundle_staging_id,t.review_type,{sql_literal(run_id)}::uuid
FROM bundle_buffer b
JOIN advisor_bundle_staging_records r ON r.tenant_id={sql_literal(TENANT_ID)} AND r.external_record_id=b.payload->>'external_record_id' AND r.content_hash=b.payload->>'content_hash' AND r.version_state='current'
CROSS JOIN LATERAL jsonb_array_elements_text(b.payload->'review_types') t(review_type)
ON CONFLICT(bundle_staging_id,review_type) DO UPDATE SET last_evaluated_run_id=EXCLUDED.last_evaluated_run_id,updated_at=now(),queue_status='pending';
DO $$ BEGIN {fail_review} END $$;
UPDATE advisor_bundle_import_runs SET run_state='completed', finished_at=now() WHERE import_run_id={sql_literal(run_id)}::uuid;
COMMIT;
"""


def mark_failed(run_id: str, error: str) -> None:
    sql = f"""INSERT INTO advisor_bundle_import_runs(import_run_id,tenant_id,run_state,error_text,finished_at)
VALUES ({sql_literal(run_id)}::uuid,{sql_literal(TENANT_ID)},'failed',{sql_literal(error[-2500:])},now())
ON CONFLICT(import_run_id) DO UPDATE SET run_state='failed',error_text=EXCLUDED.error_text,finished_at=now();"""
    run_psql_file(sql, 45)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--full-staging-run", action="store_true")
    parser.add_argument("--ensure-schema", action="store_true")
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--fail-phase", choices=sorted(FAIL_PHASES))
    args = parser.parse_args()
    if not (args.validate or args.full_staging_run):
        parser.error("use --validate or --full-staging-run")
    if args.full_staging_run and not args.no_publish:
        parser.error("--full-staging-run requires --no-publish")
    source = Path(args.source_dir) / "09_BUNDLE_CANDIDATES.tsv"
    rows, missing, errors = load_rows(source)
    if args.validate:
        print(json.dumps({"valid_records": len(rows), "errors": errors, "missing_columns": missing, "publication": "disabled"}, ensure_ascii=False, indent=2))
        raise SystemExit(1 if errors or missing else 0)
    if errors or missing:
        raise SystemExit(json.dumps({"errors": errors, "missing_columns": missing}, ensure_ascii=False))
    if args.ensure_schema:
        run_psql_file(Path(__file__).with_name("whieda_bundles_staging_schema_v1.sql").read_text(encoding="utf-8"), 60)
    payloads = [make_payload(row) for row in rows]
    run_id = str(uuid.uuid4())
    try:
        run_psql_file(build_run_sql(run_id, payloads, args.fail_phase), 75)
    except Exception as exc:
        mark_failed(run_id, str(exc))
        raise
    report = run_psql_query(f"""SELECT jsonb_build_object(
      'run_id',{sql_literal(run_id)},
      'state',(SELECT run_state FROM advisor_bundle_import_runs WHERE import_run_id={sql_literal(run_id)}::uuid),
      'records',(SELECT count(*) FROM advisor_bundle_run_records WHERE import_run_id={sql_literal(run_id)}::uuid),
      'actions',(SELECT coalesce(jsonb_object_agg(action,n),'{{}}'::jsonb) FROM (SELECT action,count(*) n FROM advisor_bundle_run_records WHERE import_run_id={sql_literal(run_id)}::uuid GROUP BY action)s),
      'reviews',(SELECT coalesce(jsonb_object_agg(review_type,n),'{{}}'::jsonb) FROM (SELECT review_type,count(*) n FROM advisor_bundle_review_queue WHERE last_evaluated_run_id={sql_literal(run_id)}::uuid GROUP BY review_type)s),
      'publication','disabled');""")
    print(report)


if __name__ == "__main__":
    main()
