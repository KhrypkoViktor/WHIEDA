"""Release 2 Testimonials: staging-only, atomic, no publication."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

TENANT = "whieda"
FILES = {
    "07_TESTIMONIALS.tsv": ("testimonial_id", "testimonials"),
    "06_USAGE_PATTERNS.tsv": ("record_id", "usage_patterns"),
    "15_SAFETY_SIGNALS.tsv": ("signal_id", "safety_signals"),
    "12_RESOURCE_LINKS.tsv": ("record_id", "resource_links"),
}
FAULTS = ("records_lineage", "review_queue", "media_candidates", "duplicate_links")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalized(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").lower().replace("ё", "е")).strip()


def row_hash(row: dict[str, str]) -> str:
    return sha256(json.dumps({key: (value or "").strip() for key, value in row.items()}, ensure_ascii=False, sort_keys=True).encode())


def pick(row: dict[str, str], *keys: str) -> str:
    return next((row[key] for key in keys if row.get(key)), "")


def is_yes(value: str | None) -> bool:
    return normalized(value) in {"да", "yes", "true", "1", "y"}


def load_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file, delimiter="\t"))


def psql(args: list[str], stdin: str | None = None) -> str:
    executable = shutil.which("psql")
    if not executable:
        raise RuntimeError("psql is not installed")
    required = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGPASSWORD")
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RuntimeError(f"Missing database environment variables: {', '.join(missing)}")
    result = subprocess.run(
        [executable, "-v", "ON_ERROR_STOP=1", *args],
        input=stdin,
        text=True,
        encoding="utf8",
        errors="replace",
        capture_output=True,
        env=os.environ.copy(),
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def classify(source_table: str, row: dict[str, str]) -> str:
    if source_table == "safety_signals":
        return "safety"
    if source_table == "usage_patterns" and row.get("уровень_риска") in {"высокий", "критический"}:
        return "safety"
    if source_table == "testimonials" and row.get("мед_критичность") in {"высокая", "критическая"}:
        return "safety"
    text = normalized(pick(row, "результат_словами_автора", "наблюдаемый_результат", "событие", "примечание"))
    if any(token in text for token in ("не помог", "ухудш", "отрицатель", "без результата")):
        return "negative"
    return "positive" if source_table == "testimonials" and row.get("можно_в_маркетинг") == "да" else "neutral"


def review_types(record: dict[str, object]) -> list[str]:
    """Accumulate every review obligation carried by the raw source."""
    source_table = str(record["table"])
    types: list[str] = []
    if source_table == "testimonials":
        types.append("consent")
    if record["medical_review_required"]:
        types.append("medical")
    if record["class"] == "safety":
        types.append("safety")
    if record["class"] == "negative":
        types.append("negative")
    if source_table == "testimonials" and record["marketing_allowed"] and record["class"] == "positive":
        types.append("marketing")
    return types


def fault_sql(phase: str | None, expected: str) -> str:
    return "DO $$ BEGIN RAISE EXCEPTION 'injected fault: %'; END $$;" % expected if phase == expected else ""


def prepare(source_dir: Path) -> tuple[list[dict[str, object]], list[dict[str, str]], list[list[object]], list[list[object]], list[dict[str, object]]]:
    source_ids = {row.get("source_id", "") for row in load_tsv(source_dir / "00_SOURCE_REGISTER.tsv")}
    records: list[dict[str, object]] = []
    media: list[dict[str, str]] = []
    errors: list[list[object]] = []
    warnings: list[list[object]] = []
    manifest: list[dict[str, object]] = []
    for filename, (id_column, source_table) in FILES.items():
        path = source_dir / filename
        rows = load_tsv(path)
        manifest.append({"file": filename, "rows": len(rows), "sha256": sha256(path.read_bytes())})
        seen: set[str] = set()
        for line, row in enumerate(rows, 2):
            external_id = (row.get(id_column) or "").strip()
            if not external_id or external_id in seen:
                errors.append([filename, line, "duplicate_or_missing_external_id"])
                continue
            seen.add(external_id)
            if row.get("source_id", "") not in source_ids:
                errors.append([filename, line, "invalid_source_id"])
                continue
            url = pick(row, "ссылка", "url")
            if url and re.match(r"^https?://", url) is None and any(token in normalized(url) for token in ("http", "www.")):
                errors.append([filename, line, "broken_url"])
                continue
            provenance = bool(row.get("source_locator") or row.get("raw_quote") or url)
            content = pick(row, "результат_словами_автора", "наблюдаемый_результат", "событие", "краткое_нейтральное_резюме", "название")
            if not provenance:
                warnings.append([filename, line, "missing_provenance"])
            if not content:
                warnings.append([filename, line, "empty_content"])
            if source_table == "resource_links":
                media.append({
                    "id": external_id, "product": row.get("товар", ""), "type": row.get("тип_материала", ""),
                    "title": row.get("название", ""), "url": url, "source_id": row.get("source_id", ""),
                    "locator": row.get("source_locator", ""),
                    "status": "blocked_unconfirmed_testimonial" if "видеоотзыв" in normalized(row.get("тип_материала")) else "not_testimonial_media",
                })
                continue
            quote = pick(row, "raw_quote", "результат_словами_автора", "наблюдаемый_результат", "событие")
            records.append({
                "table": source_table, "id": external_id, "hash": row_hash(row), "source_id": row.get("source_id", ""),
                "locator": row.get("source_locator", ""), "quote": row.get("raw_quote", ""), "author": pick(row, "автор"),
                "role": row.get("author_role", ""), "product": row.get("товар", ""),
                "situation": pick(row, "исходная_проблема", "ситуация", "состояние_до_применения"),
                "use": pick(row, "что_применяли", "как_использовали"), "duration": row.get("длительность", ""),
                "outcome": pick(row, "результат_словами_автора", "наблюдаемый_результат", "исход"),
                "summary": row.get("краткое_нейтральное_резюме", ""), "format": pick(row, "формат_источника", "тип_свидетельства"),
                "url": url, "class": classify(source_table, row),
                "finger": sha256((normalized(row.get("товар")) + "|" + normalized(quote) + "|" + normalized(pick(row, "автор"))).encode()),
                "provenance": provenance,
                "medical_review_required": is_yes(row.get("medical_review_required")),
                "marketing_allowed": is_yes(row.get("можно_в_маркетинг")),
                "risk_level": row.get("уровень_риска", ""),
                "medical_criticality": row.get("мед_критичность", ""),
            })
    return records, media, errors, warnings, manifest


def run_sql(run_id: str, fault: str | None) -> str:
    return f"""
