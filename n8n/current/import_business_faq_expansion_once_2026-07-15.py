"""One-time append of approved Business FAQ rows through the existing n8n Sheets credential."""
import csv
import importlib.util
import json
import time
from pathlib import Path

import requests


BASE = Path(__file__).resolve().parent
CSV_PATH = BASE.parent.parent / "RAG" / "WHIEDA_BUSINESS_FAQ_EXPANSION_V1_2026-07-15.csv"
WORKFLOW_NAME = "WHIEDA One-time Business FAQ Import"
WEBHOOK_PATH = "whieda-import-business-faq-v1"


def load_publisher():
    path = BASE / "publish_whieda_answer_photo_then_text_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_publisher", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_workflow(rows):
    code = "return " + json.dumps([{"json": row} for row in rows], ensure_ascii=False) + ";"
    credential = {"googleSheetsOAuth2Api": {"id": "XnF6UcslXgxuXbGm", "name": "Google Sheets account"}}
    return {
        "name": WORKFLOW_NAME,
        "active": False,
        "nodes": [
            {"parameters": {"httpMethod": "POST", "path": WEBHOOK_PATH, "responseMode": "responseNode", "options": {}}, "id": "trigger", "name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [0, 0]},
            {"parameters": {"jsCode": code}, "id": "rows", "name": "Approved FAQ Rows", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [220, 0]},
            {"parameters": {"operation": "append", "documentId": {"__rl": True, "value": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/edit", "mode": "url"}, "sheetName": {"__rl": True, "value": "Business_FAQ", "mode": "name"}, "columns": {"mappingMode": "autoMapInputData", "value": {}, "schema": []}, "options": {}}, "id": "append", "name": "Append Business FAQ", "type": "n8n-nodes-base.googleSheets", "typeVersion": 4.7, "position": [440, 0], "credentials": credential},
            {"parameters": {"respondWith": "json", "responseBody": "={{ { ok: true, imported: 7 } }}", "options": {}}, "id": "respond", "name": "Respond", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [660, 0]},
        ],
        "connections": {"Webhook": {"main": [[{"node": "Approved FAQ Rows", "type": "main", "index": 0}]]}, "Approved FAQ Rows": {"main": [[{"node": "Append Business FAQ", "type": "main", "index": 0}]]}, "Append Business FAQ": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]}},
        "settings": {"executionOrder": "v1"},
    }


def main():
    pub = load_publisher()
    rows = list(csv.DictReader(CSV_PATH.open(encoding="utf-8")))
    session = requests.Session()
    session.post(f"{pub.BASE_URL}/rest/login", json={"emailOrLdapLoginId": pub.EMAIL, "password": pub.PASSWORD}, verify=False, timeout=30).raise_for_status()
    items = session.get(f"{pub.BASE_URL}/rest/workflows?limit=200", verify=False, timeout=30).json()["data"]
    current = next((item for item in items if item["name"] == WORKFLOW_NAME), None)
    workflow = build_workflow(rows)
    if current:
        workflow["id"] = current["id"]
        session.patch(f"{pub.BASE_URL}/rest/workflows/{current['id']}", json=workflow, verify=False, timeout=60).raise_for_status()
        workflow_id = current["id"]
    else:
        created = session.post(f"{pub.BASE_URL}/rest/workflows", json=workflow, verify=False, timeout=60)
        created.raise_for_status()
        created_data = created.json()
        workflow_id = created_data.get("id") or created_data.get("data", {}).get("id")
        if not workflow_id:
            raise RuntimeError("n8n did not return a workflow id: " + json.dumps(created_data, ensure_ascii=False))
    session.post(f"{pub.BASE_URL}/rest/workflows/{workflow_id}/activate", json={}, verify=False, timeout=30)
    pub.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={workflow_id}")
    pub.ssh_run("docker restart n8n-n8n-1")
    pub.wait_for_n8n_ready()
    time.sleep(8)
    response = requests.post(f"{pub.BASE_URL}/webhook/{WEBHOOK_PATH}", json={}, verify=False, timeout=90)
    response.raise_for_status()
    print(json.dumps({"workflow_id": workflow_id, "rows": len(rows), "response": response.json()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
