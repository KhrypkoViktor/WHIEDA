from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / "n8n" / "current" / "wwc_website_lead_v1_2026-08-02.json"
SERVICE = ROOT / "backend" / "platform-api" / "app" / "leads" / "service.py"

ASSIGN_ACTIVE_FIRST = "COALESCE(active_profile.owner_id, first_profile.owner_id, 'viktor')"


def _lead_workflow_nodes() -> list[dict]:
    payload = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    workflow = payload[0] if isinstance(payload, list) else payload
    return workflow["nodes"]


def test_n8n_lead_sql_assigns_site_owner_from_active_ref():
    nodes = _lead_workflow_nodes()
    postgres = next(node for node in nodes if node.get("name") == "Postgres: save lead and audit")
    query = postgres["parameters"]["query"]
    assert ASSIGN_ACTIVE_FIRST in query.replace("\n", " ")


def test_n8n_validate_recovers_ref_from_page_url():
    nodes = _lead_workflow_nodes()
    validate = next(node for node in nodes if node.get("name") == "Code: validate and assign owner")
    js = validate["parameters"]["jsCode"]
    assert "searchParams.get('ref')" in js or 'searchParams.get("ref")' in js
    assert "elena" in js and "onlineelena" in js


def test_platform_save_lead_sql_assigns_site_owner_from_active_ref():
    sql = SERVICE.read_text(encoding="utf-8")
    assert "coalesce(active_profile.owner_id, first_profile.owner_id, 'viktor')" in sql.lower()