BEGIN;
INSERT INTO advisor_testimonial_records(
  tenant_id, source_table, external_record_id, content_hash, source_id, source_locator, raw_quote,
  author_name, author_role, product_name, situation, use_description, duration_text,
  outcome_author_words, neutral_summary, source_format, source_url, testimonial_class,
  consent_state, publication_scope, medical_review_state, marketing_review_state,
  publication_status, duplicate_fingerprint, first_seen_run_id, last_seen_run_id
  ,source_medical_review_required, source_marketing_allowed, source_risk_level, source_medical_criticality
)
SELECT tenant_id, source_table, external_record_id, content_hash, payload->>'source_id',
  payload->>'locator', payload->>'quote', payload->>'author', payload->>'role', payload->>'product',
  payload->>'situation', payload->>'use', payload->>'duration', payload->>'outcome', payload->>'summary',
  payload->>'format', payload->>'url', payload->>'class', 'unknown', 'internal_only', 'review_required',
  'review_required', 'blocked_raw', payload->>'finger', '{run_id}'::uuid, '{run_id}'::uuid
  ,COALESCE((payload->>'medical_review_required')::boolean, false), COALESCE((payload->>'marketing_allowed')::boolean, false),
  payload->>'risk_level', payload->>'medical_criticality'
FROM advisor_testimonial_hardening_buffer WHERE import_run_id = '{run_id}'::uuid
ON CONFLICT DO NOTHING;

