"""Skip synthetic test gaps before the review queue, preserving real gaps."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
WORKFLOW_ID = "advisor-whieda-phase1"
FILTER_NAME = "IF: Skip Synthetic Review Queue?"


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
    backup = json.loads(json.dumps(workflow))
    backup_dir = BASE_DIR.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"advisor-whieda-before-synthetic-review-skip-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")

    nodes = [node for node in workflow["nodes"] if node["name"] != FILTER_NAME]
    gap_node = next(node for node in nodes if node["name"] == "IF: Route Gap True?")
    review_node = next(node for node in nodes if node["name"] == "Postgres: Write Review Queue")
    gap_audit = next(node for node in nodes if "Write Audit" in node["name"] and node["name"].endswith("Gap"))
    answer_audit = next(node for node in nodes if "Write Audit" in node["name"] and node["name"].endswith("Answer"))
    nodes.append({
        "parameters": {
            "conditions": {
                "options": {"caseSensitive": False, "leftValue": "", "typeValidation": "loose", "version": 1},
                "combinator": "and",
                "conditions": [{
                    "id": "skip-synthetic-review",
                    "operator": {"type": "string", "operation": "notEquals"},
                    "leftValue": "={{ $json.review_queue_status || '' }}",
                    "rightValue": "ignored_test",
                }],
            },
            "options": {},
        },
        "id": "whieda-skip-synthetic-review-queue",
        "name": FILTER_NAME,
        "type": "n8n-nodes-base.if",
        "typeVersion": 2,
        "position": [gap_node["position"][0] + 224, gap_node["position"][1]],
        "notes": "true -> real gap enters review queue; false -> synthetic test gap is audit-only",
    })
    workflow["nodes"] = nodes
    connections = workflow["connections"]
    connections[FILTER_NAME] = {
        "main": [
            [{"node": review_node["name"], "type": "main", "index": 0}],
            [{"node": gap_audit["name"], "type": "main", "index": 0}],
        ]
    }
    connections[gap_node["name"]] = {
        "main": [
            [{"node": FILTER_NAME, "type": "main", "index": 0}],
            [{"node": answer_audit["name"], "type": "main", "index": 0}],
        ]
    }

    saved = session.patch(f"{helpers.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=60)
    saved.raise_for_status()
    saved_data = saved.json().get("data", saved.json())
    version_id = saved_data.get("versionId")
    if not version_id:
        refreshed = session.get(f"{helpers.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=30)
        refreshed.raise_for_status()
        version_id = refreshed.json()["data"].get("versionId")
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
