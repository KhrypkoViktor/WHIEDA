"""Publish read-only public ref profile API (GET). Admin upsert removed — use Partners_Ref sync."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
WORKFLOW_NAME = "WHIEDA Public Ref API"
PUBLIC_PATH = "whieda-public-ref-v1"
POSTGRES_CREDENTIAL = {"postgres": {"id": "RmjHh3rdZri7axzq", "name": "advisor-dev-postgres"}}

PUBLIC_QUERY = """SELECT ref_code,
       tenant_id,
       owner_id,
       display_mode,
       enabled,
       country_code,
       region_code,
       profile_version,
       public_profile
FROM referral_profiles
WHERE tenant_id = 'whieda'
  AND ref_code = '{{ String($json.ref_code || '').replace(/'/g, '') }}'
  AND enabled = true
LIMIT 1;"""

NORMALIZE_JS = r"""const q = $input.first().json.query || {};
const ref = String(q.ref || q.ref_code || '').trim().toLowerCase();
if (!ref) {
  return [{ json: { ok: false, error: 'ref_required', status_code: 400 } }];
}
return [{ json: { ref_code: ref } }];"""

FORMAT_PUBLIC_JS = r"""const items = $input.all();
const row = (items[0] && items[0].json) ? items[0].json : {};
if (row.status_code) return [{ json: row }];
if (!row.ref_code) {
  return [{ json: { ok: false, error: 'ref_not_found', status_code: 404 } }];
}
const profile = row.public_profile || {};
return [{ json: {
  ok: true,
  ref_code: row.ref_code,
  display_mode: row.display_mode,
  enabled: row.enabled,
  profile_version: row.profile_version,
  consultant: {
    display_name: profile.display_name || null,
    page_mode: profile.page_mode || null,
    public_site_url: profile.public_site_url || null,
    site_type: profile.site_type || null,
    focus_group: profile.focus_group === true,
    access_tier: profile.access_tier || null,
  },
  status_code: 200,
} }];"""


def load_helpers():
    path = BASE / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("h", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_workflow() -> dict:
    return {
        "name": WORKFLOW_NAME,
        "active": True,
        "nodes": [
            {
                "parameters": {"httpMethod": "GET", "path": PUBLIC_PATH, "responseMode": "responseNode", "options": {}},
                "id": "public-webhook",
                "name": "Webhook: Public Ref",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [-420, 0],
            },
            {
                "parameters": {"jsCode": NORMALIZE_JS},
                "id": "public-normalize",
                "name": "Code: Normalize Public Ref",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [-220, 0],
            },
            {
                "parameters": {
                    "conditions": {
                        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                        "conditions": [
                            {
                                "id": "has-ref",
                                "leftValue": "={{ $json.status_code }}",
                                "rightValue": 400,
                                "operator": {"type": "number", "operation": "equals"},
                            }
                        ],
                        "combinator": "and",
                    },
                    "options": {},
                },
                "id": "if-missing-ref",
                "name": "IF: Missing Ref",
                "type": "n8n-nodes-base.if",
                "typeVersion": 2,
                "position": [-40, 0],
            },
            {
                "parameters": {"operation": "executeQuery", "query": PUBLIC_QUERY, "options": {}},
                "id": "public-query",
                "name": "Postgres: Load Public Ref",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [180, 80],
                "alwaysOutputData": True,
                "credentials": POSTGRES_CREDENTIAL,
            },
            {
                "parameters": {"jsCode": FORMAT_PUBLIC_JS},
                "id": "public-format",
                "name": "Code: Format Public Ref",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [380, 80],
            },
            {
                "parameters": {
                    "respondWith": "json",
                    "responseBody": "={{ $json }}",
                    "options": {"responseCode": "={{ $json.status_code || 200 }}"},
                },
                "id": "public-respond",
                "name": "Respond: Public Ref",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1.1,
                "position": [580, 0],
            },
        ],
        "connections": {
            "Webhook: Public Ref": {"main": [[{"node": "Code: Normalize Public Ref", "type": "main", "index": 0}]]},
            "Code: Normalize Public Ref": {"main": [[{"node": "IF: Missing Ref", "type": "main", "index": 0}]]},
            "IF: Missing Ref": {
                "main": [
                    [{"node": "Respond: Public Ref", "type": "main", "index": 0}],
                    [{"node": "Postgres: Load Public Ref", "type": "main", "index": 0}],
                ]
            },
            "Postgres: Load Public Ref": {"main": [[{"node": "Code: Format Public Ref", "type": "main", "index": 0}]]},
            "Code: Format Public Ref": {"main": [[{"node": "Respond: Public Ref", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def strip_admin_nodes(workflow: dict) -> list[str]:
    """Remove partner-admin webhook nodes from an existing workflow payload."""
    removed: list[str] = []
    nodes = workflow.get("nodes") or []
    admin_ids = {
        node.get("id")
        for node in nodes
        if str((node.get("parameters") or {}).get("path") or "") == "whieda-partner-profile-admin-v1"
    }
    if not admin_ids:
        return removed
    workflow["nodes"] = [node for node in nodes if node.get("id") not in admin_ids]
    connections = workflow.get("connections") or {}
    for source, block in list(connections.items()):
        mains = block.get("main") or []
        new_mains = []
        for branch in mains:
            new_branch = [edge for edge in branch if edge.get("node") not in admin_ids]
            if new_branch:
                new_mains.append(new_branch)
        if new_mains:
            connections[source] = {"main": new_mains}
        else:
            connections.pop(source, None)
    workflow["connections"] = connections
    removed.extend(sorted(admin_ids))
    return removed


def main() -> None:
    helpers = load_helpers()
    session = helpers.login_session()
    backup_dir = BASE.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    deactivated = []

    existing = session.get(f"{helpers.BASE_URL}/rest/workflows?limit=200", verify=False, timeout=60).json()
    workflow_id = None
    for row in existing.get("data", []):
        if row.get("name") == WORKFLOW_NAME:
            workflow_id = row.get("id")
            break

    workflow = build_workflow()
    if workflow_id:
        current = session.get(f"{helpers.BASE_URL}/rest/workflows/{workflow_id}", verify=False, timeout=60).json()["data"]
        backup = backup_dir / f"whieda-public-ref-api-before-{datetime.now():%Y%m%d-%H%M%S}.json"
        backup.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        deactivated = strip_admin_nodes(current)
        workflow["id"] = workflow_id
        saved = session.patch(f"{helpers.BASE_URL}/rest/workflows/{workflow_id}", json=workflow, verify=False, timeout=120)
    else:
        saved = session.post(f"{helpers.BASE_URL}/rest/workflows", json=workflow, verify=False, timeout=120)

    saved.raise_for_status()
    data = saved.json().get("data", saved.json())
    workflow_id = data["id"]
    version = data.get("versionId")
    session.post(
        f"{helpers.BASE_URL}/rest/workflows/{workflow_id}/activate",
        json={"versionId": version},
        verify=False,
        timeout=60,
    ).raise_for_status()
    helpers.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={workflow_id}")
    print(
        json.dumps(
            {
                "workflow_id": workflow_id,
                "version_id": version,
                "public_webhook": PUBLIC_PATH,
                "admin_webhooks_deactivated": deactivated,
                "mode": "read_only",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
