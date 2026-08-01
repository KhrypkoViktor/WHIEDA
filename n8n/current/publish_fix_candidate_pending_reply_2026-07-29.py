"""Candidate gets the pending reply directly; Sheets is a side effect."""
from __future__ import annotations
import importlib.util, json
from datetime import datetime
from pathlib import Path

base = Path(r"D:\Projects\WHIEDA\n8n\current")
spec = importlib.util.spec_from_file_location("sync", base / "publish_and_run_whieda_sync_2026-07-13.py")
sync = importlib.util.module_from_spec(spec); spec.loader.exec_module(sync)
session = sync.login_session(); workflow_id = "advisor-whieda-phase1"
workflow = session.get(f"{sync.BASE_URL}/rest/workflows/{workflow_id}", verify=False, timeout=60).json()["data"]
backup = base.parent / "backups" / f"advisor-whieda-before-candidate-direct-reply-{datetime.now():%Y%m%d-%H%M%S}.json"
backup.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
workflow["connections"]["Code: Build Users Access Candidate Row"] = {"main": [[
    {"node": "Google Sheets: Append Users Access Candidate", "type": "main", "index": 0},
    {"node": "Telegram: Pending Access Reply", "type": "main", "index": 0},
]]}
workflow["connections"]["Google Sheets: Append Users Access Candidate"] = {"main": [[]]}
saved = session.patch(f"{sync.BASE_URL}/rest/workflows/{workflow_id}", json=workflow, verify=False, timeout=120); saved.raise_for_status()
version = saved.json().get("data", saved.json()).get("versionId")
session.post(f"{sync.BASE_URL}/rest/workflows/{workflow_id}/activate", json={"versionId": version}, verify=False, timeout=60).raise_for_status()
sync.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={workflow_id}"); sync.ssh_run("docker restart n8n-n8n-1"); sync.wait_for_n8n_ready()
print(json.dumps({"version_id": version, "backup": str(backup)}, ensure_ascii=False))
