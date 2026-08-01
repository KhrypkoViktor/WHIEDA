"""Publish persistent first-week coach progress using the existing conversation context."""

from __future__ import annotations

import copy
import importlib.util
import json
from datetime import datetime
from pathlib import Path

import requests


BASE_DIR = Path(__file__).resolve().parent
WORKFLOW_ID = "advisor-whieda-phase1"
LOOKUP_NODE_ID = "whieda-structured-sheet-lookup"
AUDIT_NODE_ID = "6cd7b608-4fb1-4dfb-bfaf-08b6b25280c6"


def load_publisher():
    path = BASE_DIR / "publish_whieda_answer_photo_then_text_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_live", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def update_context_query(query: str) -> str:
    old_context = """      last_product_alias: $json.structured_match?.alias
    }).replace(/'/g, '') }}'::jsonb AS context"""
    new_context = """      last_product_alias: $json.structured_match?.alias,
      coach_first_week: $json.context_updates?.coach_first_week
    }).replace(/'/g, '') }}'::jsonb AS context"""
    old_gate = """WHERE conversation_id IS NOT NULL
  AND context ? 'last_product_sku'"""
    new_gate = """WHERE conversation_id IS NOT NULL
  AND (context ? 'last_product_sku' OR context ? 'coach_first_week')"""
    if new_context in query and new_gate in query:
        return query
    if old_context not in query or old_gate not in query:
        raise RuntimeError("Answer audit context query no longer matches the reviewed shape")
    return query.replace(old_context, new_context).replace(old_gate, new_gate)


def main():
    publisher = load_publisher()
    lookup_code = (BASE_DIR / "structured_sheet_lookup_current.js").read_text(encoding="utf-8")
    if "first_week_continue" not in lookup_code or "coach_first_week" not in lookup_code:
        raise RuntimeError("Local coach progress implementation is incomplete")

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
    lookup_node = next((node for node in workflow.get("nodes", []) if node.get("id") == LOOKUP_NODE_ID), None)
    audit_node = next((node for node in workflow.get("nodes", []) if node.get("id") == AUDIT_NODE_ID), None)
    if lookup_node is None or audit_node is None:
        raise RuntimeError("Required live nodes were not found")

    audit_query = str(audit_node.get("parameters", {}).get("query", ""))
    updated_query = update_context_query(audit_query)
    if lookup_node.get("parameters", {}).get("jsCode") == lookup_code and audit_query == updated_query:
        print(json.dumps({"updated": False, "reason": "already_current"}, ensure_ascii=False))
        return

    backup_dir = BASE_DIR.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"advisor-whieda-phase1-before-coach-progress-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup_path.write_text(json.dumps(copy.deepcopy(workflow), ensure_ascii=False, indent=2), encoding="utf-8")
    lookup_node.setdefault("parameters", {})["jsCode"] = lookup_code
    audit_node.setdefault("parameters", {})["query"] = updated_query
    saved = session.patch(f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=120)
    saved.raise_for_status()
    version_id = saved.json().get("data", saved.json()).get("versionId")
    if not version_id:
        raise RuntimeError("n8n did not return a workflow version")
    session.post(f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate", json={"versionId": version_id}, verify=False, timeout=60).raise_for_status()
    publisher.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    publisher.ssh_run("docker restart n8n-n8n-1")
    publisher.wait_for_n8n_ready()
    print(json.dumps({"updated": True, "version_id": version_id, "backup_path": str(backup_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
