"""Replace hardcoded /leads actor map with lead_actors + lead_actor_roles SQL lookup."""

from __future__ import annotations

import importlib.util
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

import requests

BASE_DIR = Path(__file__).resolve().parent
WORKFLOW_ID = "advisor-whieda-phase1"
MIGRATION = BASE_DIR.parents[1] / "postgres" / "sql" / "whieda_lead_actor_telegram_bindings_2026-08-01.sql"
POSTGRES_CREDENTIAL = {"postgres": {"id": "RmjHh3rdZri7axzq", "name": "advisor-dev-postgres"}}

LOOKUP_NODE_NAME = "Postgres: Lookup lead actor"
LOOKUP_QUERY = """SELECT
  la.actor_id,
  la.display_name,
  CASE
    WHEN bool_or(lar.role IN ('platform_owner', 'tenant_admin', 'market_admin')) THEN 'all'
    WHEN bool_or(lar.role = 'lead_watcher') THEN 'watcher'
    ELSE 'assigned'
  END AS leads_scope,
  bool_or(lar.role IN ('platform_owner', 'tenant_admin')) AS can_report
FROM lead_actors la
LEFT JOIN lead_actor_roles lar
  ON lar.actor_id = la.actor_id
 AND lar.tenant_id = la.tenant_id
 AND lar.active = true
WHERE la.tenant_id = 'whieda'
  AND la.active = true
  AND (
    lower(coalesce(la.telegram_username, '')) = lower('{{ String($json.external_username || '').replace(/'/g, '') }}')
    OR la.telegram_chat_id = '{{ String($json.external_chat_id || '').replace(/'/g, '') }}'
  )
GROUP BY la.actor_id, la.display_name
LIMIT 1;"""

ROUTER_JS = r"""const runtime = $input.first()?.json || {};
const envelope = $('Code: Normalize Payload').first().json;
const input = { ...envelope, ...runtime };
const text = String(input.message_text || '').trim();
const isLeads = /^\/leads(?:@\w+)?(?:\s|$)/i.test(text);
const isReport = /^\/report(?:@\w+)?\s*$/i.test(text);
const leadsMatch = text.match(/^\/leads(?:@\w+)?(?:\s+(mine|new))?\s*$/i);
const leadsMode = leadsMatch?.[1]?.toLowerCase() || 'default';
const detailMatch = text.match(/^\/lead(?:@\w+)?\s+(L-[A-Z0-9]{6,24})\s*$/i);
const amountMatch = text.match(/^\/lead(?:@\w+)?\s+(L-[A-Z0-9]{6,24})\s+(?:сумма|sum)\s+(\d+(?:[.,]\d{1,2})?)\s*(BYN|RUB|W\$)\s*$/i);
const actionMatch = String(input.callback_data || '').match(/^lead:(L-[A-Z0-9]{6,24}):(in_progress|completed)$/i);
const actorId = String(runtime.actor_id || '').trim();
const leadsScope = String(runtime.leads_scope || 'none');
const canReport = runtime.can_report === true || runtime.can_report === 'true' || leadsScope === 'all';
const hasAccess = Boolean(actorId && input.trusted_reviewer);
return [{ json: {
  ...input,
  is_leads_command: isLeads,
  is_report_command: isReport,
  is_owner_report: Boolean(isReport && canReport && hasAccess),
  is_lead_action: Boolean(actionMatch),
  is_lead_amount: Boolean(amountMatch),
  is_lead_detail: Boolean(detailMatch),
  is_leads_control: isLeads || isReport || Boolean(actionMatch) || Boolean(amountMatch) || Boolean(detailMatch),
  leads_access: Boolean((isLeads || isReport || actionMatch || amountMatch || detailMatch) && hasAccess),
  lead_actor_id: actorId,
  leads_scope: leadsScope,
  leads_mode: leadsMode,
  lead_public_id: actionMatch ? actionMatch[1].toUpperCase() : (amountMatch ? amountMatch[1].toUpperCase() : (detailMatch ? detailMatch[1].toUpperCase() : '')),
  lead_target_status: actionMatch ? actionMatch[2].toLowerCase() : '',
  lead_amount_value: amountMatch ? Number(String(amountMatch[2]).replace(',', '.')) : null,
  lead_amount_currency: amountMatch ? String(amountMatch[3]).toUpperCase() : '',
} }];"""

ACCESS_SQL = """(
  '{{ String($json.leads_scope).replace(/'/g, '') }}' = 'all'
  OR assigned_owner_id = '{{ String($json.lead_actor_id).replace(/'/g, '') }}'
  OR EXISTS (
    SELECT 1 FROM website_lead_watchers wl
    WHERE wl.lead_id = website_leads.lead_id
      AND wl.watcher_actor_id = '{{ String($json.lead_actor_id).replace(/'/g, '') }}'
      AND wl.enabled = true
  )
)"""

ACCESS_SQL_ALIAS_L = """(
  '{{ String($json.leads_scope).replace(/'/g, '') }}' = 'all'
  OR l.assigned_owner_id = '{{ String($json.lead_actor_id).replace(/'/g, '') }}'
  OR EXISTS (
    SELECT 1 FROM website_lead_watchers wl
    WHERE wl.lead_id = l.lead_id
      AND wl.watcher_actor_id = '{{ String($json.lead_actor_id).replace(/'/g, '') }}'
      AND wl.enabled = true
  )
)"""


