"""WHIEDA Release 0.1: audited, idempotent raw corpus staging importer.

It only writes advisor_distillate_* staging tables. Publication is impossible
without --no-publish, and this runner contains no runtime/Sheets/card writes.
"""
from __future__ import annotations

import argparse, base64, csv, hashlib, io, json, os, re, shutil, subprocess, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

TENANT = "whieda"
FILES = {
    "01_QUESTIONS.tsv": ("questions", "question_id", "question"),
    "02_ALIASES.tsv": ("aliases", "record_id", "alias"),
    "04_CLARIFICATION_RULES.tsv": ("clarification_rules", "record_id", "clarification_rule"),
    "13_SMOKE_CASES.tsv": ("smoke_cases", "case_id", "smoke_case"),
    "18_DIALOGUE_FLOWS.tsv": ("dialogue_flows", "flow_id", "dialogue_flow"),
}
INTENT_MAP = {
    "цена": "product_price", "партнерская цена": "product_price", "первичная покупка": "product_price",
    "pv": "product_pv", "описание": "product_overview", "применение": "product_usage",
    "ограничения": "product_limitations", "сравнение": "product_compare", "фото": "product_photo",
    "видео": "product_video", "документ": "product_document", "отзыв": "product_reviews",
    "возражение": "business_objection", "бизнес": "business_faq", "маркетинг план": "business_faq",
    "другое": "unknown_supported",
}

