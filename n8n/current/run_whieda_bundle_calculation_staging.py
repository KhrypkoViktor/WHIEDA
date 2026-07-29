#!/usr/bin/env python3
"""Release 3.2: normalize bundle items and calculate only confirmed catalog products."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import requests

SHEET_ID = "1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4"
TENANT_ID = "whieda"
PRODUCT_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=tsv&gid=1035748906"
ALIAS_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=tsv&gid=2001001"
PRICE_FIELDS = (
    "retail_price_rub", "retail_price_byn", "retail_w",
    "partner_price_rub", "partner_price_byn", "partner_w", "partner_points",
)
SPLIT_RE = re.compile(r"\s*(?:,|;|\+|/)\s*")
PAREN_RE = re.compile(r"\(([^)]*)\)")
QUANTITY_RE = re.compile(r"^\s*(\d+)\s*(?:шт\.?|штук|уп\.?|упак(?:овка|овки)?|флак(?:он|она|онов)?)\s+", re.I)
DOSAGE_RE = re.compile(r"\b\d+(?:[,.]\d+)?\s*(?:мг|г|мл|л|капсул\w*|таблет\w*|капл\w*|раз\w*)\b", re.I)
GENERIC_NAMES = {
    "различные добавки", "добавки", "минералы", "водородная вода", "витамины", "бады", "эликсиры",
}
PARSING_NOISE_NAMES = {"день"}
COLOR_COMPANION_NAMES = {
    "красный": "эликсир фохоу",
    "зеленый": "эликсир саньцин",
    "синий": "эликсир 3 драгоценности",
}
EXTERNAL_NAMES = {
    "чип из прокладки", "чип", "компресс", "вода", "питание", "массаж",
}


def normalize(value: str | None) -> str:
    text = (value or "").lower().replace("ё", "е").replace('"', "")
    text = re.sub(r"[«»]", "", text)
    text = re.sub(r"[-–—]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .,:;-")


def number(value: str | None) -> float | None:
    text = str(value or "").replace("\u00a0", "").replace(" ", "").replace(",", ".")
    if not text or text == "-":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def sql_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def run_sql(sql: str, *, tuples_only: bool = False, timeout: int = 120) -> str:
    fd, filename = tempfile.mkstemp(prefix="whieda-bundle-calc-", suffix=".sql")
    path = Path(filename)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(sql)
        command = ["psql", "-X", "-v", "ON_ERROR_STOP=1"]
        if tuples_only:
            command.append("-Atq")
        command.extend(["-f", str(path)])
        env = os.environ.copy()
        env["PGCLIENTENCODING"] = "UTF8"
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=env,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise RuntimeError(f"psql timeout after {timeout}s; child terminated")
        if process.returncode:
            raise RuntimeError((stderr or stdout or "psql failed")[-3000:])
        return stdout.strip()
    finally:
        path.unlink(missing_ok=True)


def read_sheet(url: str) -> list[dict[str, str]]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(
                f"{url}&cache_bust={int(time.time())}-{attempt}", timeout=45,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            response.raise_for_status()
            response.encoding = "utf-8"
            return read_rows(io.StringIO(response.text))
        except requests.RequestException as error:
            last_error = error
    raise RuntimeError(f"Google Sheet export failed after 3 attempts: {last_error}")


def read_rows(handle: io.TextIOBase) -> list[dict[str, str]]:
    return [
        {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}
        for row in csv.DictReader(handle, delimiter="\t")
        if any(str(value or "").strip() for value in row.values())
    ]


def read_tsv_file(path: str) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return read_rows(handle)


def current_bundles() -> list[dict[str, object]]:
    query = """SELECT coalesce(jsonb_agg(jsonb_build_object(
      'bundle_staging_id',bundle_staging_id,'primary_product',primary_product,
      'additional_products',additional_products,'medical',medical_review_required,
      'business',business_review_required,'owner',owner_approved) ORDER BY external_record_id),'[]'::jsonb)
    FROM advisor_bundle_staging_records WHERE tenant_id='whieda' AND version_state='current';"""
    return json.loads(run_sql(query, tuples_only=True, timeout=45))


def curated_aliases() -> list[dict[str, str]]:
    path = Path(__file__).with_name("whieda_bundle_aliases_v1.json")
    return json.loads(path.read_text(encoding="utf-8"))


def split_product_text(value: object, source_stage: str) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for source_fragment in SPLIT_RE.split(str(value or "")):
        raw = source_fragment.strip()
        if not raw:
            continue
        parentheticals = [part.strip() for part in PAREN_RE.findall(raw) if part.strip()]
        without_parentheses = PAREN_RE.sub(" ", raw)
        quantity = 1
        quantity_match = QUANTITY_RE.match(without_parentheses)
        if quantity_match:
            quantity = int(quantity_match.group(1))
            without_parentheses = without_parentheses[quantity_match.end():]
        dosage_parts = DOSAGE_RE.findall(without_parentheses)
        clean_name = DOSAGE_RE.sub(" ", without_parentheses)
        clean_name = normalize(clean_name)
        clean_name = re.sub(r"^(?:далее|затем)\s+", "", clean_name)
        if clean_name in PARSING_NOISE_NAMES:
            continue
        if not clean_name:
            continue
        result.append({
            "source_name": raw,
            "normalized_name": clean_name,
            "quantity": quantity,
            "stage_text": source_stage,
            "dosage_text": "; ".join([*dosage_parts, *parentheticals]) or None,
        })
    return result


def normalized_bundle_items(primary: object, additional: object) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    raw_items: list[dict[str, object]] = []
    for source_stage, source in (("primary", primary), ("additional", additional)):
        raw_items.extend(split_product_text(source, source_stage))
    all_names = {str(item["normalized_name"]) for item in raw_items}
    for item in raw_items:
        companion = COLOR_COMPANION_NAMES.get(str(item["normalized_name"]))
        if companion and companion in all_names:
            item["normalized_name"] = companion
    for item in raw_items:
            key = str(item["normalized_name"])
            previous = grouped.get(key)
            if previous is None:
                item["source_fragments"] = [item["source_name"]]
                grouped[key] = item
                continue
            previous["quantity"] = int(previous["quantity"]) + int(item["quantity"])
            previous["source_fragments"].append(item["source_name"])
            previous["stage_text"] = "+".join(dict.fromkeys(
                str(previous["stage_text"]).split("+") + [str(item["stage_text"])]
            ))
            existing_dosage = [part for part in (previous.get("dosage_text"), item.get("dosage_text")) if part]
            previous["dosage_text"] = "; ".join(dict.fromkeys(existing_dosage)) or None
    return list(grouped.values())


def classify_item(
    item: dict[str, object], names: dict[str, set[str]], catalog: dict[str, dict[str, object]],
) -> tuple[str, str | None, list[str], str | None]:
    normalized_name = str(item["normalized_name"])
    if normalized_name in GENERIC_NAMES or normalized_name.startswith((
        "витамин", "коэнзим", "q10", "железо", "селен", "детокс идеал",
    )):
        return "generic", None, [], "generic_non_catalog"
    if normalized_name in EXTERNAL_NAMES or normalized_name.startswith("чип"):
        return "external", None, [], "external_non_catalog"
    candidates = sorted(names.get(normalized_name, set()))
    if len(candidates) == 1 and catalog[candidates[0]]["active"]:
        return "catalog_product", candidates[0], candidates, None
    if len(candidates) == 1:
        return "unknown", None, candidates, "inactive_catalog_sku"
    if len(candidates) > 1:
        return "unknown", None, candidates, "ambiguous_alias"
    return "unknown", None, [], "unmapped_name"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-publish", action="store_true", required=True)
    parser.add_argument("--ensure-schema", action="store_true")
    parser.add_argument("--products-file")
    parser.add_argument("--aliases-file")
    parser.add_argument("--fail-before-commit", action="store_true")
    args = parser.parse_args()
    if args.ensure_schema:
        schema = Path(__file__).with_name("whieda_bundle_calculation_staging_schema_v1.sql")
        run_sql(schema.read_text(encoding="utf-8"), timeout=60)

    products = read_tsv_file(args.products_file) if args.products_file else read_sheet(PRODUCT_URL)
    aliases = read_tsv_file(args.aliases_file) if args.aliases_file else read_sheet(ALIAS_URL)
    curated = curated_aliases()
    source_hash = hashlib.sha256(json.dumps([products, aliases, curated], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    catalog: dict[str, dict[str, object]] = {}
    names: defaultdict[str, set[str]] = defaultdict(set)
    alias_rows: list[dict[str, str]] = []
    for row in products:
        sku = row.get("sku", "")
        if not sku:
            continue
        active = row.get("active", "true").lower() not in {"false", "0", "нет", "inactive"}
        catalog[sku] = {
            "sku": sku, "canonical_name": row.get("canonical_name", ""), "active": active,
            **{field: number(row.get(field)) for field in PRICE_FIELDS},
        }
        canonical = normalize(row.get("canonical_name"))
        if canonical:
            names[canonical].add(sku)
            alias_rows.append({"alias": canonical, "sku": sku, "name": row.get("canonical_name", ""), "source_kind": "catalog"})
    for row in aliases:
        sku = row.get("canonical_sku", "")
        alias = normalize(row.get("alias"))
        if sku in catalog and alias and row.get("active", "true").lower() not in {"false", "0", "нет"}:
            names[alias].add(sku)
            alias_rows.append({"alias": alias, "sku": sku, "name": catalog[sku]["canonical_name"], "source_kind": "catalog"})
    for row in curated:
        alias = normalize(row["alias"])
        sku = row["sku"]
        if sku in catalog:
            names[alias].add(sku)
            alias_rows.append({"alias": alias, "sku": sku, "name": row["name"], "source_kind": "curated_bundle"})

    # The dictionary stores one approved binding per normalized spelling.
    # Curated bundle aliases intentionally override a duplicate catalog spelling.
    distinct_aliases: dict[str, dict[str, str]] = {}
    for row in alias_rows:
        existing = distinct_aliases.get(row["alias"])
        if existing is None or row["source_kind"] == "curated_bundle":
            distinct_aliases[row["alias"]] = row
    alias_rows = list(distinct_aliases.values())

    bundles = current_bundles()
    catalog_payload = list(catalog.values())
    item_payload: list[dict[str, object]] = []
    calculation_payload: list[dict[str, object]] = []
    state_counts: defaultdict[str, int] = defaultdict(int)
    for bundle in bundles:
        resolved: list[dict[str, object]] = []
        totals = {field: 0.0 for field in PRICE_FIELDS}
        missing_fields: set[str] = set()
        statuses: set[str] = set()
        for item in normalized_bundle_items(bundle.get("primary_product"), bundle.get("additional_products")):
            status, sku, candidates, reason = classify_item(item, names, catalog)
            statuses.add(status)
            evidence = {"candidates": candidates, "reason": reason, "source_fragments": item["source_fragments"]}
            item_payload.append({
                **item,
                "bundle_staging_id": bundle["bundle_staging_id"],
                "sku": sku,
                "item_status": status,
                "match_state": status,
                "evidence": evidence,
            })
            if status != "catalog_product" or not sku:
                continue
            quantity = int(item["quantity"])
            item_missing = []
            for field in PRICE_FIELDS:
                value = catalog[sku][field]
                if value is None:
                    missing_fields.add(field)
                    item_missing.append(field)
                else:
                    totals[field] += float(value) * quantity
            resolved.append({"sku": sku, "quantity": quantity, "missing_fields": item_missing})
        if "unknown" in statuses:
            calculation_state = "requires_binding"
        elif missing_fields:
            calculation_state = "missing_price"
        elif statuses & {"generic", "external"}:
            calculation_state = "partial_non_catalog"
        elif bundle["medical"] or bundle["business"] or not bundle["owner"]:
            calculation_state = "awaiting_approval"
        else:
            calculation_state = "fully_calculated"
        state_counts[calculation_state] += 1
        calculation_payload.append({
            "bundle_staging_id": bundle["bundle_staging_id"], "state": calculation_state,
            "totals": {field: (None if field in missing_fields else value) for field, value in totals.items()},
            "items": resolved, "missing_fields": sorted(missing_fields),
            "non_catalog_statuses": sorted(statuses & {"generic", "external"}),
        })

    catalog_json = sql_literal(json.dumps(catalog_payload, ensure_ascii=False))
    aliases_json = sql_literal(json.dumps(alias_rows, ensure_ascii=False))
    items_json = sql_literal(json.dumps(item_payload, ensure_ascii=False))
    calculations_json = sql_literal(json.dumps(calculation_payload, ensure_ascii=False))
    bundle_ids = sql_literal(json.dumps([row["bundle_staging_id"] for row in bundles]))
    injected_failure = "DO $$ BEGIN RAISE EXCEPTION 'injected failure before calculation commit'; END $$;" if args.fail_before_commit else ""
    sql = f"""
