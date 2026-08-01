"""Allow new candidates to use the advisor immediately; only blocked users are denied."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
WORKFLOW_ID = "advisor-whieda-phase1"


def load_helpers():
    path = BASE_DIR / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_sync", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    pub = load_helpers()
    session = pub.login_session()
    response = session.get(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
    response.raise_for_status()
    workflow = response.json()["data"]

    backup_dir = BASE_DIR.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"advisor-whieda-before-open-candidate-access-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

    gate = next(node for node in workflow["nodes"] if node["name"] == "IF: User Access Approved?")
    condition = gate["parameters"]["conditions"]["conditions"][0]
    condition["operator"] = {"type": "string", "operation": "notEquals"}
    condition["leftValue"] = "={{ String($json.access_status || 'candidate').toLowerCase() }}"
    condition["rightValue"] = "blocked"

    connections = workflow["connections"]
    candidate_true = connections["IF: New User Candidate?"]["main"][0]
    if not any(item.get("node") == "Code: Restore User Session After Alert" for item in candidate_true):
        candidate_true.append({"node": "Code: Restore User Session After Alert", "type": "main", "index": 0})

    pending = next(node for node in workflow["nodes"] if node["name"] == "Telegram: Pending Access Reply")
    pending["parameters"]["jsonBody"] = "={{ ({ chat_id: $('Code: Normalize Payload').first().json.external_chat_id, text: 'Доступ к советнику сейчас отключён. Если это ошибка, обратитесь к своему лидеру или администратору.', disable_web_page_preview: true }) }}"

    saved = session.patch(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=120)
    saved.raise_for_status()
    version = saved.json().get("data", saved.json()).get("versionId")
    if not version:
        raise RuntimeError("n8n did not return versionId")
    session.post(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate", json={"versionId": version}, verify=False, timeout=60).raise_for_status()
    pub.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    pub.ssh_run("docker restart n8n-n8n-1")
    pub.wait_for_n8n_ready()
    print(json.dumps({"workflow_id": WORKFLOW_ID, "version_id": version, "backup": str(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
