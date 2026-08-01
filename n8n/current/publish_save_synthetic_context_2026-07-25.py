"""Preserve product context for synthetic tests without creating review-queue items."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
WORKFLOW_ID = "advisor-whieda-phase1"
SAVE_NODE_NAME = "Postgres: Save Synthetic Conversation Context"


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
    backup_path = backup_dir / f"advisor-whieda-before-synthetic-context-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

    nodes = [node for node in workflow["nodes"] if node["name"] != SAVE_NODE_NAME]
    filter_node = next(node for node in nodes if node["name"] == "IF: Skip Synthetic Review Queue?")
    gap_audit = next(node for node in nodes if "Write Audit" in node["name"] and node["name"].endswith("Gap"))
    nodes.append({
        "parameters": {
            "operation": "executeQuery",
            "query": """CREATE TABLE IF NOT EXISTS advisor_conversation_context (
  client_id text NOT NULL,
  conversation_id uuid NOT NULL,
  context jsonb NOT NULL DEFAULT '{}'::jsonb,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, conversation_id)
);

WITH payload AS (
  SELECT
    '{{ String($json.project_id ?? 'whieda').replace(/'/g, '') }}'::text AS client_id,
    NULLIF('{{ String($json.conversation_id ?? '').replace(/'/g, '') }}', '')::uuid AS conversation_id,
    '{{ JSON.stringify({
      last_product_sku: $json.structured_match?.sku,
      last_product_name: $json.structured_match?.canonical_name,
      last_product_alias: $json.structured_match?.alias
    }).replace(/'/g, '') }}'::jsonb AS context
)
INSERT INTO advisor_conversation_context (client_id, conversation_id, context, updated_at)
SELECT client_id, conversation_id, context, now()
FROM payload
WHERE conversation_id IS NOT NULL
  AND context ? 'last_product_sku'
ON CONFLICT (client_id, conversation_id)
DO UPDATE SET
  context = advisor_conversation_context.context || EXCLUDED.context,
  updated_at = now();""",
            "options": {},
        },
        "id": "whieda-save-synthetic-conversation-context",
        "name": SAVE_NODE_NAME,
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.6,
        "credentials": gap_audit.get("credentials", {}),
        "position": [filter_node["position"][0] + 230, filter_node["position"][1] + 120],
        "notes": "Synthetic tests keep real conversational state but never enter the human review queue.",
    })
    workflow["nodes"] = nodes
    workflow["connections"][filter_node["name"]] = {
        "main": [
            [{"node": "Postgres: Write Review Queue", "type": "main", "index": 0}],
            [{"node": SAVE_NODE_NAME, "type": "main", "index": 0}],
        ]
    }
    workflow["connections"][SAVE_NODE_NAME] = {
        "main": [[{"node": gap_audit["name"], "type": "main", "index": 0}]]
    }

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
