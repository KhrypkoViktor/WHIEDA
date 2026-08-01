"""Replace serial structured reads with one Postgres runtime snapshot."""

from __future__ import annotations

import importlib.util
import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
WORKFLOW_ID = "advisor-whieda-phase1"
SNAPSHOT = "Postgres: Runtime Snapshot"
REMOVED = {
    "Google Sheets: Products_Prices", "Google Sheets: Product_Aliases",
    "Postgres: Product Cards", "Postgres: Product Details", "Postgres: Product Comparisons",
    "Postgres: Resource Links", "Postgres: Business Objections", "Postgres: Business FAQ",
    "Postgres: Clarification Prompts", "Postgres: Capability Responses",
}

QUERY = """CREATE TABLE IF NOT EXISTS advisor_promotions (
  client_id text NOT NULL,
  promotion_id text NOT NULL,
  tenant_id text NOT NULL DEFAULT 'by',
  title text NOT NULL,
  short_text text,
  full_text text,
  country text,
  city text,
  starts_at timestamptz NOT NULL,
  ends_at timestamptz NOT NULL,
  timezone text,
  promotion_type text,
  product_ids text,
  min_amount text,
  min_pv text,
  benefit_text text,
  source_url text,
  image_url text,
  priority integer NOT NULL DEFAULT 100,
  status text NOT NULL DEFAULT 'draft',
  owner text,
  updated_at text,
  PRIMARY KEY (client_id, promotion_id)
);

CREATE TABLE IF NOT EXISTS advisor_product_recommendation_rules (
  client_id text NOT NULL,
  product_id text NOT NULL,
  tenant_id text NOT NULL DEFAULT 'by',
  registration_enabled boolean NOT NULL DEFAULT true,
  availability_status text NOT NULL DEFAULT 'available',
  universality_score integer NOT NULL DEFAULT 0,
  popularity_score integer NOT NULL DEFAULT 0,
  demo_score integer NOT NULL DEFAULT 0,
  gift_score integer NOT NULL DEFAULT 0,
  resale_score integer NOT NULL DEFAULT 0,
  personal_use_score integer NOT NULL DEFAULT 0,
  explanation_difficulty integer NOT NULL DEFAULT 0,
  price_sensitivity integer NOT NULL DEFAULT 0,
  business_priority integer NOT NULL DEFAULT 0,
  goal_tags text,
  audience_tags text,
  reason_short text,
  avoid_when text,
  status text NOT NULL DEFAULT 'draft',
  owner text,
  updated_at text,
  PRIMARY KEY (client_id, product_id)
);

CREATE TABLE IF NOT EXISTS advisor_starter_basket_templates (
  client_id text NOT NULL,
  template_id text NOT NULL,
  tenant_id text NOT NULL DEFAULT 'by',
  title text NOT NULL,
  goal text NOT NULL DEFAULT 'balanced',
  budget_min text,
  budget_max text,
  target_pv_min text,
  target_pv_max text,
  required_product_ids text,
  preferred_product_ids text,
  excluded_product_ids text,
  description text,
  priority integer NOT NULL DEFAULT 100,
  status text NOT NULL DEFAULT 'draft',
  owner text,
  updated_at text,
  PRIMARY KEY (client_id, template_id)
);

CREATE TABLE IF NOT EXISTS advisor_whieda_events (
  client_id text NOT NULL,
  event_id text NOT NULL,
  tenant_id text NOT NULL DEFAULT 'by',
  title text NOT NULL,
  event_type text,
  description text,
  starts_at timestamptz NOT NULL,
  ends_at timestamptz,
  timezone text,
  country text,
  city text,
  address text,
  online_url text,
  contact text,
  audience_segment text,
  leader_id text,
  image_url text,
  source_url text,
  reminder_offsets text,
  status text NOT NULL DEFAULT 'draft',
  owner text,
  updated_at text,
  recurrence_rule text,
  PRIMARY KEY (client_id, event_id)
);

CREATE TABLE IF NOT EXISTS advisor_whieda_community_resources (
  client_id text NOT NULL,
  resource_id text NOT NULL,
  tenant_id text NOT NULL DEFAULT 'by',
  leader_id text,
  title text NOT NULL,
  category text,
  description text,
  url text NOT NULL,
  platform text,
  country text,
  city text,
  audience text,
  topic_tags text,
  access_level text NOT NULL DEFAULT 'public',
  priority integer NOT NULL DEFAULT 100,
  is_official boolean NOT NULL DEFAULT false,
  status text NOT NULL DEFAULT 'draft',
  last_checked_at text,
  owner text,
  updated_at text,
  notes text,
  PRIMARY KEY (client_id, resource_id)
);

SELECT
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_structured_products t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS products,
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_structured_aliases t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS aliases,
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_structured_product_cards t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS product_cards,
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_structured_product_details t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS product_details,
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_structured_product_comparisons t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS product_comparisons,
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_structured_resources t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS resources,
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_structured_business_objections t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS business_objections,
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_structured_business_faq t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS business_faq,
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_promotions t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS promotions,
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_product_recommendation_rules t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS recommendation_rules,
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_starter_basket_templates t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS starter_basket_templates,
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_whieda_events t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS events,
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_whieda_community_resources t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS community_resources,
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_structured_clarification_prompts t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS clarification_prompts,
  COALESCE((SELECT jsonb_agg(to_jsonb(t)) FROM advisor_structured_capability_responses t WHERE t.client_id = 'whieda'), '[]'::jsonb) AS capability_responses;"""


