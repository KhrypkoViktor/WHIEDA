"""Fast smoke subset: API + partners (no telegram/sync)."""
from __future__ import annotations

import importlib.util
import json
import time
import uuid
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings()

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_API = "https://sysarchn8n.duckdns.org/webhook/wwc-advisor-public-v1"


def main() -> None:
    results = []
    for case_id, payload in [
        ("api_price", {"question": "Сколько стоит активатор?", "session_id": "fast-q1", "ref": "ladnaya"}),
        ("api_greet", {"question": "Привет", "session_id": "fast-q2", "ref": "mariam"}),
    ]:
        r = requests.post(PUBLIC_API, json=payload, verify=False, timeout=90)
        body = r.json() if r.text else {}
        ok = r.status_code == 200 and body.get("ok") is True and bool(body.get("answer_text"))
        results.append({"id": case_id, "pass": ok, "status": r.status_code, "route": body.get("route")})

    helper_path = BASE_DIR / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("h", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    session = helper.login_session()
    suffix = uuid.uuid4().hex[:8]
    path = f"whieda-smoke-ro-{suffix}"
    query = (
        "SELECT ref_code, public_profile->>'public_site_url' AS public_site_url, "
        "(public_profile->>'focus_group')::boolean AS focus_group, "
        "public_profile->>'access_tier' AS access_tier "
        "FROM referral_profiles WHERE tenant_id = 'whieda' "
        "AND ref_code IN ('ladnaya', 'mariam') ORDER BY ref_code"
    )
    wrapped = f"SELECT COALESCE(json_agg(row_to_json(x)), '[]'::json) AS rows FROM ({query}) x"
    workflow = {
        "name": f"TEMP ro {suffix}",
        "active": False,
        "nodes": [
            {"parameters": {"httpMethod": "GET", "path": path, "responseMode": "responseNode", "options": {}}, "id": "w", "name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 0]},
            {"parameters": {"operation": "executeQuery", "query": wrapped, "options": {}}, "id": "p", "name": "Q", "type": "n8n-nodes-base.postgres", "typeVersion": 2.6, "position": [200, 0], "credentials": {"postgres": {"id": "RmjHh3rdZri7axzq", "name": "advisor-dev-postgres"}}},
            {"parameters": {"respondWith": "json", "responseBody": "={{ $json }}", "options": {"responseCode": 200}}, "id": "r", "name": "Respond", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [400, 0]},
        ],
        "connections": {"Webhook": {"main": [[{"node": "Q", "type": "main", "index": 0}]]}, "Q": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]}},
        "settings": {"executionOrder": "v1"},
    }
    workflow_id = None
    partner_ok = False
    try:
        data = session.post(f"{helper.BASE_URL}/rest/workflows", json=workflow, verify=False, timeout=60).json()["data"]
        workflow_id = data["id"]
        version = data["versionId"]
        session.post(f"{helper.BASE_URL}/rest/workflows/{workflow_id}/activate", json={"versionId": version}, verify=False, timeout=60).raise_for_status()
        helper.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={workflow_id}")
        time.sleep(5)
        rows = requests.get(f"{helper.BASE_URL}/webhook/{path}", verify=False, timeout=60).json().get("rows", [])
        partner_ok = all(
            str(r.get("public_site_url", "")).startswith("https://") and r.get("focus_group") is True and r.get("access_tier")
            for r in rows
        )
        results.append({"id": "partners_runtime", "pass": partner_ok, "rows": rows})
    finally:
        if workflow_id:
            session.delete(f"{helper.BASE_URL}/rest/workflows/{workflow_id}", verify=False, timeout=60)

    print(json.dumps(results, ensure_ascii=False, indent=2))
    if not all(item["pass"] for item in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
