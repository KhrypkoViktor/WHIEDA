"""Publish Google Sheet-driven service responses into the live WHIEDA advisor."""

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
PREVIOUS_NODE_NAME = "Postgres: Clarification Prompts"
NODE_ID = "whieda-capability-responses"
NODE_NAME = "Postgres: Capability Responses"
NEXT_NODE_NAME = "Postgres: Resource Links"


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


def capability_node(credentials):
    return {
        "parameters": {
            "operation": "executeQuery",
            "query": (
                "SELECT *\n"
                "FROM advisor_structured_capability_responses\n"
                "WHERE client_id = 'whieda' AND enabled = TRUE\n"
                "ORDER BY response_id ASC;"
            ),
            "options": {},
        },
        "id": NODE_ID,
        "name": NODE_NAME,
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.6,
        "position": [-8744, -976],
        "credentials": copy.deepcopy(credentials),
    }


def main() -> None:
    publisher = load_publisher()
    code = (BASE_DIR / "structured_sheet_lookup_current.js").read_text(encoding="utf-8")
    if "const capabilityResponses = rowsFromNode" not in code:
        raise RuntimeError("Local capability response logic is missing")

    session = login(publisher)
    response = session.get(f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
    response.raise_for_status()
    workflow = response.json()["data"]
    backup = copy.deepcopy(workflow)

    lookup = next((node for node in workflow.get("nodes", []) if node.get("id") == LOOKUP_NODE_ID), None)
    previous = next((node for node in workflow.get("nodes", []) if node.get("name") == PREVIOUS_NODE_NAME), None)
    if lookup is None or previous is None:
        raise RuntimeError("Required workflow nodes are missing")

    changed = lookup.get("parameters", {}).get("jsCode") != code
    lookup.setdefault("parameters", {})["jsCode"] = code
    wanted_node = capability_node(previous.get("credentials", {}))
    existing = next((node for node in workflow.get("nodes", []) if node.get("id") == NODE_ID), None)
    if existing is None:
        workflow["nodes"].append(wanted_node)
        changed = True
    elif existing != wanted_node:
        workflow["nodes"][workflow["nodes"].index(existing)] = wanted_node
        changed = True

    connections = workflow.setdefault("connections", {})
    expected_previous = {"main": [[{"node": NODE_NAME, "type": "main", "index": 0}]]}
    expected_current = {"main": [[{"node": NEXT_NODE_NAME, "type": "main", "index": 0}]]}
    if connections.get(PREVIOUS_NODE_NAME) != expected_previous:
        connections[PREVIOUS_NODE_NAME] = expected_previous
        changed = True
    if connections.get(NODE_NAME) != expected_current:
        connections[NODE_NAME] = expected_current
        changed = True

    if not changed:
        print(json.dumps({"updated": False, "reason": "already_current"}, ensure_ascii=False))
        return

    backup_dir = BASE_DIR.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"advisor-whieda-phase1-before-capability-responses-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")

    saved = session.patch(f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=120)
    saved.raise_for_status()
    version_id = saved.json().get("data", saved.json()).get("versionId")
    if not version_id:
        refreshed = session.get(f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
        refreshed.raise_for_status()
        version_id = refreshed.json()["data"].get("versionId")
    if not version_id:
        raise RuntimeError("n8n did not return a workflow versionId")

    session.post(
        f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate",
        json={"versionId": version_id}, verify=False, timeout=60,
    ).raise_for_status()
    publisher.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    publisher.ssh_run("docker restart n8n-n8n-1")
    publisher.wait_for_n8n_ready()

    session = login(publisher)
    check = session.get(f"{publisher.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
    check.raise_for_status()
    live = check.json()["data"]
    live_lookup = next(item for item in live.get("nodes", []) if item.get("id") == LOOKUP_NODE_ID)
    if live_lookup.get("parameters", {}).get("jsCode") != code:
        raise RuntimeError("Published workflow does not contain the capability response logic")
    if not any(item.get("id") == NODE_ID for item in live.get("nodes", [])):
        raise RuntimeError("Published workflow does not contain the capability response query")

    print(json.dumps({
        "updated": True,
        "backup_path": str(backup_path),
        "version_id": version_id,
        "active": live.get("active"),
        "active_version_id": live.get("activeVersionId"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
