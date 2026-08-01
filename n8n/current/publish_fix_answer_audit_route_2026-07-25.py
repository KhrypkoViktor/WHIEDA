"""Restore the answer-audit route so every normal reply persists conversation context."""

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
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    helpers = load_helpers()
    session = helpers.login_session()
    response = session.get(f"{helpers.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
    response.raise_for_status()
    workflow = response.json()["data"]

    backup_dir = BASE_DIR.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"advisor-whieda-before-answer-audit-route-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

    answer_audit = next(node for node in workflow["nodes"] if "Write Audit" in node["name"] and node["name"].endswith("Answer"))
    route = workflow["connections"]["IF: Route Gap True?"]["main"]
    route[1] = [{"node": answer_audit["name"], "type": "main", "index": 0}]

    saved = session.patch(f"{helpers.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=60)
    saved.raise_for_status()
    version_id = saved.json().get("data", saved.json()).get("versionId")
    if not version_id:
        raise RuntimeError("n8n did not return versionId")
    activation = session.post(f"{helpers.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate", json={"versionId": version_id}, verify=False, timeout=60)
    activation.raise_for_status()
    helpers.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    helpers.ssh_run("docker restart n8n-n8n-1")
    helpers.wait_for_n8n_ready()
    print(json.dumps({"workflow_id": WORKFLOW_ID, "version_id": version_id, "backup_path": str(backup_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