UPDATE advisor_testimonial_records record SET
  source_medical_review_required = COALESCE((buffer.payload->>'medical_review_required')::boolean, false),
  source_marketing_allowed = COALESCE((buffer.payload->>'marketing_allowed')::boolean, false),
  source_risk_level = buffer.payload->>'risk_level',
  source_medical_criticality = buffer.payload->>'medical_criticality',
  last_seen_run_id = '{run_id}'::uuid,
  updated_at = now()
FROM advisor_testimonial_hardening_buffer buffer
WHERE buffer.import_run_id = '{run_id}'::uuid
  AND record.tenant_id = buffer.tenant_id AND record.source_table = buffer.source_table
  AND record.external_record_id = buffer.external_record_id AND record.content_hash = buffer.content_hash;

INSERT INTO advisor_testimonial_run_records(import_run_id, testimonial_staging_id, action)
SELECT '{run_id}'::uuid, record.testimonial_staging_id,
  CASE WHEN record.first_seen_run_id = '{run_id}'::uuid
       THEN CASE WHEN EXISTS (
          SELECT 1 FROM advisor_testimonial_records other
          WHERE other.tenant_id = record.tenant_id AND other.source_table = record.source_table
            AND other.external_record_id = record.external_record_id AND other.content_hash <> record.content_hash
       ) THEN 'new_version' ELSE 'inserted' END
       ELSE 'reused' END
FROM advisor_testimonial_hardening_buffer buffer
JOIN advisor_testimonial_records record ON (
  record.tenant_id = buffer.tenant_id AND record.source_table = buffer.source_table
  AND record.external_record_id = buffer.external_record_id AND record.content_hash = buffer.content_hash
)
WHERE buffer.import_run_id = '{run_id}'::uuid;

UPDATE advisor_testimonial_records active SET version_state = 'current', superseded_by = NULL,
  last_seen_run_id = '{run_id}'::uuid
FROM advisor_testimonial_hardening_buffer buffer
WHERE buffer.import_run_id = '{run_id}'::uuid
  AND active.tenant_id = buffer.tenant_id AND active.source_table = buffer.source_table
  AND active.external_record_id = buffer.external_record_id AND active.content_hash = buffer.content_hash;

UPDATE advisor_testimonial_records old SET version_state = 'superseded', superseded_by = active.testimonial_staging_id
FROM advisor_testimonial_records active
JOIN advisor_testimonial_hardening_buffer buffer ON (
  active.tenant_id = buffer.tenant_id AND active.source_table = buffer.source_table
  AND active.external_record_id = buffer.external_record_id AND active.content_hash = buffer.content_hash
)
WHERE buffer.import_run_id = '{run_id}'::uuid
  AND old.tenant_id = active.tenant_id AND old.source_table = active.source_table
  AND old.external_record_id = active.external_record_id AND old.content_hash <> active.content_hash
  AND old.version_state = 'current';

UPDATE advisor_testimonial_review_queue queue SET queue_status = 'superseded', updated_at = now()
FROM advisor_testimonial_records record
WHERE record.testimonial_staging_id = queue.testimonial_staging_id
  AND record.version_state = 'superseded'
  AND record.superseded_by IN (
    SELECT active.testimonial_staging_id FROM advisor_testimonial_records active
    JOIN advisor_testimonial_hardening_buffer buffer ON (
      active.tenant_id = buffer.tenant_id AND active.source_table = buffer.source_table
      AND active.external_record_id = buffer.external_record_id AND active.content_hash = buffer.content_hash
    )
    WHERE buffer.import_run_id = '{run_id}'::uuid
  );
{fault_sql(fault, 'records_lineage')}

