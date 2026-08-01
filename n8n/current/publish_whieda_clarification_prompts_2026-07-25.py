"""Publish the Google Sheet-driven clarification prompts into the live advisor workflow."""

from __future__ import annotations

import copy
import importlib.util
import json
import time
from datetime import datetime
from pathlib import Path

import requests


BASE_DIR = Path(__file__).resolve().parent
WORKFLOW_ID = "advisor-whieda-phase1"
LOOKUP_NODE_ID = "whieda-structured-sheet-lookup"
PROMPTS_NODE_ID = "whieda-clarification-prompts"
PROMPTS_NODE_NAME = "Postgres: Clarification Prompts"


def load_publisher():
    path = BASE_DIR / "publish_whieda_answer_photo_then_text_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_live", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def login(publisher, timeout_seconds: int = 90):
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        session = requests.Session()
        try:
            response = session.post(
                f"{publisher.BASE_URL}/rest/login",
                json={"emailOrLdapLoginId": publisher.EMAIL, "password": publisher.PASSWORD},
                verify=False,
                timeout=15,
            )
            if response.ok:
                return session
            last_error = f"login={response.status_code}"
        except requests.RequestException as error:
            last_error = str(error)
        time.sleep(3)
    raise RuntimeError(f"n8n REST API did not become ready: {last_error}")


def prompts_node(credentials):
    return {
        "parameters": {
            "operation": "executeQuery",
            "query": (
                "SELECT *\n"
                "FROM advisor_structured_clarification_prompts\n"
                "WHERE client_id = 'whieda' AND enabled = TRUE\n"
                "ORDER BY clarification_key ASC;"
            ),
            "options": {},
        },
        "id": PROMPTS_NODE_ID,
        "name": PROMPTS_NODE_NAME,
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.6,
        "position": [-8880, -976],
        "credentials": copy.deepcopy(credentials),
    }


def main() -> None:
    publisher = load_publisher()
    code = (BASE_DIR / "structured_sheet_lookup_current.js").read_text(encoding="utf-8")
    if "const clarificationPrompts = rowsFromNode" not in code:
        raise RuntimeError("Local clarification prompt logic is missing")

    session = login(publisher)
    response = session.get(f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
    response.raise_for_status()
    workflow = response.json()["data"]
    backup = copy.deepcopy(workflow)

    lookup = next((node for node in workflow.get("nodes", []) if node.get("id") == LOOKUP_NODE_ID), None)
    business_faq = next((node for node in workflow.get("nodes", []) if node.get("name") == "Postgres: Business FAQ"), None)
    if lookup is None or business_faq is None:
        raise RuntimeError("Required workflow nodes are missing")

    changed = lookup.get("parameters", {}).get("jsCode") != code
    lookup.setdefault("parameters", {})["jsCode"] = code

    existing = next((node for node in workflow.get("nodes", []) if node.get("id") == PROMPTS_NODE_ID), None)
    if existing is None:
        workflow["nodes"].append(prompts_node(business_faq.get("credentials", {})))
        changed = True
    else:
        wanted = prompts_node(business_faq.get("credentials", {}))
        if existing != wanted:
            index = workflow["nodes"].index(existing)
            workflow["nodes"][index] = wanted
            changed = True

    connections = workflow.setdefault("connections", {})
    expected_business = {"main": [[{"node": PROMPTS_NODE_NAME, "type": "main", "index": 0}]]}
    expected_prompts = {"main": [[{"node": "Postgres: Resource Links", "type": "main", "index": 0}]]}
    if connections.get("Postgres: Business FAQ") != expected_business:
        connections["Postgres: Business FAQ"] = expected_business
        changed = True
    if connections.get(PROMPTS_NODE_NAME) != expected_prompts:
        connections[PROMPTS_NODE_NAME] = expected_prompts
        changed = True

    if not changed:
        print(json.dumps({"updated": False, "reason": "already_current"}, ensure_ascii=False))
        return

    backup_dir = BASE_DIR.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"advisor-whieda-phase1-before-clarification-prompts-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")

    saved = session.patch(
        f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=120
    )
    saved.raise_for_status()
    version_id = saved.json().get("data", saved.json()).get("versionId")
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

    session = login(publisher)
    check = session.get(f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
    check.raise_for_status()
    live = check.json()["data"]
    live_lookup = next(item for item in live.get("nodes", []) if item.get("id") == LOOKUP_NODE_ID)
    if live_lookup.get("parameters", {}).get("jsCode") != code:
        raise RuntimeError("Published workflow does not contain the clarification logic")
    if not any(item.get("id") == PROMPTS_NODE_ID for item in live.get("nodes", [])):
        raise RuntimeError("Published workflow does not contain the clarification prompts query")

    print(json.dumps({
        "updated": True,
        "backup_path": str(backup_path),
        "version_id": version_id,
        "active": live.get("active"),
        "active_version_id": live.get("activeVersionId"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