SET lock_timeout='10s'; SET statement_timeout='90s'; BEGIN;
SELECT pg_advisory_xact_lock(hashtext('whieda:bundle-calculation-staging'));
CREATE TEMP TABLE calculation_snapshot ON COMMIT DROP AS
WITH snapshot AS (
  INSERT INTO advisor_bundle_catalog_snapshots(tenant_id,source_url,source_hash)
  VALUES({sql_literal(TENANT_ID)},{sql_literal(PRODUCT_URL)},{sql_literal(source_hash)})
  ON CONFLICT(tenant_id,source_hash) DO UPDATE SET source_url=EXCLUDED.source_url
  RETURNING snapshot_id
) SELECT snapshot_id FROM snapshot;
INSERT INTO advisor_bundle_catalog_snapshot_items(
 snapshot_id,sku,canonical_name,retail_price_rub,retail_price_byn,retail_w,
 partner_price_rub,partner_price_byn,partner_w,partner_pv,active)
SELECT (SELECT snapshot_id FROM calculation_snapshot),x.sku,x.canonical_name,x.retail_price_rub,x.retail_price_byn,x.retail_w,
 x.partner_price_rub,x.partner_price_byn,x.partner_w,x.partner_points,x.active
FROM jsonb_to_recordset({catalog_json}::jsonb) AS x(
 sku text,canonical_name text,retail_price_rub numeric,retail_price_byn numeric,retail_w numeric,
 partner_price_rub numeric,partner_price_byn numeric,partner_w numeric,partner_points numeric,active boolean)