def sha(data: bytes) -> str: return hashlib.sha256(data).hexdigest()
def row_hash(row: dict) -> str:
    clean = {k: (v or "").strip() for k, v in row.items()}
    return sha(json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
def truth(value: str) -> bool: return str(value or "").strip().lower() in {"да","yes","true","1"}
def normalize_intent(value: str) -> str:
    value = (value or "").strip().lower().replace("ё", "е")
    value = re.sub(r"[_/|,;:+\\-]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()
def sql_utf8(value: str) -> str:
    payload = base64.b64encode((value or "").encode()).decode()
    return f"convert_from(decode('{payload}','base64'),'UTF8')"

def psql(args: list[str], stdin: str | None = None) -> str:
    binary = shutil.which("psql")
    if not binary: raise RuntimeError("psql unavailable")
    env = os.environ.copy()
    env["PGHOST"] = "aws-0-eu-west-1.pooler.supabase.com"; env["PGPORT"] = "6543"
    env["PGDATABASE"] = "postgres"; env["PGUSER"] = "postgres.rlrehqqtirwrbaotjcxs"
    env["PGCLIENTENCODING"] = "UTF8"
    if not env.get("PGPASSWORD"): raise RuntimeError("PGPASSWORD required for staging")
    done = subprocess.run([binary,"-h",env["PGHOST"],"-p",env["PGPORT"],"-d",env["PGDATABASE"],"-U",env["PGUSER"],"-v","ON_ERROR_STOP=1",*args], input=stdin, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if done.returncode: raise RuntimeError(done.stderr.strip() or done.stdout.strip())
    return done.stdout

def tsv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter="\t"); return reader.fieldnames or [], list(reader)
def live_intents() -> set[str]:
    text = psql(["-Atq","-c","select intent_id from advisor_structured_intent_registry where client_id='whieda' and enabled;"])
    return {x.strip() for x in text.splitlines() if x.strip()}

def validate(source: Path, registry: dict, live: set[str]):
    _, source_rows = tsv(source / "00_SOURCE_REGISTER.tsv")
    source_ids = {x.get("source_id","").strip() for x in source_rows}
    rows, files, rejected = [], {}, []
    for filename, (logical, id_field, candidate_type) in FILES.items():
        headers, data = tsv(source / filename); schema = registry[filename]; errors=[]; seen=set()
        if headers != schema["columns"]: errors.append({"row":1,"reason":"header_mismatch"})
        for number, row in enumerate(data, 2):
            record_id=(row.get(id_field) or "").strip(); reasons=[]; content=row_hash(row)
            for field in schema.get("required",[]):
                if not (row.get(field) or "").strip(): reasons.append("missing:"+field)
            if schema.get("id_regex") and record_id and not re.match(schema["id_regex"],record_id): reasons.append("invalid_record_id")
            if row.get("source_id","").strip() not in source_ids: reasons.append("invalid_source_id")
            if (record_id,content) in seen: reasons.append("duplicate_record_source_hash")
            seen.add((record_id,content))
            if logical == "dialogue_flows":
                if row.get("flow_type") not in {"observed_dialogue","derived_clarification_candidate"}: reasons.append("invalid_flow_type")
                if row.get("flow_type")=="observed_dialogue" and not row.get("follow_up","").strip(): reasons.append("observed_without_follow_up")
                if row.get("flow_type")=="derived_clarification_candidate" and row.get("publication_status")!="blocked_raw": reasons.append("derived_must_be_blocked_raw")
            if reasons:
                errors.append({"row":number,"record_id":record_id,"reason":";".join(reasons)}); rejected.append((filename,number,row,";".join(reasons))); continue
            rows.append({"file":filename,"logical":logical,"id_field":id_field,"candidate_type":candidate_type,"row":row,"record_id":record_id,"content_hash":content})
        files[filename]={"rows":len(data),"valid":len(data)-len(errors),"errors":errors}
    return rows, files, rejected

def evaluate(item: dict, live: set[str]) -> dict:
    row=item["row"]; reasons=[]; mapped=""
    if item["logical"] == "questions":
        normalized=normalize_intent(row.get("намерение","")); mapped=INTENT_MAP.get(normalized,"")
        if not mapped: reasons.append("intent_gap")
        elif mapped not in live: reasons.append("intent_gap:intent_not_live")
        if normalized == "проблема симптом": reasons.append("release5_not_release1")
    if item["logical"] == "dialogue_flows":
        if row.get("flow_type") == "derived_clarification_candidate": reasons.append("derived_dialogue_never_runtime_candidate")
        if not truth(row.get("provenance_verified","")): reasons.append("provenance_gap")
    if row.get("publication_status","").strip() == "blocked_raw": reasons.append("blocked_raw")
    if truth(row.get("medical_review_required","")): reasons.append("medical")
    if truth(row.get("business_review_required","")): reasons.append("business")
    return {"candidate_status":"candidate" if not reasons else "review_required", "proposed_intent_id":mapped, "reason":";".join(reasons), "conflict_json":json.dumps({"reasons":reasons,"normalized_intent":normalize_intent(row.get("намерение",""))},ensure_ascii=False)}

def create_run(source: Path, manifest: list[dict]) -> str:
    manifest_json=json.dumps(manifest,ensure_ascii=False).replace("'","''")
    sql=("insert into advisor_distillate_import_runs (tenant_id,source_dir,source_manifest,validation_status,run_state,report) values "
         f"('{TENANT}','raw_sql_corpus_release_0_1','{manifest_json}'::jsonb,'green','running','{{}}') returning import_run_id")
    return psql(["-Atq","-c",sql]).strip().splitlines()[0]

def fail_run(run_id: str, error: Exception):
    message=str(error).replace("'","''")[:4000]
    # Candidate/review changes run in one transaction. If a prior records
    # phase failed, only records first introduced by this run are removed.
    psql(["-c",f"begin; delete from advisor_distillate_import_buffer where import_run_id='{run_id}'::uuid; delete from advisor_distillate_candidate_buffer where import_run_id='{run_id}'::uuid; delete from advisor_distillate_review_buffer where import_run_id='{run_id}'::uuid; delete from advisor_distillate_run_records where import_run_id='{run_id}'::uuid; delete from advisor_distillate_records where first_seen_run_id='{run_id}'::uuid; update advisor_distillate_import_runs set run_state='failed', validation_status='failed', error_text='{message}', finished_at=now() where import_run_id='{run_id}'::uuid; commit;"])

def stage(source: Path, rows: list[dict], live: set[str], schema: Path, failure_phase: str | None = None) -> str:
    psql(["-f",str(schema)])
    manifest=[{"file":name,"sha256":sha((source/name).read_bytes()),"bytes":(source/name).stat().st_size} for name in FILES]
    run_id=create_run(source,manifest)
    try:
        file_hash={x["file"]:x["sha256"] for x in manifest}; cols="import_run_id,tenant_id,source_table,external_record_id,source_id,source_locator,raw_quote,source_type,authority_level,extraction_pass,source_hash,content_hash,payload,publication_status,medical_review_state,business_review_state,owner_state"
        batches=defaultdict(list)
        for item in rows:
            r=item["row"]; batches[item["logical"]].append([run_id,TENANT,item["logical"],item["record_id"],r.get("source_id",""),r.get("source_locator",""),r.get("raw_quote",""),"raw_distillate",r.get("authority_level",""),r.get("extraction_pass",""),file_hash[item["file"]],item["content_hash"],json.dumps(r,ensure_ascii=False),r.get("publication_status","") or "blocked_raw","review_required" if truth(r.get("medical_review_required","")) else "not_required","review_required" if truth(r.get("business_review_required","")) else "not_required","approved" if truth(r.get("owner_approved","")) else "not_approved"])
        for data in batches.values():
            buff=io.StringIO(); csv.writer(buff,lineterminator="\n").writerows(data)
            psql(["-c",f"\\copy advisor_distillate_import_buffer ({cols}) FROM STDIN WITH (FORMAT csv)"],buff.getvalue())
        psql(["-c",f"insert into advisor_distillate_records ({cols},first_seen_run_id,last_seen_run_id) select {cols},'{run_id}'::uuid,'{run_id}'::uuid from advisor_distillate_import_buffer where import_run_id='{run_id}'::uuid on conflict (tenant_id,source_table,external_record_id,content_hash) do nothing;"])
        lookup=psql(["-Atq","-F","\t","-c",f"select b.source_table,b.external_record_id,b.content_hash,r.staging_id,case when r.first_seen_run_id='{run_id}'::uuid then case when exists(select 1 from advisor_distillate_records p where p.tenant_id=b.tenant_id and p.source_table=b.source_table and p.external_record_id=b.external_record_id and p.content_hash<>b.content_hash) then 'new_version' else 'inserted' end else 'reused' end from advisor_distillate_import_buffer b join advisor_distillate_records r on r.tenant_id=b.tenant_id and r.source_table=b.source_table and r.external_record_id=b.external_record_id and r.content_hash=b.content_hash where b.import_run_id='{run_id}'::uuid;"])
        identities={}; links=[]
        for line in lookup.splitlines():
            logical,record,content,sid,action=line.split("\t"); identities[(logical,record,content)]=sid; links.append([run_id,sid,action,logical,record,content])
        buff=io.StringIO(); csv.writer(buff,lineterminator="\n").writerows(links)
        psql(["-c","\\copy advisor_distillate_run_records (import_run_id,staging_id,action,source_table,external_record_id,content_hash) FROM STDIN WITH (FORMAT csv)"],buff.getvalue())
        psql(["-c",f"update advisor_distillate_records r set last_seen_run_id='{run_id}'::uuid,updated_at=now() from advisor_distillate_import_buffer b where b.import_run_id='{run_id}'::uuid and r.tenant_id=b.tenant_id and r.source_table=b.source_table and r.external_record_id=b.external_record_id and r.content_hash=b.content_hash; delete from advisor_distillate_import_buffer where import_run_id='{run_id}'::uuid;"])
        if failure_phase == "after_records_lineage": raise RuntimeError("simulated_after_records_lineage")
        candidate_rows=[]; observed=[]; derived=[]
        for item in rows:
            sid=identities[(item["logical"],item["record_id"],item["content_hash"])]; ev=evaluate(item,live); r=item["row"]
            candidate_rows.append([run_id,sid,TENANT,item["candidate_type"],ev["proposed_intent_id"],ev["candidate_status"],ev["reason"],ev["conflict_json"]])
            if item["logical"]=="dialogue_flows":
                if r.get("flow_type")=="observed_dialogue": observed.append(f"('{sid}','{run_id}',{sql_utf8(r.get('source_record_id',''))},{str(truth(r.get('provenance_verified',''))).lower()},{sql_utf8(r.get('follow_up',''))})")
                else: derived.append(f"('{sid}','{run_id}',{sql_utf8(r.get('source_record_id',''))},{str(truth(r.get('provenance_verified',''))).lower()},{sql_utf8(r.get('уточнение',''))},'derived dialogue is review-only')")
        if observed: psql(["-c","insert into advisor_distillate_dialogue_observed(staging_id,import_run_id,source_record_id,provenance_verified,follow_up) values "+",".join(observed)+" on conflict(staging_id) do update set import_run_id=excluded.import_run_id,source_record_id=excluded.source_record_id,provenance_verified=excluded.provenance_verified,follow_up=excluded.follow_up;"])
        if derived: psql(["-c","insert into advisor_distillate_dialogue_derived(staging_id,import_run_id,source_record_id,provenance_verified,clarification_text,publication_block_reason) values "+",".join(derived)+" on conflict(staging_id) do update set import_run_id=excluded.import_run_id,source_record_id=excluded.source_record_id,provenance_verified=excluded.provenance_verified,clarification_text=excluded.clarification_text;"])
        cand_cols="import_run_id,staging_id,tenant_id,candidate_type,proposed_intent_id,candidate_status,reason,conflict_json"; buff=io.StringIO(); csv.writer(buff,lineterminator="\n").writerows(candidate_rows)
        psql(["-c",f"\\copy advisor_distillate_candidate_buffer ({cand_cols}) FROM STDIN WITH (FORMAT csv)"],buff.getvalue())
        # Candidate upsert, audit and review queue form one atomic phase.
        fault_after_candidate = "select 1/0;" if failure_phase == "after_candidate_upsert" else ""
        fault_after_review = "select 1/0;" if failure_phase == "after_review_queue" else ""
        psql(["-c",f"begin; insert into advisor_distillate_candidates ({cand_cols},last_evaluated_run_id,updated_at) select {cand_cols},'{run_id}'::uuid,now() from advisor_distillate_candidate_buffer where import_run_id='{run_id}'::uuid on conflict(staging_id,candidate_type) do update set candidate_status=excluded.candidate_status,proposed_intent_id=excluded.proposed_intent_id,reason=excluded.reason,conflict_json=excluded.conflict_json,last_evaluated_run_id=excluded.last_evaluated_run_id,updated_at=now(); {fault_after_candidate} insert into advisor_distillate_candidate_audit(candidate_id,import_run_id,candidate_status,proposed_intent_id,reason,conflict_json) select c.candidate_id,'{run_id}'::uuid,c.candidate_status,c.proposed_intent_id,c.reason,c.conflict_json from advisor_distillate_candidates c join advisor_distillate_candidate_buffer b on b.staging_id=c.staging_id and b.candidate_type=c.candidate_type where b.import_run_id='{run_id}'::uuid on conflict do nothing; insert into advisor_distillate_review_buffer(import_run_id,staging_id,tenant_id,review_type) select '{run_id}'::uuid,c.staging_id,c.tenant_id,x.review_type from advisor_distillate_candidates c cross join lateral unnest(string_to_array(c.reason,';')) token join lateral (select case when token='medical' then 'medical' when token='business' then 'business' when token='blocked_raw' then 'blocked_raw' when token like 'intent_gap%' then 'intent_gap' when token='provenance_gap' then 'provenance_gap' end as review_type) x on true where c.last_evaluated_run_id='{run_id}'::uuid and c.candidate_status='review_required' and x.review_type is not null; update advisor_distillate_review_queue q set queue_status='resolved',last_evaluated_run_id='{run_id}'::uuid,updated_at=now() from advisor_distillate_candidates c where c.staging_id=q.staging_id and c.last_evaluated_run_id='{run_id}'::uuid and not exists(select 1 from advisor_distillate_review_buffer b where b.import_run_id='{run_id}'::uuid and b.staging_id=q.staging_id and b.review_type=q.review_type); insert into advisor_distillate_review_queue(staging_id,tenant_id,review_type,priority,last_evaluated_run_id) select staging_id,tenant_id,review_type,priority,'{run_id}'::uuid from advisor_distillate_review_buffer where import_run_id='{run_id}'::uuid on conflict(staging_id,review_type) do update set queue_status='pending',last_evaluated_run_id=excluded.last_evaluated_run_id,updated_at=now(); {fault_after_review} delete from advisor_distillate_review_buffer where import_run_id='{run_id}'::uuid; delete from advisor_distillate_candidate_buffer where import_run_id='{run_id}'::uuid; commit;"])
        psql(["-c",f"update advisor_distillate_import_runs set run_state='completed', validation_status='green', finished_at=now(), completed_at=now() where import_run_id='{run_id}'::uuid;"])
        return run_id
    except Exception as error:
        fail_run(run_id,error); raise

def db_report(run_id: str, files: dict) -> dict:
    sql=f"select json_build_object('run_state',(select run_state from advisor_distillate_import_runs where import_run_id='{run_id}'::uuid),'staged_records',(select count(*) from advisor_distillate_run_records where import_run_id='{run_id}'::uuid),'actions',(select coalesce(json_object_agg(action,n),'{{}}'::json) from (select action,count(*) n from advisor_distillate_run_records where import_run_id='{run_id}'::uuid group by action)s),'candidate',(select count(*) from advisor_distillate_candidates where last_evaluated_run_id='{run_id}'::uuid and candidate_status='candidate'),'review_required',(select count(*) from advisor_distillate_candidates where last_evaluated_run_id='{run_id}'::uuid and candidate_status='review_required'),'queue_items',(select count(*) from advisor_distillate_review_queue where last_evaluated_run_id='{run_id}'::uuid and queue_status='pending'),'conflicts',(select count(*) from advisor_distillate_candidates where last_evaluated_run_id='{run_id}'::uuid and reason like '%intent_gap%'));"
    data=json.loads(psql(["-Atq","-c",sql])); return {"run_id":run_id,"files":files,"db":data,"publication":"disabled_by_no_publish","generated_at":datetime.now(timezone.utc).isoformat()}

def write_report(report: dict, out: Path):
    db=report["db"]; lines=["# RELEASE_0_1_HARDENING_REPORT","",f"Run: `{report['run_id']}`",f"State: `{db['run_state']}`","","## Database result",f"- Staged records considered: {db['staged_records']}",f"- Actions: {json.dumps(db['actions'],ensure_ascii=False)}",f"- Candidate: {db['candidate']}",f"- Review required: {db['review_required']}",f"- Review queue items: {db['queue_items']}",f"- Conflicts (candidate intent_gap): {db['conflicts']}","","## Guards","- Runtime, Google Sheets, product cards and workflow definitions were not changed.","- blocked_raw remains staging-only.","- observed and derived dialogue remain separated."]
    for name,x in report["files"].items(): lines.append(f"- {name}: {x['valid']}/{x['rows']} valid")
    out.write_text("\n".join(lines)+"\n",encoding="utf-8")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--source-dir",required=True,type=Path); ap.add_argument("--schema-file",type=Path,default=Path(__file__).with_name("whieda_distillate_staging_schema_v3.sql")); ap.add_argument("--migration-file",type=Path,default=Path(__file__).with_name("whieda_distillate_staging_v0_1_one_time_migration.sql")); ap.add_argument("--report-dir",type=Path,default=Path(__file__).resolve().parents[2]/"reports"/"distillate"); ap.add_argument("--live-intents-file",type=Path); ap.add_argument("--validate",action="store_true"); ap.add_argument("--full-staging-run",action="store_true"); ap.add_argument("--no-publish",action="store_true"); ap.add_argument("--migrate",action="store_true"); ap.add_argument("--simulate-failure-phase",choices=["after_records_lineage","after_candidate_upsert","after_review_queue"]); args=ap.parse_args()
    if not args.no_publish: raise SystemExit("Safety gate: --no-publish is mandatory")
    if not (args.validate or args.full_staging_run): raise SystemExit("Use --validate or --full-staging-run")
    if args.migrate: psql(["-f",str(args.migration_file)])
    registry=json.loads((args.source_dir/"SCHEMA_REGISTRY.json").read_text(encoding="utf-8")); live=(set(json.loads(args.live_intents_file.read_text(encoding="utf-8"))) if args.live_intents_file else live_intents()) if args.full_staging_run else set(); rows,files,rejected=validate(args.source_dir,registry,live)
    if rejected: raise SystemExit("Validation errors prevent staging")
    if not args.full_staging_run:
        print(json.dumps({"files":files,"valid":len(rows),"publication":"disabled_by_no_publish"},ensure_ascii=False)); return
    run_id=stage(args.source_dir,rows,live,args.schema_file,args.simulate_failure_phase); report=db_report(run_id,files); args.report_dir.mkdir(parents=True,exist_ok=True); (args.report_dir/f"RELEASE_0_1_HARDENING_REPORT_{run_id}.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"); write_report(report,args.report_dir/f"RELEASE_0_1_HARDENING_REPORT_{run_id}.md"); print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