DELETE FROM advisor_testimonial_review_queue queue
USING advisor_testimonial_run_records link
WHERE link.import_run_id = '{run_id}'::uuid
  AND queue.testimonial_staging_id = link.testimonial_staging_id;

INSERT INTO advisor_testimonial_review_queue(testimonial_staging_id, review_type, last_evaluated_run_id)
SELECT record.testimonial_staging_id, review.review_type, '{run_id}'::uuid
FROM advisor_testimonial_hardening_buffer buffer
JOIN advisor_testimonial_records record ON (
  record.tenant_id = buffer.tenant_id AND record.source_table = buffer.source_table
  AND record.external_record_id = buffer.external_record_id AND record.content_hash = buffer.content_hash
)
CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(buffer.payload->'review_types', '[]'::jsonb)) review(review_type)
WHERE buffer.import_run_id = '{run_id}'::uuid
ON CONFLICT(testimonial_staging_id, review_type) DO UPDATE
SET last_evaluated_run_id = EXCLUDED.last_evaluated_run_id, updated_at = now();

INSERT INTO advisor_testimonial_review_queue(testimonial_staging_id, review_type, last_evaluated_run_id)
SELECT record.testimonial_staging_id, 'provenance', '{run_id}'::uuid
FROM advisor_testimonial_hardening_buffer buffer
JOIN advisor_testimonial_records record ON (
  record.tenant_id = buffer.tenant_id AND record.source_table = buffer.source_table
  AND record.external_record_id = buffer.external_record_id AND record.content_hash = buffer.content_hash
)
WHERE buffer.import_run_id = '{run_id}'::uuid
  AND COALESCE(NULLIF(record.source_locator, ''), NULLIF(record.raw_quote, ''), NULLIF(record.source_url, '')) IS NULL
ON CONFLICT(testimonial_staging_id, review_type) DO UPDATE
SET last_evaluated_run_id = EXCLUDED.last_evaluated_run_id, updated_at = now();

DO $$
BEGIN
  IF EXISTS (
    WITH expected AS (
      SELECT record.testimonial_staging_id, review.review_type
      FROM advisor_testimonial_hardening_buffer buffer
      JOIN advisor_testimonial_records record ON (
        record.tenant_id = buffer.tenant_id AND record.source_table = buffer.source_table
        AND record.external_record_id = buffer.external_record_id AND record.content_hash = buffer.content_hash
      )
      CROSS JOIN LATERAL jsonb_array_elements_text(COALESCE(buffer.payload->'review_types', '[]'::jsonb)) review(review_type)
      WHERE buffer.import_run_id = '{run_id}'::uuid
    )
    SELECT 1 FROM expected
    LEFT JOIN advisor_testimonial_review_queue queue ON (
      queue.testimonial_staging_id = expected.testimonial_staging_id
      AND queue.review_type = expected.review_type AND queue.queue_status = 'pending'
    )
    WHERE queue.review_id IS NULL
  ) THEN
    RAISE EXCEPTION 'review coverage assertion failed';
  END IF;
END $$;
{fault_sql(fault, 'review_queue')}

INSERT INTO advisor_testimonial_media_candidates(
  import_run_id, tenant_id, resource_record_id, product_name, media_type, title, resource_url,
  source_id, source_locator, media_link_status
)
SELECT '{run_id}'::uuid, tenant_id, resource_record_id, payload->>'product', payload->>'type',
  payload->>'title', payload->>'url', payload->>'source_id', payload->>'locator', payload->>'status'
FROM advisor_testimonial_hardening_media_buffer WHERE import_run_id = '{run_id}'::uuid
ON CONFLICT(tenant_id, resource_record_id) DO UPDATE
SET import_run_id = EXCLUDED.import_run_id, media_link_status = EXCLUDED.media_link_status;
{fault_sql(fault, 'media_candidates')}

