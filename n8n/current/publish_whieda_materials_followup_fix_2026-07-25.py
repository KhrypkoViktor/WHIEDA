"""Publish only the SQL materials follow-up fix after a fresh live backup."""

from __future__ import annotations

import copy
import importlib.util
import json
from datetime import datetime
from pathlib import Path

import requests


BASE_DIR = Path(__file__).resolve().parent
WORKFLOW_ID = "advisor-whieda-phase1"
NODE_ID = "whieda-structured-sheet-lookup"


def load_publisher():
    path = BASE_DIR / "publish_whieda_answer_photo_then_text_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_live", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    publisher = load_publisher()
    code = (BASE_DIR / "structured_sheet_lookup_current.js").read_text(encoding="utf-8")
    session = requests.Session()
    session.post(
        f"{publisher.BASE_URL}/rest/login",
        json={"emailOrLdapLoginId": publisher.EMAIL, "password": publisher.PASSWORD},
        verify=False,
        timeout=30,
    ).raise_for_status()
    response = session.get(f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
    response.raise_for_status()
    workflow = response.json()["data"]
    backup = copy.deepcopy(workflow)
    node = next((item for item in workflow.get("nodes", []) if item.get("id") == NODE_ID), None)
    if node is None:
        raise RuntimeError(f"Node not found: {NODE_ID}")
    old_code = str(node.get("parameters", {}).get("jsCode", ""))
    if "const materialResources = getProductResources" not in code:
        raise RuntimeError("Local materials fix is missing")
    if old_code == code:
        publisher.ssh_run("docker restart n8n-n8n-1")
        publisher.wait_for_n8n_ready()
        print(json.dumps({"updated": False, "reason": "already_current"}, ensure_ascii=False))
        return

    backup_dir = BASE_DIR.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"advisor-whieda-phase1-before-materials-followup-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")

    node.setdefault("parameters", {})["jsCode"] = code
    response = session.patch(
        f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=120
    )
    response.raise_for_status()
    updated = response.json().get("data", response.json())
    version_id = updated.get("versionId")
    if not version_id:
        refreshed = session.get(f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
        refreshed.raise_for_status()
        version_id = refreshed.json()["data"].get("versionId")
    if not version_id:
        raise RuntimeError("n8n did not return a workflow versionId")
    activation = session.post(
        f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate",
        json={"versionId": version_id}, verify=False, timeout=60,
    )
    activation.raise_for_status()
    publisher.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    publisher.ssh_run("docker restart n8n-n8n-1")
    publisher.wait_for_n8n_ready()
    session = requests.Session()
    session.post(
        f"{publisher.BASE_URL}/rest/login",
        json={"emailOrLdapLoginId": publisher.EMAIL, "password": publisher.PASSWORD},
        verify=False,
        timeout=30,
    ).raise_for_status()

    check = session.get(f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
    check.raise_for_status()
    live = check.json()["data"]
    live_node = next(item for item in live.get("nodes", []) if item.get("id") == NODE_ID)
    if live_node.get("parameters", {}).get("jsCode") != code:
        raise RuntimeError("Published workflow does not contain the requested code")
    print(json.dumps({
        "updated": True,
        "backup_path": str(backup_path),
        "version_id": version_id,
        "active": live.get("active"),
        "active_version_id": live.get("activeVersionId"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
