#!/usr/bin/env python3
"""Release 3.1: bind bundle items to real SKUs and snapshot price calculations."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path

import requests

SHEET_ID = "1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4"
TENANT_ID = "whieda"
PRODUCT_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=tsv&gid=1035748906"
ALIAS_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=tsv&gid=2001001"
PRICE_FIELDS = [
    "retail_price_rub", "retail_price_byn", "retail_w",
    "partner_price_rub", "partner_price_byn", "partner_w", "partner_points",
]


def normalize(value: str | None) -> str:
    return " ".join((value or "").lower().replace("ё", "е").replace('"', "").split())


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
        process_env = os.environ.copy()
        process_env["PGCLIENTENCODING"] = "UTF8"
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", env=process_env,
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
    response = requests.get(url, timeout=45)
    response.raise_for_status()
    response.encoding = "utf-8"
    return [
        {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}
        for row in csv.DictReader(io.StringIO(response.text), delimiter="\t")
        if any(str(value or "").strip() for value in row.values())
    ]


def read_tsv_file(path: str) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return [
            {str(key or "").strip(): str(value or "").strip() for key, value in row.items()}
            for row in csv.DictReader(handle, delimiter="\t")
            if any(str(value or "").strip() for value in row.values())
        ]


def current_bundles() -> list[dict[str, object]]:
    query = """SELECT coalesce(jsonb_agg(jsonb_build_object(
      'bundle_staging_id',bundle_staging_id,'primary_product',primary_product,
      'additional_products',additional_products,'medical',medical_review_required,
      'business',business_review_required,'owner',owner_approved) ORDER BY external_record_id),'[]'::jsonb)
    FROM advisor_bundle_staging_records WHERE tenant_id='whieda' AND version_state='current';"""
    return json.loads(run_sql(query, tuples_only=True, timeout=45))


def count_product_names(primary: object, additional: object) -> dict[str, int]:
    result: defaultdict[str, int] = defaultdict(int)
    for value in (str(primary or "") + "," + str(additional or "")).split(","):
        if value.strip():
            result[value.strip()] += 1
    return dict(result)


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
    source_hash = hashlib.sha256(
        json.dumps([products, aliases], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    catalog: dict[str, dict[str, object]] = {}
    names: defaultdict[str, set[str]] = defaultdict(set)
    for row in products:
        sku = row.get("sku", "")
        if not sku:
            continue
        active = row.get("active", "true").lower() not in {"false", "0", "нет", "inactive"}
        catalog[sku] = {
            "sku": sku, "canonical_name": row.get("canonical_name", ""), "active": active,
            **{field: number(row.get(field)) for field in PRICE_FIELDS},
        }
        names[normalize(row.get("canonical_name"))].add(sku)
    for row in aliases:
        sku = row.get("canonical_sku", "")
        if sku in catalog and row.get("active", "true").lower() not in {"false", "0", "нет"}:
            names[normalize(row.get("alias"))].add(sku)

    bundles = current_bundles()
    catalog_payload = list(catalog.values())
    item_payload: list[dict[str, object]] = []
    calculation_payload: list[dict[str, object]] = []
    state_counts: defaultdict[str, int] = defaultdict(int)
    for bundle in bundles:
        quantities = count_product_names(bundle.get("primary_product"), bundle.get("additional_products"))
        resolved: list[dict[str, object]] = []
        calculation_state = "awaiting_approval"
        totals = [0.0] * 7
        missing_price = False
        for source_name, quantity in quantities.items():
            candidates = sorted(names.get(normalize(source_name), set()))
            if not candidates:
                match_state, sku = "unresolved", None
                calculation_state = "requires_binding"
            elif len(candidates) > 1:
                match_state, sku = "ambiguous", None
                calculation_state = "requires_binding"
            else:
                sku = candidates[0]
                match_state = "matched" if catalog[sku]["active"] else "inactive"
                if match_state == "inactive":
                    calculation_state = "requires_binding"
            item_payload.append({
                "bundle_staging_id": bundle["bundle_staging_id"], "source_name": source_name,
                "sku": sku, "quantity": quantity, "match_state": match_state,
                "candidates": candidates,
            })
            if sku and match_state == "matched":
                values = [catalog[sku][field] for field in PRICE_FIELDS]
                if any(value is None for value in values):
                    missing_price = True
                else:
                    totals = [total + float(value) * quantity for total, value in zip(totals, values)]
                resolved.append({"sku": sku, "quantity": quantity})
        if calculation_state != "requires_binding" and missing_price:
            calculation_state = "missing_price"
        if (
            calculation_state == "awaiting_approval"
            and not bundle["medical"] and not bundle["business"] and bundle["owner"]
        ):
            calculation_state = "fully_calculated"
        state_counts[calculation_state] += 1
        calculation_payload.append({
            "bundle_staging_id": bundle["bundle_staging_id"], "state": calculation_state,
            "totals": totals, "items": resolved,
        })

    snapshot_sql = f"""INSERT INTO advisor_bundle_catalog_snapshots(tenant_id,source_url,source_hash)
    VALUES({sql_literal(TENANT_ID)},{sql_literal(PRODUCT_URL)},{sql_literal(source_hash)})
    ON CONFLICT(tenant_id,source_hash) DO NOTHING;
    SELECT snapshot_id FROM advisor_bundle_catalog_snapshots
    WHERE tenant_id={sql_literal(TENANT_ID)} AND source_hash={sql_literal(source_hash)};"""
    snapshot_id = run_sql(snapshot_sql, tuples_only=True, timeout=45).splitlines()[-1]
    catalog_json = sql_literal(json.dumps(catalog_payload, ensure_ascii=False))
    items_json = sql_literal(json.dumps(item_payload, ensure_ascii=False))
    calculations_json = sql_literal(json.dumps(calculation_payload, ensure_ascii=False))
    bundle_ids = sql_literal(json.dumps([row["bundle_staging_id"] for row in bundles]))
    injected_failure = "DO $$ BEGIN RAISE EXCEPTION 'injected failure before calculation commit'; END $$;" if args.fail_before_commit else ""
    sql = f"""
