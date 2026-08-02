"""Compare RAG alias corpus against live advisor_structured_aliases."""

from __future__ import annotations

import csv
import importlib.util
import json
import re
import time
import uuid
from datetime import date
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
RAG_ALIASES = (
    BASE_DIR.parents[1]
    / "RAG"
    / "1 компиляция. диалоги с врачами"
    / "2_SQL_корпус_из_RAW"
    / "02_ALIASES.tsv"
)
EXPORT_DIR = BASE_DIR.parent / "live-exports" / date.today().isoformat()
POSTGRES_CREDENTIAL = {"postgres": {"id": "RmjHh3rdZri7axzq", "name": "advisor-dev-postgres"}}


def normalize(value: str) -> str:
    value = (value or "").casefold().strip()
    value = re.sub(r"[^\wа-яё$]+", " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def split_alias_variants(raw: str) -> list[str]:
    parts = re.split(r"\s*/\s*|;", raw or "")
    return [part.strip() for part in parts if part.strip()]


def load_corpus_aliases() -> list[dict]:
    rows = []
    with RAG_ALIASES.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            canonical = (row.get("нормализованное_значение") or row.get("товар_или_тема") or "").strip()
            for variant in split_alias_variants(row.get("алиас") or ""):
                rows.append(
                    {
                        "record_id": row.get("record_id"),
                        "alias": variant,
                        "normalized_alias": normalize(variant),
                        "canonical_target": canonical,
                        "entity_type": row.get("тип_сущности"),
                        "confidence": row.get("уверенность"),
                        "publication_status": row.get("publication_status"),
                    }
                )
    return rows


def fetch_live_aliases() -> list[dict]:
    helper_path = BASE_DIR / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("h", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    session = helper.login_session()
    suffix = uuid.uuid4().hex[:8]
    path = f"whieda-alias-gap-{suffix}"
    query = """
SELECT alias, canonical_sku, canonical_name, match_type, priority, active::text AS active
FROM advisor_structured_aliases
WHERE client_id = 'whieda'
ORDER BY alias
"""
    wrapped = f"SELECT COALESCE(json_agg(row_to_json(x)), '[]'::json) AS rows FROM ({query}) x"
    workflow = {
        "name": f"TEMP alias gap {suffix}",
        "active": False,
        "nodes": [
            {"parameters": {"httpMethod": "GET", "path": path, "responseMode": "responseNode", "options": {}}, "id": "w", "name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 0]},
            {"parameters": {"operation": "executeQuery", "query": wrapped, "options": {}}, "id": "q", "name": "Q", "type": "n8n-nodes-base.postgres", "typeVersion": 2.6, "position": [200, 0], "credentials": POSTGRES_CREDENTIAL},
            {"parameters": {"respondWith": "json", "responseBody": "={{ $json }}", "options": {"responseCode": 200}}, "id": "r", "name": "Respond", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [400, 0]},
        ],
        "connections": {"Webhook": {"main": [[{"node": "Q", "type": "main", "index": 0}]]}, "Q": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]}},
        "settings": {"executionOrder": "v1"},
    }
    workflow_id = None
    try:
        data = session.post(f"{helper.BASE_URL}/rest/workflows", json=workflow, verify=False, timeout=60).json()["data"]
        workflow_id = data["id"]
        session.post(
            f"{helper.BASE_URL}/rest/workflows/{workflow_id}/activate",
            json={"versionId": data["versionId"]},
            verify=False,
            timeout=60,
        ).raise_for_status()
        helper.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={workflow_id}")
        time.sleep(5)
        payload = requests.get(f"{helper.BASE_URL}/webhook/{path}", verify=False, timeout=60).json()
        return payload.get("rows", payload) if isinstance(payload, dict) else payload
    finally:
        if workflow_id:
            session.delete(f"{helper.BASE_URL}/rest/workflows/{workflow_id}", verify=False, timeout=60)


def main() -> None:
    corpus = load_corpus_aliases()
    live = fetch_live_aliases()
    live_norm = {normalize(row.get("alias") or "") for row in live if row.get("alias")}
    live_norm.discard("")

    missing = []
    for row in corpus:
        norm = row["normalized_alias"]
        if not norm or norm in live_norm:
            continue
        missing.append(row)

    by_confidence = {}
    for row in missing:
        key = row.get("confidence") or "unknown"
        by_confidence[key] = by_confidence.get(key, 0) + 1

    report = {
        "date": date.today().isoformat(),
        "corpus_alias_variants": len(corpus),
        "live_aliases": len(live),
        "missing_in_live": len(missing),
        "by_confidence": by_confidence,
        "sample_missing": missing[:40],
        "publication": "candidate_only",
    }
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = EXPORT_DIR / "WHIEDA_alias_gap_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("corpus_alias_variants", "live_aliases", "missing_in_live", "by_confidence", "publication")}, ensure_ascii=False))
    print(f"report={out}")


if __name__ == "__main__":
    main()
