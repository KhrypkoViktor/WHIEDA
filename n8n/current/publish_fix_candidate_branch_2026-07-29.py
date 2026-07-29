"""Repair candidate routing: only new private users touch the Google Sheet node."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path


BASE = Path(__file__).resolve().parent
WORKFLOW_ID = "advisor-whieda-phase1"


def helpers():
    path = BASE / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_sync", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> None:
    pub = helpers()
    session = pub.login_session()
    response = session.get(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
    response.raise_for_status()
    workflow = response.json()["data"]
    backups = BASE.parent / "backups"; backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"advisor-whieda-before-candidate-route-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

    connections = workflow["connections"]
    register = "Postgres: Register New User Candidate"
    gate = "IF: New User Candidate?"
    build = "Code: Build Users Access Candidate Row"
    lookup = "Postgres: Lookup User Access"
    if not all(name in connections or name == lookup for name in [register, gate, build]):
        raise RuntimeError("candidate routing nodes were not found")
    # Register only decides whether a truly new private user needs Sheet/alert work.
    connections[register] = {"main": [[{"node": gate, "type": "main", "index": 0}]]}
    connections[gate] = {"main": [
        [{"node": build, "type": "main", "index": 0}, {"node": "Telegram: Alert New User Candidate", "type": "main", "index": 0}],
        [{"node": lookup, "type": "main", "index": 0}],
    ]}
    # The row builder remains connected only from the true candidate branch.
    connections[build] = {"main": [[
        {"node": "Google Sheets: Append Users Access Candidate", "type": "main", "index": 0},
        {"node": "Telegram: Pending Access Reply", "type": "main", "index": 0},
    ]]}

    saved = session.patch(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=120)
    saved.raise_for_status()
    version = saved.json().get("data", saved.json()).get("versionId")
    if not version:
        raise RuntimeError("n8n did not return a versionId")
    activated = session.post(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate", json={"versionId": version}, verify=False, timeout=60)
    activated.raise_for_status()
    pub.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    pub.ssh_run("docker restart n8n-n8n-1")
    pub.wait_for_n8n_ready()
    print(json.dumps({"workflow_id": WORKFLOW_ID, "version_id": version, "backup": str(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