SET lock_timeout='10s'; SET statement_timeout='90s'; BEGIN;
SELECT pg_advisory_xact_lock(hashtext('whieda:bundle-calculation-staging'));
INSERT INTO advisor_bundle_catalog_snapshot_items(
 snapshot_id,sku,canonical_name,retail_price_rub,retail_price_byn,retail_w,
 partner_price_rub,partner_price_byn,partner_w,partner_pv,active)
SELECT {sql_literal(snapshot_id)}::uuid,x.sku,x.canonical_name,x.retail_price_rub,x.retail_price_byn,x.retail_w,
 x.partner_price_rub,x.partner_price_byn,x.partner_w,x.partner_points,x.active
FROM jsonb_to_recordset({catalog_json}::jsonb) AS x(
 sku text,canonical_name text,retail_price_rub numeric,retail_price_byn numeric,retail_w numeric,
 partner_price_rub numeric,partner_price_byn numeric,partner_w numeric,partner_points numeric,active boolean)
ON CONFLICT(snapshot_id,sku) DO UPDATE SET canonical_name=EXCLUDED.canonical_name,
 retail_price_rub=EXCLUDED.retail_price_rub,retail_price_byn=EXCLUDED.retail_price_byn,retail_w=EXCLUDED.retail_w,
 partner_price_rub=EXCLUDED.partner_price_rub,partner_price_byn=EXCLUDED.partner_price_byn,
 partner_w=EXCLUDED.partner_w,partner_pv=EXCLUDED.partner_pv,active=EXCLUDED.active;
DELETE FROM advisor_bundle_item_staging
WHERE bundle_staging_id IN (SELECT value::text::uuid FROM jsonb_array_elements_text({bundle_ids}::jsonb));
INSERT INTO advisor_bundle_item_staging(bundle_staging_id,source_name,sku,quantity,match_state,match_evidence)
SELECT x.bundle_staging_id,x.source_name,x.sku,x.quantity,x.match_state,jsonb_build_object('candidates',x.candidates)
FROM jsonb_to_recordset({items_json}::jsonb) AS x(
 bundle_staging_id uuid,source_name text,sku text,quantity integer,match_state text,candidates jsonb);
INSERT INTO advisor_bundle_calculation_snapshots(
 bundle_staging_id,catalog_snapshot_id,calculation_state,retail_rub,retail_byn,retail_w,
 partner_rub,partner_byn,partner_w,partner_pv,details)
SELECT x.bundle_staging_id,{sql_literal(snapshot_id)}::uuid,x.state,
 (x.totals->>0)::numeric,(x.totals->>1)::numeric,(x.totals->>2)::numeric,
 (x.totals->>3)::numeric,(x.totals->>4)::numeric,(x.totals->>5)::numeric,(x.totals->>6)::numeric,
 jsonb_build_object('items',x.items,'prices_as_of',now())
FROM jsonb_to_recordset({calculations_json}::jsonb) AS x(
 bundle_staging_id uuid,state text,totals jsonb,items jsonb)
ON CONFLICT(bundle_staging_id,catalog_snapshot_id) DO UPDATE SET
 calculation_state=EXCLUDED.calculation_state,retail_rub=EXCLUDED.retail_rub,retail_byn=EXCLUDED.retail_byn,
 retail_w=EXCLUDED.retail_w,partner_rub=EXCLUDED.partner_rub,partner_byn=EXCLUDED.partner_byn,
 partner_w=EXCLUDED.partner_w,partner_pv=EXCLUDED.partner_pv,details=EXCLUDED.details,calculated_at=now();
{injected_failure}
COMMIT;
"""
    run_sql(sql, timeout=120)
    print(json.dumps({
        "catalog_skus": len(catalog), "bundles": len(bundles), "states": dict(state_counts),
        "catalog_snapshot_id": snapshot_id, "publication": "disabled",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
