"""Repair candidate persistence: every candidate is upserted into Users_Access; alert stays one-time."""
from __future__ import annotations
import importlib.util, json
from datetime import datetime
from pathlib import Path

BASE = Path(r"D:\Projects\WHIEDA\n8n\current")
WORKFLOW_ID = "advisor-whieda-phase1"

spec = importlib.util.spec_from_file_location("sync", BASE / "publish_and_run_whieda_sync_2026-07-13.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
session = sync.login_session()
response = session.get(f"{sync.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
response.raise_for_status()
workflow = response.json()["data"]

backups = BASE.parent / "backups"
backups.mkdir(exist_ok=True)
backup = backups / f"advisor-whieda-before-users-access-upsert-{datetime.now():%Y%m%d-%H%M%S}.json"
backup.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

nodes = {node["name"]: node for node in workflow["nodes"]}
sheets = nodes["Google Sheets: Append Users Access Candidate"]
sheets["parameters"]["operation"] = "appendOrUpdate"
sheets["parameters"]["columns"] = {
    "mappingMode": "autoMapInputData",
    "value": {},
    "matchingColumns": ["telegram_user_id"],
    "schema": [],
}

# Candidate persistence must not depend on whether the one-time admin alert was sent.
connections = workflow["connections"]
connections["Postgres: Register New User Candidate"] = {"main": [[
    {"node": "Code: Build Users Access Candidate Row", "type": "main", "index": 0},
    {"node": "IF: New User Candidate?", "type": "main", "index": 0},
]]}
connections["IF: New User Candidate?"] = {"main": [[
    {"node": "Telegram: Alert New User Candidate", "type": "main", "index": 0},
], []]}
connections["Code: Build Users Access Candidate Row"] = {"main": [[
    {"node": "Google Sheets: Append Users Access Candidate", "type": "main", "index": 0},
]]}
connections["Google Sheets: Append Users Access Candidate"] = {"main": [[
    {"node": "Postgres: Lookup User Access", "type": "main", "index": 0},
]]}
connections["Telegram: Alert New User Candidate"] = {"main": [[]]}

saved = session.patch(f"{sync.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=120)
saved.raise_for_status()
version = saved.json().get("data", saved.json()).get("versionId")
if not version:
    raise RuntimeError("n8n did not return versionId")
activated = session.post(f"{sync.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate", json={"versionId": version}, verify=False, timeout=60)
activated.raise_for_status()
sync.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
sync.ssh_run("docker restart n8n-n8n-1")
sync.wait_for_n8n_ready()
print(json.dumps({"workflow_id": WORKFLOW_ID, "version_id": version, "backup": str(backup)}, ensure_ascii=False))