def load_helper():
    path = BASE_DIR / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_sync", path)
    if not spec or not spec.loader:
        raise RuntimeError("Cannot load n8n helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_sql_migration(helper) -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    session = helper.login_session()
    suffix = uuid.uuid4().hex[:12]
    path = f"whieda-lead-actor-migration-{suffix}"
    workflow = {
        "name": f"TEMP WHIEDA Lead Actor Migration {suffix}",
        "active": False,
        "nodes": [
            {
                "parameters": {"httpMethod": "POST", "path": path, "responseMode": "responseNode", "options": {}},
                "id": "webhook",
                "name": "Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [-260, 0],
            },
            {
                "parameters": {"operation": "executeQuery", "query": sql, "options": {}},
                "id": "query",
                "name": "Apply migration",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [0, 0],
                "credentials": POSTGRES_CREDENTIAL,
            },
            {
                "parameters": {"respondWith": "json", "responseBody": "={{ { \"ok\": true } }}", "options": {"responseCode": 200}},
                "id": "respond",
                "name": "Respond",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1.1,
                "position": [260, 0],
            },
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Apply migration", "type": "main", "index": 0}]]},
            "Apply migration": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
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
        session.post(
            f"{helper.BASE_URL}/rest/workflows/{workflow_id}/activate",
            json={"versionId": version},
            verify=False,
            timeout=60,
        ).raise_for_status()
        requests.post(f"{helper.BASE_URL}/webhook/{path}", json={}, verify=False, timeout=60).raise_for_status()
    finally:
        if workflow_id:
            session.delete(f"{helper.BASE_URL}/rest/workflows/{workflow_id}", verify=False, timeout=60)


def replace_viktor_access(query: str, *, table_alias: str | None = None) -> str:
    pattern = (
        r"\('?\{\{ String\(\$json\.lead_actor_id\)\.replace\(/'/g, ''\) \}\}'? = 'viktor'"
        r" OR (?:l\.)?assigned_owner_id = '\{\{ String\(\$json\.lead_actor_id\)\.replace\(/'/g, ''\) \}\}'\)"
    )
    replacement = ACCESS_SQL_ALIAS_L if table_alias == "l" else ACCESS_SQL
    return re.sub(pattern, replacement, query)


def ensure_lookup_node(workflow: dict) -> None:
    nodes = {node["name"]: node for node in workflow["nodes"]}
    if LOOKUP_NODE_NAME not in nodes:
        normalize = nodes["Code: Normalize Payload"]
        workflow["nodes"].append(
            {
                "id": "lead-actor-lookup",
                "name": LOOKUP_NODE_NAME,
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [normalize["position"][0] + 220, normalize["position"][1]],
                "credentials": POSTGRES_CREDENTIAL,
                "parameters": {"operation": "executeQuery", "query": LOOKUP_QUERY, "options": {}},
            }
        )
    else:
        nodes[LOOKUP_NODE_NAME]["parameters"]["query"] = LOOKUP_QUERY

    connections = workflow["connections"]
    connections["Code: Normalize Payload"] = {
        "main": [[{"node": LOOKUP_NODE_NAME, "type": "main", "index": 0}]]
    }
    connections[LOOKUP_NODE_NAME] = {
        "main": [[{"node": "Code: Route /leads", "type": "main", "index": 0}]]
    }


def patch_workflow(workflow: dict) -> None:
    nodes = {node["name"]: node for node in workflow["nodes"]}
    nodes["Code: Route /leads"]["parameters"]["jsCode"] = ROUTER_JS
    ensure_lookup_node(workflow)

    for name in (
        "Postgres: Update lead status",
        "Postgres: Get lead detail",
        "Postgres: Update lead amount",
    ):
        if name not in nodes:
            continue
        query = nodes[name]["parameters"]["query"]
        if "lead_actor_id" in query and "viktor" in query:
            alias = "l" if name == "Postgres: Get lead detail" else None
            nodes[name]["parameters"]["query"] = replace_viktor_access(query, table_alias=alias)


def main() -> None:
    helper = load_helper()
    run_sql_migration(helper)

    session = helper.login_session()
    response = session.get(f"{helper.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
    response.raise_for_status()
    workflow = response.json()["data"]

    backup_dir = BASE_DIR.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"advisor-whieda-before-lead-actor-sql-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

    patch_workflow(workflow)

    saved = session.patch(f"{helper.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=120)
    saved.raise_for_status()
    version = saved.json().get("data", saved.json()).get("versionId")
    if not version:
        raise RuntimeError("n8n did not return versionId")

    session.post(
        f"{helper.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate",
        json={"versionId": version},
        verify=False,
        timeout=60,
    ).raise_for_status()
    helper.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    helper.ssh_run("docker restart n8n-n8n-1")
    helper.wait_for_n8n_ready()
    print(
        json.dumps(
            {"workflow_id": WORKFLOW_ID, "version_id": version, "backup": str(backup_path)},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
