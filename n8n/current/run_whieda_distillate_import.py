"""WHIEDA Release 0/1 importer: validated TSV -> isolated staging only.

No command in this runner writes Google Sheets, advisor_structured_* runtime
tables, workflow definitions, or user-facing text. Publication is intentionally
unsupported until a later separately approved release.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

TENANT = "whieda"
INITIAL_FILES = {
    "01_QUESTIONS.tsv": ("questions", "question_id"),
    "02_ALIASES.tsv": ("aliases", "record_id"),
    "04_CLARIFICATION_RULES.tsv": ("clarification_rules", "record_id"),
    "13_SMOKE_CASES.tsv": ("smoke_cases", "case_id"),
    "18_DIALOGUE_FLOWS.tsv": ("dialogue_flows", "flow_id"),
}
INTENT_MAP = {
    "цена": "product_price", "партнерская_цена": "product_price",
    "первичная_покупка": "product_price", "PV": "product_pv",
    "описание": "product_overview", "применение": "product_usage",
    "ограничения": "product_limitations", "сравнение": "product_compare",
    "фото": "product_photo", "видео": "product_video", "документ": "product_document",
    "отзыв": "product_reviews", "возражение": "business_objection",
    "бизнес": "business_faq", "маркетинг_план": "business_faq",
    "другое": "unknown_supported",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def row_hash(row: dict[str, str]) -> str:
    clean = {key: (value or "").strip() for key, value in row.items()}
    return sha256_bytes(json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def psql(args: list[str], *, stdin: str | None = None) -> str:
    binary = shutil.which("psql")
    if not binary:
        raise RuntimeError("psql is not installed or unavailable in PATH")
    env = os.environ.copy()
    env.setdefault("PGHOST", "aws-0-eu-west-1.pooler.supabase.com")
    env.setdefault("PGPORT", "6543")
    env.setdefault("PGDATABASE", "postgres")
    env.setdefault("PGUSER", "postgres.rlrehqqtirwrbaotjcxs")
    env.setdefault("PGCLIENTENCODING", "UTF8")
    if not env.get("PGPASSWORD"):
        raise RuntimeError("PGPASSWORD is required for --stage")
    completed = subprocess.run(
        [binary, "-v", "ON_ERROR_STOP=1", *args], input=stdin, text=True,
        encoding="utf-8", errors="replace", capture_output=True, env=env, check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout


def read_tsv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return reader.fieldnames or [], list(reader)


def source_ids(source_dir: Path) -> set[str]:
    _, rows = read_tsv(source_dir / "00_SOURCE_REGISTER.tsv")
    return {str(row.get("source_id", "")).strip() for row in rows if row.get("source_id")}


def normalize_bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"да", "yes", "true", "1"}


def sql_utf8(value: str) -> str:
    encoded = base64.b64encode((value or "").encode("utf-8")).decode("ascii")
    return f"convert_from(decode('{encoded}','base64'),'UTF8')"


def load_live_intents() -> set[str]:
    text = psql(["-At", "-c", "select intent_id from advisor_structured_intent_registry where client_id='whieda' and enabled is true;"])
    return {line.strip() for line in text.splitlines() if line.strip()}


def validation(source_dir: Path, registry: dict, live_intents: set[str] | None) -> tuple[dict, list[dict]]:
    ids = source_ids(source_dir)
    report = {"files": {}, "accepted": 0, "skipped": 0, "conflicts": 0, "requires_review": 0, "blocked_raw": 0}
    valid: list[dict] = []
    for file_name, (logical, id_field) in INITIAL_FILES.items():
        path = source_dir / file_name
        schema = registry[file_name]
        headers, rows = read_tsv(path)
        errors: list[dict] = []
        expected = schema["columns"]
        if headers != expected:
            errors.append({"row": 1, "reason": "header_mismatch"})
        seen: set[tuple[str, str]] = set()
        for index, row in enumerate(rows, start=2):
            record_id = (row.get(id_field) or "").strip()
            reasons = []
            for field in schema.get("required", []):
                if not (row.get(field) or "").strip(): reasons.append(f"missing:{field}")
            regex = schema.get("id_regex")
            if regex and record_id and not re.match(regex, record_id): reasons.append("invalid_record_id")
            if row.get("source_id", "").strip() not in ids: reasons.append("invalid_source_id")
            key = (record_id, row_hash(row))
            if key in seen: reasons.append("duplicate_record_source_hash")
            seen.add(key)
            gates = []
            if logical == "dialogue_flows":
                flow_type = row.get("flow_type", "")
                verified = normalize_bool(row.get("provenance_verified", ""))
                if flow_type not in {"observed_dialogue", "derived_clarification_candidate"}: reasons.append("invalid_flow_type")
                if flow_type == "observed_dialogue" and not (row.get("follow_up") or "").strip(): reasons.append("observed_without_follow_up")
                if flow_type == "derived_clarification_candidate" and row.get("publication_status") != "blocked_raw": reasons.append("derived_must_be_blocked_raw")
                if flow_type == "derived_clarification_candidate" and not verified: gates.append("derived_unverified_provenance")
            if logical == "questions":
                intent = row.get("намерение", "")
                mapped = INTENT_MAP.get(intent)
                if live_intents is not None and mapped and mapped not in live_intents: gates.append(f"intent_not_live:{mapped}")
                if intent == "проблема_симптом": gates.append("release5_not_release1")
            if reasons:
                errors.append({"row": index, "record_id": record_id, "reason": ";".join(reasons)})
                report["skipped"] += 1
                continue
            status = row.get("publication_status", "").strip() or schema.get("default_publication_status", "blocked_raw")
            item = {"file": file_name, "logical": logical, "id_field": id_field, "row_number": index, "row": row, "status": status, "gates": gates}
            valid.append(item)
            if status == "blocked_raw": report["blocked_raw"] += 1
            elif normalize_bool(row.get("medical_review_required", "")) or normalize_bool(row.get("business_review_required", "")):
                report["requires_review"] += 1
            else: report["accepted"] += 1
        report["files"][file_name] = {"rows": len(rows), "valid": len(rows) - len(errors), "errors": errors}
        report["conflicts"] += sum(1 for error in errors if "intent_not_live" in error["reason"])
    return report, valid


def build_candidates(valid: list[dict], live_intents: set[str]) -> list[dict]:
    candidates = []
    for item in valid:
        row, logical = item["row"], item["logical"]
        blocked = item["status"] == "blocked_raw"
        medical = normalize_bool(row.get("medical_review_required", ""))
        business = normalize_bool(row.get("business_review_required", ""))
        kind = logical.rstrip("s")
        proposed_intent = None
        reasons = []
        if logical == "questions":
            proposed_intent = INTENT_MAP.get(row.get("намерение", ""))
            if not proposed_intent: reasons.append("unmapped_corpus_intent")
            elif proposed_intent not in live_intents: reasons.append("intent_not_live")
            reasons.extend(item.get("gates", []))
        if logical == "dialogue_flows":
            if row.get("flow_type") == "derived_clarification_candidate": reasons.append("derived_dialogue_never_runtime_candidate")
            if not normalize_bool(row.get("provenance_verified", "")): reasons.append("provenance_not_verified")
        if blocked: reasons.append("blocked_raw")
        if medical: reasons.append("medical_review")
        if business: reasons.append("business_review")
        state = "candidate" if not reasons else "review_required"
        candidates.append({"logical": logical, "record_id": row.get(item["id_field"], ""), "candidate_type": kind, "proposed_intent_id": proposed_intent or "", "candidate_status": state, "reason": ";".join(reasons)})
    return candidates


def create_schema(schema_file: Path) -> None:
    psql(["-f", str(schema_file)])


def stage(source_dir: Path, valid: list[dict], report: dict, schema_file: Path, candidates: list[dict]) -> str:
    create_schema(schema_file)
    manifest = []
    for file_name in INITIAL_FILES:
        data = (source_dir / file_name).read_bytes()
        manifest.append({"file": file_name, "sha256": sha256_bytes(data), "bytes": len(data)})
    manifest_json = json.dumps(manifest, ensure_ascii=False).replace("'", "''")
    source_label = "raw_sql_corpus_release_0_1"
    run_id = psql(["-Atq", "-c", f"insert into advisor_distillate_import_runs (tenant_id,source_dir,source_manifest,validation_status,report) values ('{TENANT}','{source_label}','{manifest_json}'::jsonb,'green','{{}}'::jsonb) returning import_run_id;"]).strip().splitlines()[0]
    rows_by_table: dict[str, list[list[str]]] = defaultdict(list)
    dialogue_rows: list[list[str]] = []
    for item in valid:
        row = item["row"]
        logical = item["logical"]
        record_id = row.get(item["id_field"], "")
        source_hash = next(entry["sha256"] for entry in manifest if entry["file"] == item["file"])
        status = item["status"]
        medical_state = "review_required" if normalize_bool(row.get("medical_review_required", "")) else "not_required"
        business_state = "review_required" if normalize_bool(row.get("business_review_required", "")) else "not_required"
        owner_state = "approved" if normalize_bool(row.get("owner_approved", "")) else "not_approved"
        payload = json.dumps(row, ensure_ascii=False)
        rows_by_table[logical].append([run_id, TENANT, logical, record_id, row.get("source_id", ""), row.get("source_locator", ""), row.get("raw_quote", ""), "raw_distillate", row.get("authority_level", ""), row.get("extraction_pass", ""), source_hash, row_hash(row), payload, status, medical_state, business_state, owner_state])
    columns = "import_run_id,tenant_id,source_table,external_record_id,source_id,source_locator,raw_quote,source_type,authority_level,extraction_pass,source_hash,content_hash,payload,publication_status,medical_review_state,business_review_state,owner_state"
    for logical, data in rows_by_table.items():
        if not data: continue
        # Copy to a disposable buffer, then merge with ON CONFLICT. This makes
        # retries idempotent without ever touching runtime tables.
        import io
        buffer = io.StringIO(); writer = csv.writer(buffer, lineterminator="\n"); writer.writerows(data)
        psql(["-c", f"\\copy advisor_distillate_import_buffer ({columns}) FROM STDIN WITH (FORMAT csv)"], stdin=buffer.getvalue())
        psql(["-c", f"insert into advisor_distillate_records ({columns}) select {columns} from advisor_distillate_import_buffer where import_run_id='{run_id}'::uuid and source_table='{logical}' on conflict (tenant_id, source_table, external_record_id, content_hash) do nothing; delete from advisor_distillate_import_buffer where import_run_id='{run_id}'::uuid and source_table='{logical}';"])
    # File source hashes are enough to identify this immutable source batch,
    # and keep the Windows psql command well below its argument limit.
    source_hashes = ",".join("'" + entry["sha256"] + "'" for entry in manifest)
    lookup = psql(["-At", "-F", "\t", "-c", f"select source_table, external_record_id, content_hash, staging_id from advisor_distillate_records where tenant_id='{TENANT}' and source_hash in ({source_hashes});"])
    staging_ids = {}
    for line in lookup.splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            staging_ids[(parts[0], parts[1], parts[2])] = parts[3]
    observed_values, derived_values = [], []
    for item in valid:
        if item["logical"] != "dialogue_flows": continue
        row = item["row"]; sid = staging_ids.get((item["logical"], row.get(item["id_field"], ""), row_hash(row)))
        if not sid: continue
        verified = "true" if normalize_bool(row.get("provenance_verified", "")) else "false"
        if row.get("flow_type") == "observed_dialogue":
            observed_values.append("('%s','%s',%s,%s,%s)" % (sid, run_id, sql_utf8(row.get("source_record_id", "")), verified, sql_utf8(row.get("follow_up", ""))))
        else:
            derived_values.append("('%s','%s',%s,%s,%s,'derived dialogue is review-only')" % (sid, run_id, sql_utf8(row.get("source_record_id", "")), verified, sql_utf8(row.get("уточнение", ""))))
    if observed_values:
        psql(["-c", "insert into advisor_distillate_dialogue_observed (staging_id,import_run_id,source_record_id,provenance_verified,follow_up) values " + ",".join(observed_values) + " on conflict do nothing;"])
    if derived_values:
        psql(["-c", "insert into advisor_distillate_dialogue_derived (staging_id,import_run_id,source_record_id,provenance_verified,clarification_text,publication_block_reason) values " + ",".join(derived_values) + " on conflict do nothing;"])
    import io
    candidate_rows = []
    for candidate in candidates:
        item = next((value for value in valid if value["logical"] == candidate["logical"] and value["row"].get(value["id_field"], "") == candidate["record_id"]), None)
        sid = staging_ids.get((candidate["logical"], candidate["record_id"], row_hash(item["row"]))) if item else None
        if sid: candidate_rows.append([run_id, sid, TENANT, candidate["candidate_type"], candidate["proposed_intent_id"], candidate["candidate_status"], candidate["reason"], "{}"])
    if candidate_rows:
        buffer = io.StringIO(); csv.writer(buffer, lineterminator="\n").writerows(candidate_rows)
        candidate_columns = "import_run_id,staging_id,tenant_id,candidate_type,proposed_intent_id,candidate_status,reason,conflict_json"
        psql(["-c", f"\\copy advisor_distillate_candidate_buffer ({candidate_columns}) FROM STDIN WITH (FORMAT csv)"], stdin=buffer.getvalue())
        psql(["-c", f"insert into advisor_distillate_candidates ({candidate_columns}) select {candidate_columns} from advisor_distillate_candidate_buffer where import_run_id='{run_id}'::uuid on conflict do nothing; delete from advisor_distillate_candidate_buffer where import_run_id='{run_id}'::uuid;"])
    report_json = json.dumps(report, ensure_ascii=False).replace("'", "''")
    psql(["-c", f"update advisor_distillate_import_runs set completed_at=now(), report='{report_json}'::jsonb where import_run_id='{run_id}'::uuid;"])
    return run_id


def write_markdown_report(report: dict, path: Path) -> None:
    lines = [
        "# WHIEDA Release 0/1 staging import report",
        "",
        f"Generated: {report['generated_at']}",
        f"Mode: staging only; publication: {report['publication']}",
        "",
        "## Result",
        f"- Accepted: {report['accepted']}",
        f"- Skipped: {report['skipped']}",
        f"- Conflicts: {report['conflicts']}",
        f"- Requires review: {report['requires_review']}",
        f"- blocked_raw retained only in staging: {report['blocked_raw']}",
        f"- Candidates: {report['candidate_summary'].get('candidate', 0)}",
        f"- Review candidates: {report['candidate_summary'].get('review_required', 0)}",
        "",
        "## Source files",
    ]
    for name, data in report['files'].items():
        lines.append(f"- {name}: {data['valid']}/{data['rows']} structurally valid; errors: {len(data['errors'])}")
    lines.extend([
        "",
        "## Safety gates honoured",
        "- No runtime advisor_structured_* table was written.",
        "- Google Sheets and product-card texts were not touched.",
        "- blocked_raw was not published.",
        "- derived dialogue with unverified provenance was isolated and excluded from runtime candidate promotion.",
        "- observed dialogue and derived clarification candidates are in separate tables.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--schema-file", type=Path, default=Path(__file__).with_name("whieda_distillate_staging_schema_v2.sql"))
    parser.add_argument("--report-dir", type=Path, default=Path(__file__).resolve().parents[2] / "reports" / "distillate")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--stage", action="store_true")
    parser.add_argument("--build-candidates", action="store_true")
    parser.add_argument("--no-publish", action="store_true")
    args = parser.parse_args()
    if not args.no_publish:
        raise SystemExit("Safety gate: this runner requires --no-publish; publication is not implemented.")
    if not (args.validate or args.stage or args.build_candidates):
        raise SystemExit("Choose at least one of --validate, --stage, --build-candidates.")
    registry = json.loads((args.source_dir / "SCHEMA_REGISTRY.json").read_text(encoding="utf-8"))
    live_intents = load_live_intents() if args.stage or args.build_candidates else None
    report, valid = validation(args.source_dir, registry, live_intents)
    candidates = build_candidates(valid, live_intents or set()) if args.build_candidates else []
    report["candidate_summary"] = dict(Counter(item["candidate_status"] for item in candidates))
    report["candidate_reasons"] = dict(Counter(reason for item in candidates for reason in item["reason"].split(";") if reason))
    report["publication"] = "disabled_by_no_publish"
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["source_coverage"] = "partial; staging only"
    run_id = None
    if args.stage:
        if any(data["errors"] for data in report["files"].values()):
            report["stage"] = "not_started_due_to_validation_errors"
        else:
            run_id = stage(args.source_dir, valid, report, args.schema_file, candidates)
            report["stage"] = "completed"
            report["import_run_id"] = run_id
    args.report_dir.mkdir(parents=True, exist_ok=True)
    suffix = run_id or "dry-run"
    (args.report_dir / f"distillate_import_{suffix}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_markdown_report(report, args.report_dir / f"distillate_import_{suffix}.md")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not any(data["errors"] for data in report["files"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
