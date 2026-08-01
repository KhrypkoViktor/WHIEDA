"""Transient n8n-based read-only runtime database inspector.

It creates an isolated webhook workflow using the existing Postgres credential,
requests a single SELECT-only diagnostic payload, then removes the workflow.
No runtime tables or application workflows are changed.
"""

from __future__ import annotations

import importlib.util
import json
import uuid

import requests


HELPER_PATH = r"D:\Projects\WHIEDA\n8n\current\publish_and_run_whieda_sync_2026-07-13.py"
POSTGRES_CREDENTIAL = {"postgres": {"id": "RmjHh3rdZri7axzq", "name": "advisor-dev-postgres"}}

QUERY = """
WITH expected(name) AS (
  VALUES
    ('website_leads'), ('website_lead_status_history'), ('website_lead_owner_history'),
    ('lead_actors'), ('lead_actor_roles'), ('referral_profiles'),
    ('website_lead_watchers'), ('lead_visibility_rules'), ('lead_delivery_attempts'),
    ('advisor_conversations'), ('advisor_audit_events'), ('advisor_structured_sync_runs')
), tables AS (
  SELECT e.name, EXISTS (
    SELECT 1 FROM information_schema.tables t
    WHERE t.table_schema = 'public' AND t.table_name = e.name
  ) AS present
  FROM expected e
), leads AS (
  SELECT CASE WHEN EXISTS (SELECT 1 FROM tables WHERE name = 'website_leads' AND present)
    THEN (SELECT count(*) FROM website_leads WHERE tenant_id = 'whieda') ELSE 0 END AS total
), users AS (
  SELECT CASE WHEN EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'advisor_conversations')
    THEN (SELECT count(DISTINCT external_chat_id) FROM advisor_conversations WHERE project_id = 'whieda') ELSE 0 END AS total
)
SELECT
  (SELECT jsonb_object_agg(name, present) FROM tables) AS tables,
  (SELECT total FROM leads) AS whieda_leads,
  (SELECT total FROM users) AS advisor_users;
"""


def load_helper():
    spec = importlib.util.spec_from_file_location("whieda_sync", HELPER_PATH)
    if not spec or not spec.loader:
        raise RuntimeError("Cannot load n8n helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    helper = load_helper()
    session = helper.login_session()
    suffix = uuid.uuid4().hex[:12]
    path = f"whieda-db-readonly-{suffix}"
    workflow = {
        "name": f"TEMP WHIEDA DB Readonly {suffix}",
        "active": False,
        "nodes": [
            {"parameters": {"httpMethod": "GET", "path": path, "responseMode": "responseNode", "options": {}}, "id": "webhook", "name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [-260, 0]},
            {"parameters": {"operation": "executeQuery", "query": QUERY, "options": {}}, "id": "query", "name": "Read-only DB query", "type": "n8n-nodes-base.postgres", "typeVersion": 2.6, "position": [0, 0], "credentials": POSTGRES_CREDENTIAL},
            {"parameters": {"respondWith": "json", "responseBody": "={{ $json }}", "options": {"responseCode": 200}}, "id": "respond", "name": "Respond", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [260, 0]},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Read-only DB query", "type": "main", "index": 0}]]},
            "Read-only DB query": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }
    workflow_id = None
    try:
        created = session.post(f"{helper.BASE_URL}/rest/workflows", json=workflow, verify=False, timeout=60)
        created.raise_for_status()
        data = created.json().get("data", created.json())
        workflow_id = data["id"]
        version = data.get("versionId")
        session.post(f"{helper.BASE_URL}/rest/workflows/{workflow_id}/activate", json={"versionId": version}, verify=False, timeout=60).raise_for_status()
        result = requests.get(f"{helper.BASE_URL}/webhook/{path}", verify=False, timeout=60)
        result.raise_for_status()
        print(json.dumps(result.json(), ensure_ascii=False))
    finally:
        if workflow_id:
            session.delete(f"{helper.BASE_URL}/rest/workflows/{workflow_id}", verify=False, timeout=60)


if __name__ == "__main__":
    main()