ON CONFLICT(snapshot_id,sku) DO UPDATE SET canonical_name=EXCLUDED.canonical_name,
 retail_price_rub=EXCLUDED.retail_price_rub,retail_price_byn=EXCLUDED.retail_price_byn,retail_w=EXCLUDED.retail_w,
 partner_price_rub=EXCLUDED.partner_price_rub,partner_price_byn=EXCLUDED.partner_price_byn,
 partner_w=EXCLUDED.partner_w,partner_pv=EXCLUDED.partner_pv,active=EXCLUDED.active;
INSERT INTO advisor_bundle_alias_dictionary(normalized_alias,canonical_sku,canonical_name,source_kind)
SELECT x.alias,x.sku,x.name,x.source_kind
FROM jsonb_to_recordset({aliases_json}::jsonb) AS x(alias text,sku text,name text,source_kind text)
ON CONFLICT(normalized_alias) DO UPDATE SET canonical_sku=EXCLUDED.canonical_sku,
 canonical_name=EXCLUDED.canonical_name,source_kind=EXCLUDED.source_kind,updated_at=now();
DELETE FROM advisor_bundle_item_staging
WHERE bundle_staging_id IN (SELECT value::text::uuid FROM jsonb_array_elements_text({bundle_ids}::jsonb));
INSERT INTO advisor_bundle_item_staging(
 bundle_staging_id,source_name,normalized_name,sku,quantity,item_status,stage_text,dosage_text,source_fragments,match_state,match_evidence)