INSERT INTO advisor_testimonial_links(from_testimonial_staging_id, to_testimonial_staging_id, relation_type)
SELECT newer.testimonial_staging_id, older.testimonial_staging_id, 'duplicate_of'
FROM advisor_testimonial_records newer
JOIN advisor_testimonial_records older ON newer.tenant_id = older.tenant_id
  AND newer.duplicate_fingerprint = older.duplicate_fingerprint
  AND newer.testimonial_staging_id <> older.testimonial_staging_id
WHERE newer.first_seen_run_id = '{run_id}'::uuid
ON CONFLICT DO NOTHING;
{fault_sql(fault, 'duplicate_links')}

DELETE FROM advisor_testimonial_hardening_buffer WHERE import_run_id = '{run_id}'::uuid;
DELETE FROM advisor_testimonial_hardening_media_buffer WHERE import_run_id = '{run_id}'::uuid;
UPDATE advisor_testimonial_import_runs SET run_state = 'completed', finished_at = now()
WHERE import_run_id = '{run_id}'::uuid;
COMMIT;
"""


def report_query(run_id: str) -> str:
    return f"""SELECT json_build_object(
      'run_id', '{run_id}',
      'state', (SELECT run_state FROM advisor_testimonial_import_runs WHERE import_run_id = '{run_id}'::uuid),
      'real_testimonials', (SELECT count(*) FROM advisor_testimonial_run_records link JOIN advisor_testimonial_records record ON record.testimonial_staging_id = link.testimonial_staging_id WHERE link.import_run_id = '{run_id}'::uuid AND record.source_table = 'testimonials'),
      'usage_patterns', (SELECT count(*) FROM advisor_testimonial_run_records link JOIN advisor_testimonial_records record ON record.testimonial_staging_id = link.testimonial_staging_id WHERE link.import_run_id = '{run_id}'::uuid AND record.source_table = 'usage_patterns'),
      'safety_signals', (SELECT count(*) FROM advisor_testimonial_run_records link JOIN advisor_testimonial_records record ON record.testimonial_staging_id = link.testimonial_staging_id WHERE link.import_run_id = '{run_id}'::uuid AND record.source_table = 'safety_signals'),
      'resource_links', (SELECT count(*) FROM advisor_testimonial_media_candidates WHERE import_run_id = '{run_id}'::uuid),
      'confirmed_video_testimonials', (SELECT count(*) FROM advisor_testimonial_media_candidates WHERE import_run_id = '{run_id}'::uuid AND media_link_status = 'linked_confirmed_testimonial'),
      'video_without_consent', (SELECT count(*) FROM advisor_testimonial_media_candidates WHERE import_run_id = '{run_id}'::uuid AND media_link_status = 'blocked_unconfirmed_testimonial'),
      'non_testimonial_resources', (SELECT count(*) FROM advisor_testimonial_media_candidates WHERE import_run_id = '{run_id}'::uuid AND media_link_status = 'not_testimonial_media'),
      'review_by_type', (SELECT COALESCE(json_object_agg(review_type, total), '{{}}'::json) FROM (SELECT queue.review_type, count(*) AS total FROM advisor_testimonial_review_queue queue JOIN advisor_testimonial_run_records link ON link.testimonial_staging_id = queue.testimonial_staging_id WHERE link.import_run_id = '{run_id}'::uuid GROUP BY queue.review_type) totals),
      'source_flags', (SELECT json_build_object('medical_review_required', count(*) FILTER (WHERE source_medical_review_required), 'safety_class', count(*) FILTER (WHERE testimonial_class = 'safety'), 'negative_class', count(*) FILTER (WHERE testimonial_class = 'negative'), 'marketing_allowed', count(*) FILTER (WHERE source_marketing_allowed)) FROM advisor_testimonial_run_records link JOIN advisor_testimonial_records record ON record.testimonial_staging_id = link.testimonial_staging_id WHERE link.import_run_id = '{run_id}'::uuid),
      'actions', (SELECT COALESCE(json_object_agg(action, total), '{{}}'::json) FROM (SELECT action, count(*) AS total FROM advisor_testimonial_run_records WHERE import_run_id = '{run_id}'::uuid GROUP BY action) totals)
    );"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--full-staging-run", action="store_true")
    parser.add_argument("--no-publish", action="store_true")
    parser.add_argument("--fail-phase", choices=FAULTS)
    parser.add_argument("--schema-file", type=Path, default=Path(__file__).with_name("whieda_testimonials_staging_schema_v2.sql"))
    parser.add_argument("--report-dir", type=Path, default=Path(__file__).resolve().parents[2] / "reports" / "testimonials")
    args = parser.parse_args()
    if not args.no_publish:
        raise SystemExit("--no-publish required")
    if not (args.validate or args.full_staging_run):
        raise SystemExit("Use --validate or --full-staging-run")
    records, media, errors, warnings, manifest = prepare(args.source_dir)
    if errors:
        raise SystemExit(json.dumps({"errors": errors, "warnings": warnings}, ensure_ascii=False))
    if not args.full_staging_run:
        print(json.dumps({"valid_records": len(records), "resource_links": len(media), "warnings": warnings}, ensure_ascii=False))
        return

    psql(["-f", str(args.schema_file)])
    escaped_manifest = json.dumps(manifest, ensure_ascii=False).replace("'", "''")
    run_id = psql(["-Atq", "-c", f"INSERT INTO advisor_testimonial_import_runs(tenant_id, source_manifest) VALUES ('{TENANT}', '{escaped_manifest}'::jsonb) RETURNING import_run_id"]).strip()
    try:
        for record in records:
            record["review_types"] = review_types(record)
        record_csv = io.StringIO()
        csv.writer(record_csv, lineterminator="\n").writerows([
            [run_id, TENANT, record["table"], record["id"], record["hash"], json.dumps(record, ensure_ascii=False)]
            for record in records
        ])
        psql(["-c", "\\copy advisor_testimonial_hardening_buffer(import_run_id,tenant_id,source_table,external_record_id,content_hash,payload) from stdin with(format csv)"], record_csv.getvalue())
        media_csv = io.StringIO()
        csv.writer(media_csv, lineterminator="\n").writerows([
            [run_id, TENANT, item["id"], json.dumps(item, ensure_ascii=False)] for item in media
        ])
        psql(["-c", "\\copy advisor_testimonial_hardening_media_buffer(import_run_id,tenant_id,resource_record_id,payload) from stdin with(format csv)"], media_csv.getvalue())
        psql(["-c", run_sql(run_id, args.fail_phase)])
    except Exception as error:
        message = str(error).replace("'", "''")[:2000]
        psql(["-c", f"DELETE FROM advisor_testimonial_hardening_buffer WHERE import_run_id = '{run_id}'::uuid; DELETE FROM advisor_testimonial_hardening_media_buffer WHERE import_run_id = '{run_id}'::uuid; UPDATE advisor_testimonial_import_runs SET run_state = 'failed', error_text = '{message}', finished_at = now() WHERE import_run_id = '{run_id}'::uuid;"])
        raise

    report = json.loads(psql(["-Atq", "-c", report_query(run_id)]))
    report.update({"warnings": warnings, "generated_at": datetime.now(timezone.utc).isoformat(), "publication": "disabled_by_no_publish"})
    args.report_dir.mkdir(parents=True, exist_ok=True)
    base = args.report_dir / f"RELEASE_2_TESTIMONIALS_STAGING_REPORT_{run_id}"
    base.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    base.with_suffix(".md").write_text("# RELEASE_2_TESTIMONIALS_STAGING_REPORT\n\n" + "\n".join(f"- {key}: {value}" for key, value in report.items()), encoding="utf8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