# The main workflow already owns creation, confirmation and cancellation of
# meetings. This append-only fragment adds moving a confirmed meeting without
# creating a second event or leaving its old reminder jobs alive.
MEETING_RESCHEDULE_SQL = """UPDATE advisor_scheduled_events
SET meeting_at = NULLIF('{{ String($('Code: Structured Sheet Lookup').first().json.meeting_at ?? '').replace(/'/g, '') }}', '')::timestamptz
WHERE client_id = '{{ String($('Code: Structured Sheet Lookup').first().json.project_id ?? 'whieda').replace(/'/g, '') }}'
  AND event_id = '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_draft_id ?? '').replace(/'/g, '') }}'
  AND created_by_user_id = '{{ String($('Code: Structured Sheet Lookup').first().json.external_user_id ?? '').replace(/'/g, '') }}'
  AND status = 'confirmed'
  AND '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_action ?? '').replace(/'/g, '') }}' = 'reschedule_meeting';

UPDATE advisor_scheduled_event_jobs j
SET scheduled_at = e.meeting_at - CASE j.reminder_key
    WHEN '24h' THEN interval '24 hours'
    WHEN '1h' THEN interval '1 hour'
    ELSE interval '0 hours'
  END,
  status = CASE
    WHEN e.meeting_at - CASE j.reminder_key
      WHEN '24h' THEN interval '24 hours'
      WHEN '1h' THEN interval '1 hour'
      ELSE interval '0 hours'
    END > now() THEN 'pending'
    ELSE 'cancelled'
  END
FROM advisor_scheduled_events e
WHERE j.client_id = e.client_id
  AND j.event_id = e.event_id
  AND e.client_id = '{{ String($('Code: Structured Sheet Lookup').first().json.project_id ?? 'whieda').replace(/'/g, '') }}'
  AND e.event_id = '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_draft_id ?? '').replace(/'/g, '') }}'
  AND '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_action ?? '').replace(/'/g, '') }}' = 'reschedule_meeting';"""


HIERARCHICAL_BROADCAST_SNAPSHOT_SQL = """ALTER TABLE advisor_broadcast_drafts
  ADD COLUMN IF NOT EXISTS structure_code text NOT NULL DEFAULT 'general';

WITH RECURSIVE actor AS (
  SELECT
    '{{ String($('Postgres: Lookup User Access').first().json.structure_code ?? 'general').replace(/'/g, '') }}'::text AS structure_code,
    '{{ String($('Postgres: Lookup User Access').first().json.role ?? 'partner').replace(/'/g, '') }}'::text AS role
), scope AS (
  SELECT structure_code FROM actor
  UNION
  SELECT child.structure_code
  FROM advisor_structured_structure_owners child
  JOIN scope parent ON parent.structure_code = child.parent_structure_code
  CROSS JOIN actor
  WHERE child.client_id = 'whieda'
    AND lower(COALESCE(child.status, '')) = 'active'
    AND lower(actor.role) = 'super_admin'
)
SELECT
  count(*) FILTER (WHERE s.telegram_user_id IS NOT NULL AND lower(COALESCE(a.access_status, '')) = 'candidate')::text AS candidates_subscriber_count,
  count(*) FILTER (WHERE s.telegram_user_id IS NOT NULL AND lower(COALESCE(a.access_status, '')) = 'approved' AND lower(COALESCE(a.role, 'partner')) = 'partner')::text AS partners_subscriber_count,
  count(*) FILTER (WHERE s.telegram_user_id IS NOT NULL AND lower(COALESCE(a.access_status, '')) = 'approved' AND lower(COALESCE(a.role, '')) IN ('leader', 'admin', 'super_admin'))::text AS leaders_subscriber_count,
  COALESCE(array_to_string(array_agg(DISTINCT scope.structure_code), '|'), 'general') AS structure_codes
FROM scope
LEFT JOIN advisor_telegram_subscriptions s
  ON s.client_id = 'whieda'
 AND s.is_subscribed IS TRUE
 AND s.blocked_at IS NULL
LEFT JOIN advisor_structured_users_access a
  ON a.client_id = s.client_id
 AND a.telegram_user_id = s.telegram_user_id
 AND COALESCE(a.structure_code, 'general') = scope.structure_code;"""