SELECT x.bundle_staging_id,x.source_name,x.normalized_name,x.sku,x.quantity,x.item_status,x.stage_text,NULLIF(x.dosage_text,''),x.source_fragments,x.match_state,x.evidence
FROM jsonb_to_recordset({items_json}::jsonb) AS x(
 bundle_staging_id uuid,source_name text,normalized_name text,sku text,quantity integer,item_status text,stage_text text,dosage_text text,
 source_fragments jsonb,match_state text,evidence jsonb);
INSERT INTO advisor_bundle_calculation_snapshots(
 bundle_staging_id,catalog_snapshot_id,calculation_state,retail_rub,retail_byn,retail_w,
 partner_rub,partner_byn,partner_w,partner_pv,details)
SELECT x.bundle_staging_id,(SELECT snapshot_id FROM calculation_snapshot),x.state,
 (x.totals->>'retail_price_rub')::numeric,(x.totals->>'retail_price_byn')::numeric,(x.totals->>'retail_w')::numeric,
 (x.totals->>'partner_price_rub')::numeric,(x.totals->>'partner_price_byn')::numeric,(x.totals->>'partner_w')::numeric,(x.totals->>'partner_points')::numeric,
 jsonb_build_object('items',x.items,'missing_fields',x.missing_fields,'non_catalog_statuses',x.non_catalog_statuses,'prices_as_of',now())
FROM jsonb_to_recordset({calculations_json}::jsonb) AS x(
 bundle_staging_id uuid,state text,totals jsonb,items jsonb,missing_fields jsonb,non_catalog_statuses jsonb)
ON CONFLICT(bundle_staging_id,catalog_snapshot_id) DO UPDATE SET
 calculation_state=EXCLUDED.calculation_state,retail_rub=EXCLUDED.retail_rub,retail_byn=EXCLUDED.retail_byn,
 retail_w=EXCLUDED.retail_w,partner_rub=EXCLUDED.partner_rub,partner_byn=EXCLUDED.partner_byn,
 partner_w=EXCLUDED.partner_w,partner_pv=EXCLUDED.partner_pv,details=EXCLUDED.details,calculated_at=now();
{injected_failure}
SELECT snapshot_id FROM calculation_snapshot;
COMMIT;
"""
    snapshot_id = run_sql(sql, tuples_only=True, timeout=120).splitlines()[-1]
    print(json.dumps({
        "catalog_skus": len(catalog), "bundles": len(bundles), "states": dict(state_counts),
        "catalog_snapshot_id": snapshot_id, "publication": "disabled",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
