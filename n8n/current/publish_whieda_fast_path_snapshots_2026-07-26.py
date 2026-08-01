"""Bypass admin-only snapshots for ordinary Structure Basic requests."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
WORKFLOW_ID = "advisor-whieda-phase1"
CLASSIFIER = "Code: Need Admin Snapshots?"
ROUTER = "IF: Need Admin Snapshots?"


def helpers():
    path = BASE_DIR / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_sync", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    pub = helpers()
    session = pub.login_session()
    response = session.get(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
    response.raise_for_status()
    workflow = response.json()["data"]
    backup_dir = BASE_DIR.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"advisor-whieda-before-fast-path-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

    nodes = [node for node in workflow["nodes"] if node["name"] not in {CLASSIFIER, ROUTER}]
    resource = next(node for node in nodes if node["name"] == "Postgres: Resource Links")
    broadcast = next(node for node in nodes if node["name"] == "Postgres: Broadcast Snapshot")
    structured = next(node for node in nodes if node["name"] == "Code: Structured Sheet Lookup")
    lookup_source = (BASE_DIR / "structured_sheet_lookup_current.js").read_text(encoding="utf-8")
    structured["parameters"]["jsCode"] = lookup_source

    nodes.extend([
        {
            "parameters": {"jsCode": """const text = String($json.user_message_text ?? $json.message_text ?? '').trim().toLowerCase();
const needAdminSnapshots = /^\\/(broadcast|broadcast_send|broadcast_cancel|meeting|meeting_send|meeting_cancel|gap_report|review)(?:@\\w+)?(?:\\s|$)/.test(text);
return [{ json: { ...$json, need_admin_snapshots: needAdminSnapshots } }];"""},
            "id": "whieda-admin-snapshot-classifier",
            "name": CLASSIFIER,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [resource["position"][0] + 220, resource["position"][1]],
        },
        {
            "parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2}, "combinator": "and", "conditions": [{"id": "admin-snapshot-needed", "operator": {"type": "boolean", "operation": "true", "singleValue": True}, "leftValue": "={{ $json.need_admin_snapshots }}", "rightValue": ""}]}, "options": {}},
            "id": "whieda-admin-snapshot-router",
            "name": ROUTER,
            "type": "n8n-nodes-base.if",
            "typeVersion": 2.2,
            "position": [resource["position"][0] + 440, resource["position"][1]],
        },
    ])
    workflow["nodes"] = nodes
    workflow["connections"][resource["name"]] = {"main": [[{"node": CLASSIFIER, "type": "main", "index": 0}]]}
    workflow["connections"][CLASSIFIER] = {"main": [[{"node": ROUTER, "type": "main", "index": 0}]]}
    workflow["connections"][ROUTER] = {"main": [
        [{"node": broadcast["name"], "type": "main", "index": 0}],
        [{"node": structured["name"], "type": "main", "index": 0}],
    ]}

    saved = session.patch(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=120)
    saved.raise_for_status()
    version = saved.json().get("data", saved.json()).get("versionId")
    if not version:
        raise RuntimeError("n8n did not return versionId")
    activated = session.post(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate", json={"versionId": version}, verify=False, timeout=60)
    activated.raise_for_status()
    pub.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    pub.ssh_run("docker restart n8n-n8n-1")
    pub.wait_for_n8n_ready()
    print(json.dumps({"workflow_id": WORKFLOW_ID, "version_id": version, "backup": str(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