def load_helpers():
    path = BASE_DIR / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_sync", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def main() -> None:
    pub = load_helpers(); session = pub.login_session()
    response = session.get(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60); response.raise_for_status()
    workflow = response.json()["data"]
    backup_dir = BASE_DIR.parent / "backups"; backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"advisor-whieda-before-runtime-snapshot-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")
    template = next((node for node in workflow["nodes"] if node["name"] == SNAPSHOT), None)
    if template is None:
        template = next(node for node in workflow["nodes"] if node["name"] == "Postgres: Product Cards")
    load_context = next(node for node in workflow["nodes"] if node["name"] == "Postgres: Load Conversation Context")
    classifier = next(node for node in workflow["nodes"] if node["name"] == "Code: Need Admin Snapshots?")
    nodes = [node for node in workflow["nodes"] if node["name"] not in REMOVED | {SNAPSHOT}]
    nodes.append({"parameters": {"operation": "executeQuery", "query": QUERY, "options": {}}, "id": "whieda-runtime-snapshot", "name": SNAPSHOT, "type": "n8n-nodes-base.postgres", "typeVersion": 2.6, "credentials": template.get("credentials", {}), "position": [load_context["position"][0] + 220, load_context["position"][1]]})
    structured = next(node for node in nodes if node["name"] == "Code: Structured Sheet Lookup")
    structured["parameters"]["jsCode"] = (BASE_DIR / "structured_sheet_lookup_current.js").read_text(encoding="utf-8")
    access_lookup = next(node for node in nodes if node["name"] == "Postgres: Lookup User Access")
    access_lookup["parameters"]["query"] = """WITH access_row AS (
  SELECT access_status, role, structure_code
  FROM advisor_structured_users_access
  WHERE client_id = '{{ String($('Code: Normalize Payload').first().json.project_id ?? 'whieda').replace(/'/g, '') }}'
    AND telegram_user_id = '{{ String($('Code: Normalize Payload').first().json.external_user_id ?? '').replace(/'/g, '') }}'
  LIMIT 1
)
SELECT
  CASE
    WHEN '{{ String($('Code: Normalize Payload').first().json.chat_type ?? '').replace(/'/g, '') }}' <> 'private' THEN 'approved'
    ELSE COALESCE((SELECT access_status FROM access_row), 'candidate')
  END AS access_status,
  COALESCE((SELECT role FROM access_row), 'partner') AS role,
  COALESCE((SELECT structure_code FROM access_row), 'general') AS structure_code;"""
    admin_snapshot = next(node for node in nodes if node["name"] == "Code: Need Admin Snapshots?")
    admin_snapshot["parameters"]["jsCode"] = admin_snapshot["parameters"]["jsCode"].replace(
        "meeting|meeting_send|meeting_cancel", "meeting|meeting_send|meeting_cancel|meeting_reschedule"
    )
    broadcast_snapshot = next(node for node in nodes if node["name"] == "Postgres: Broadcast Snapshot")
    snapshot_query = broadcast_snapshot["parameters"]["query"]
    snapshot_marker = "\nSELECT\n  count(*) FILTER"
    if snapshot_marker not in snapshot_query:
        raise RuntimeError("Broadcast Snapshot query has an unexpected shape")
    broadcast_snapshot["parameters"]["query"] = snapshot_query.split(snapshot_marker, 1)[0] + "\n" + HIERARCHICAL_BROADCAST_SNAPSHOT_SQL
    broadcast_command = next(node for node in nodes if node["name"] == "Postgres: Broadcast Command")
    command_query = broadcast_command["parameters"]["query"]
    if "broadcast_structure_code" not in command_query:
        command_query = command_query.replace(
            "ADD COLUMN IF NOT EXISTS broadcast_type text NOT NULL DEFAULT 'announcement';",
            "ADD COLUMN IF NOT EXISTS broadcast_type text NOT NULL DEFAULT 'announcement';\n\nALTER TABLE advisor_broadcast_drafts\n  ADD COLUMN IF NOT EXISTS structure_code text NOT NULL DEFAULT 'general';\n\nALTER TABLE advisor_scheduled_events\n  ADD COLUMN IF NOT EXISTS structure_code text NOT NULL DEFAULT 'general';",
        )
        command_query = command_query.replace(
            "(client_id, draft_id, created_by_user_id, text_body, audience, status, broadcast_type)",
            "(client_id, draft_id, created_by_user_id, text_body, audience, structure_code, status, broadcast_type)",
            1,
        ).replace(
            "  '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_audience ?? 'partners').replace(/'/g, '') }}',\n  'draft',",
            "  '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_audience ?? 'partners').replace(/'/g, '') }}',\n  '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_structure_code ?? 'general').replace(/'/g, '') }}',\n  'draft',",
            1,
        ).replace(
            "(client_id, event_id, created_by_user_id, audience, text_body, meeting_at, status)",
            "(client_id, event_id, created_by_user_id, audience, structure_code, text_body, meeting_at, status)",
            1,
        ).replace(
            "  '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_audience ?? 'partners').replace(/'/g, '') }}',\n  '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_text ?? '').replace(/'/g, '') }}',",
            "  '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_audience ?? 'partners').replace(/'/g, '') }}',\n  '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_structure_code ?? 'general').replace(/'/g, '') }}',\n  '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_text ?? '').replace(/'/g, '') }}',",
            1,
        )
    if "reschedule_meeting" not in command_query:
        prefix, final_select = command_query.rsplit("\nSELECT ", 1)
        broadcast_command["parameters"]["query"] = prefix + "\n" + MEETING_RESCHEDULE_SQL + "\nSELECT " + final_select
    context_saver = next((node for node in nodes if node["name"] == "Postgres: Save Synthetic Conversation Context"), None)
    if context_saver is not None:
        context_saver["parameters"]["query"] = """CREATE TABLE IF NOT EXISTS advisor_conversation_context (
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
    '{{ JSON.stringify((() => {
      const context = {};
      if ($json.structured_match?.sku) {
        context.last_product_sku = $json.structured_match.sku;
        context.last_product_name = $json.structured_match.canonical_name;
        context.last_product_alias = $json.structured_match.alias;
      }
      if ($json.basket_context) context.starter_basket = $json.basket_context;
      return context;
    })()).replace(/'/g, '') }}'::jsonb AS context
)
INSERT INTO advisor_conversation_context (client_id, conversation_id, context, updated_at)
SELECT client_id, conversation_id, context, now()
FROM payload
WHERE conversation_id IS NOT NULL
  AND context <> '{}'::jsonb
ON CONFLICT (client_id, conversation_id)
DO UPDATE SET
  context = advisor_conversation_context.context || EXCLUDED.context,
  updated_at = now();"""
    workflow["nodes"] = nodes
    connections = workflow["connections"]
    for name in list(connections):
        if name in REMOVED: del connections[name]
    for data in connections.values():
        for branch in data.get("main", []):
            branch[:] = [edge for edge in branch if edge.get("node") not in REMOVED]
    connections[load_context["name"]] = {"main": [[{"node": SNAPSHOT, "type": "main", "index": 0}]]}
    connections[SNAPSHOT] = {"main": [[{"node": classifier["name"], "type": "main", "index": 0}]]}
    saved = session.patch(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=120); saved.raise_for_status()
    version = saved.json().get("data", saved.json()).get("versionId")
    if not version: raise RuntimeError("n8n did not return versionId")
    activated = session.post(f"{pub.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate", json={"versionId": version}, verify=False, timeout=60); activated.raise_for_status()
    pub.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}"); pub.ssh_run("docker restart n8n-n8n-1"); pub.wait_for_n8n_ready()
    print(json.dumps({"workflow_id": WORKFLOW_ID, "version_id": version, "backup": str(backup)}, ensure_ascii=False))

if __name__ == "__main__": main()
