"""Publish public website advisor API webhook wired into structured runtime."""

from __future__ import annotations

import importlib.util
import json
import textwrap
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
WORKFLOW_ID = "advisor-whieda-phase1"
PUBLIC_WEBHOOK_PATH = "wwc-advisor-public-v1"
CONTRACT_WEBHOOK_PATH = "whieda-advisor-api-v1"
POSTGRES_CREDENTIAL = {"postgres": {"id": "RmjHh3rdZri7axzq", "name": "advisor-dev-postgres"}}

NORMALIZE_JS = r"""const raw = $input.first().json;
const body = raw.body && typeof raw.body === 'object' ? raw.body : raw;
const tenant = String(body.tenant || 'whieda').trim();
const question = String(body.question || '').trim();
const session = String(body.session_id || body.session || '').trim();
const ref = String(body.ref_context || body.ref || '').trim().toLowerCase();
const slug = String(body.product_context || body.slug || '').trim().toLowerCase();
const sku = String(body.sku || '').trim();
const pageUrl = String(body.page_url || '').trim();
const language = String(body.language || body.locale || 'ru').trim().toLowerCase();
const country = String(body.country || 'BY').trim().toUpperCase();
const requestId = `api-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
if (!question || !session) {
  return [{ json: {
    api_error: true,
    ok: false,
    answer_text: 'Нужны поля question и session.',
    answer_mode: 'error',
    route: 'error',
    error_id: requestId,
  } }];
}
const sessionKey = session.replace(/[^a-zA-Z0-9:_-]/g, '').slice(0, 120);
let hash = 0;
for (let i = 0; i < sessionKey.length; i++) hash = ((hash * 31) + sessionKey.charCodeAt(i)) >>> 0;
const chatId = String(2000000000 + (hash % 700000000));
return [{ json: {
  project_id: tenant,
  channel: 'website_api',
  surface: String(body.surface || 'website'),
  request_id: requestId,
  message_text: question,
  user_message_text: question,
  external_chat_id: chatId,
  external_user_id: chatId,
  external_username: null,
  display_name: 'Website visitor',
  trusted_reviewer: false,
  is_website_api: true,
  website_session: sessionKey,
  website_ref: ref,
  website_slug: slug,
  website_sku: sku,
  website_page_url: pageUrl,
  website_language: language,
  website_country: country,
  idempotency_key: `website:${tenant}:${sessionKey}:${requestId}`,
  raw_payload: { body, whieda_public_api: true },
} }];"""

UPSERT_SQL = """WITH params AS (
  SELECT
    '{{ String($json.project_id || 'whieda').replace(/'/g, '') }}'::text AS client_id,
    '{{ String($json.website_session || '').replace(/'/g, '') }}'::text AS session_key,
    '{{ String($json.website_ref || '').replace(/'/g, '') }}'::text AS ref_code,
    '{{ String($json.website_slug || '').replace(/'/g, '') }}'::text AS slug,
    '{{ String($json.external_chat_id || '').replace(/'/g, '') }}'::text AS thread_id
), surface_id AS (
  SELECT (p.client_id || ':website:' || p.session_key) AS surface_user_id, p.*
  FROM params p
), existing AS (
  SELECT sa.user_id
  FROM advisor_surface_accounts sa
  JOIN surface_id s ON sa.surface = 'website' AND sa.surface_user_id = s.surface_user_id
  LIMIT 1
), ins_user AS (
  INSERT INTO advisor_users (client_id, display_name)
  SELECT s.client_id, 'Website visitor'
  FROM surface_id s
  WHERE NOT EXISTS (SELECT 1 FROM existing)
  RETURNING user_id
), uid AS (
  SELECT user_id FROM existing
  UNION ALL
  SELECT user_id FROM ins_user
  LIMIT 1
), upsert_sa AS (
  INSERT INTO advisor_surface_accounts (user_id, surface, surface_user_id)
  SELECT user_id, 'website', s.surface_user_id
  FROM uid, surface_id s
  ON CONFLICT (surface, surface_user_id, COALESCE(surface_chat_id, ''))
  DO UPDATE SET updated_at = NOW()
  RETURNING user_id
), conv AS (
  INSERT INTO advisor_conversations (user_id, surface, surface_thread_id)
  SELECT user_id, 'website', s.thread_id
  FROM uid, surface_id s
  ON CONFLICT (user_id, surface, surface_thread_id)
  DO UPDATE SET updated_at = NOW()
  RETURNING conversation_id::text AS conversation_id
), ctx AS (
  INSERT INTO advisor_conversation_context (client_id, conversation_id, context, updated_at)
  SELECT
    s.client_id,
    c.conversation_id::uuid,
    jsonb_strip_nulls(jsonb_build_object(
      'first_ref', NULLIF(s.ref_code, ''),
      'active_ref', NULLIF(s.ref_code, ''),
      'last_product_slug', NULLIF(s.slug, ''),
      'surface', 'website'
    )),
    NOW()
  FROM conv c
  CROSS JOIN surface_id s
  ON CONFLICT (client_id, conversation_id)
  DO UPDATE SET
    context = advisor_conversation_context.context
      || jsonb_strip_nulls(jsonb_build_object(
        'active_ref', NULLIF(s.ref_code, ''),
        'last_product_slug', NULLIF(s.slug, ''),
        'surface', 'website'
      ))
      || CASE
        WHEN coalesce(advisor_conversation_context.context->>'first_ref', '') = ''
        THEN jsonb_strip_nulls(jsonb_build_object('first_ref', NULLIF(s.ref_code, '')))
        ELSE '{}'::jsonb
      END,
    updated_at = NOW()
  RETURNING conversation_id::text AS conversation_id
)
SELECT
  (SELECT user_id::text FROM uid LIMIT 1) AS user_id,
  COALESCE((SELECT conversation_id FROM ctx), (SELECT conversation_id FROM conv)) AS conversation_id;"""

MERGE_JS = r"""const envelope = $('Code: Normalize Website API').first().json;
const row = $input.first()?.json || {};
if (envelope.api_error) return [{ json: envelope }];
return [{ json: {
  ...envelope,
  user_id: row.user_id || '',
  conversation_id: row.conversation_id || '',
  user_message_text: envelope.message_text,
} }];"""

FORMAT_JS = r"""const source = $input.first()?.json || {};
if (source.api_error) {
  return [{ json: {
    ok: false,
    answer_text: source.answer_text || 'Не удалось обработать запрос.',
    answer_mode: 'error',
    route: 'error',
    product: null,
    media: { photo_url: null, videos: [], documents: [] },
    clarifications: [],
    sources: [],
    context: {},
    error_id: source.error_id || source.request_id || null,
    text: source.answer_text || 'Не удалось обработать запрос.',
    message: source.answer_text || 'Не удалось обработать запрос.',
  } }];
}
const answerText = String(source.reply_text || source.answer_text || source.telegram_text || '').trim();
const sku = source.structured_match?.sku || source.product_sku || null;
const slug = $('Code: Normalize Website API').first().json.website_slug || source.structured_match?.slug || null;
const photo = source.telegram_photo_url || source.photo_url || null;
const docs = [];
const videos = [];
if (Array.isArray(source.resources)) {
  for (const item of source.resources) {
    const type = String(item.resource_type || item.type || '').toLowerCase();
    if (type.includes('video')) videos.push(item);
    else docs.push(item);
  }
}
const ok = Boolean(answerText);
return [{ json: {
  ok,
  answer_text: answerText || 'Сейчас не удалось обработать запрос. Попробуйте ещё раз.',
  answer_mode: source.answer_mode || (source.structured_hit ? 'structured_card' : 'fallback'),
  route: source.route || (source.structured_hit ? 'structured' : 'fallback'),
  product: sku || slug ? { sku, slug, canonical_name: source.structured_match?.canonical_name || source.product_name || null, name: source.structured_match?.canonical_name || source.product_name || null } : null,
  media: { photo_url: photo, videos, documents: docs },
  clarifications: source.clarifications || source.followup_questions || [],
  sources: source.sources || [],
  context: { last_product_sku: sku, last_product_slug: slug },
  error_id: ok ? null : ($('Code: Normalize Website API').first().json.request_id || null),
  text: answerText,
  message: answerText,
  resources: docs,
} }];"""


def load_helper():
    path = BASE / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_sync", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def add_node(workflow, node):
    names = {item["name"] for item in workflow["nodes"]}
    if node["name"] not in names:
        workflow["nodes"].append(node)


def main() -> None:
    helper = load_helper()
    session = helper.login_session()
    response = session.get(f"{helper.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60)
    response.raise_for_status()
    workflow = response.json()["data"]

    backup_dir = BASE.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"advisor-whieda-before-website-api-{datetime.now():%Y%m%d-%H%M%S}.json"
    backup_path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8")

    nodes = {node["name"]: node for node in workflow["nodes"]}
    merge_pos = nodes["Code: Merge Envelope + Session"]["position"]
    load_pos = nodes["Postgres: Load Conversation Context"]["position"]

    add_node(
        workflow,
        {
            "id": "website-api-webhook-public",
            "name": "Webhook: Website API public",
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 2,
            "position": [merge_pos[0] - 820, merge_pos[1] - 260],
            "parameters": {
                "httpMethod": "POST",
                "path": PUBLIC_WEBHOOK_PATH,
                "responseMode": "responseNode",
                "options": {},
            },
        },
    )
    add_node(
        workflow,
        {
            "id": "website-api-webhook-contract",
            "name": "Webhook: Website API contract",
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 2,
            "position": [merge_pos[0] - 820, merge_pos[1] - 120],
            "parameters": {
                "httpMethod": "POST",
                "path": CONTRACT_WEBHOOK_PATH,
                "responseMode": "responseNode",
                "options": {},
            },
        },
    )
    add_node(
        workflow,
        {
            "id": "website-api-normalize",
            "name": "Code: Normalize Website API",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [merge_pos[0] - 560, merge_pos[1] - 190],
            "parameters": {"jsCode": NORMALIZE_JS},
        },
    )
    add_node(
        workflow,
        {
            "id": "website-api-if-error",
            "name": "IF: Website API error?",
            "type": "n8n-nodes-base.if",
            "typeVersion": 2.2,
            "position": [merge_pos[0] - 360, merge_pos[1] - 190],
            "parameters": {
                "conditions": {
                    "options": {"version": 2, "typeValidation": "strict"},
                    "combinator": "and",
                    "conditions": [
                        {
                            "leftValue": "={{ $json.api_error }}",
                            "rightValue": True,
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                        }
                    ],
                }
            },
        },
    )
    add_node(
        workflow,
        {
            "id": "website-api-upsert",
            "name": "Postgres: Upsert Website Session",
            "type": "n8n-nodes-base.postgres",
            "typeVersion": 2.6,
            "position": [merge_pos[0] - 160, merge_pos[1] - 120],
            "credentials": POSTGRES_CREDENTIAL,
            "parameters": {"operation": "executeQuery", "query": UPSERT_SQL, "options": {}},
        },
    )
    add_node(
        workflow,
        {
            "id": "website-api-merge",
            "name": "Code: Merge Website API Session",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [load_pos[0] - 180, load_pos[1] - 120],
            "parameters": {"jsCode": MERGE_JS},
        },
    )
    add_node(
        workflow,
        {
            "id": "website-api-format",
            "name": "Code: Format Website API Response",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [nodes["Code: Validate Dify Response"]["position"][0] + 220, nodes["Code: Validate Dify Response"]["position"][1] + 180],
            "parameters": {"jsCode": FORMAT_JS},
        },
    )
    add_node(
        workflow,
        {
            "id": "website-api-respond",
            "name": "Respond: Website API JSON",
            "type": "n8n-nodes-base.respondToWebhook",
            "typeVersion": 1.1,
            "position": [nodes["Code: Validate Dify Response"]["position"][0] + 460, nodes["Code: Validate Dify Response"]["position"][1] + 180],
            "parameters": {
                "respondWith": "json",
                "responseBody": "={{ $json }}",
                "options": {"responseCode": 200},
            },
        },
    )
    add_node(
        workflow,
        {
            "id": "website-api-if-request",
            "name": "IF: Website API request?",
            "type": "n8n-nodes-base.if",
            "typeVersion": 2.2,
            "position": [nodes["Code: Validate Dify Response"]["position"][0] + 20, nodes["Code: Validate Dify Response"]["position"][1] + 80],
            "parameters": {
                "conditions": {
                    "options": {"version": 2, "typeValidation": "strict"},
                    "combinator": "and",
                    "conditions": [
                        {
                            "leftValue": "={{ $('Code: Normalize Website API').isExecuted }}",
                            "rightValue": True,
                            "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                        }
                    ],
                }
            },
        },
    )

    con = workflow["connections"]
    con["Webhook: Website API public"] = {"main": [[{"node": "Code: Normalize Website API", "type": "main", "index": 0}]]}
    con["Webhook: Website API contract"] = {"main": [[{"node": "Code: Normalize Website API", "type": "main", "index": 0}]]}
    con["Code: Normalize Website API"] = {"main": [[{"node": "IF: Website API error?", "type": "main", "index": 0}]]}
    con["IF: Website API error?"] = {
        "main": [
            [{"node": "Code: Format Website API Response", "type": "main", "index": 0}],
            [{"node": "Postgres: Upsert Website Session", "type": "main", "index": 0}],
        ]
    }
    con["Postgres: Upsert Website Session"] = {"main": [[{"node": "Code: Merge Website API Session", "type": "main", "index": 0}]]}
    con["Code: Merge Website API Session"] = {"main": [[{"node": "Postgres: Load Conversation Context", "type": "main", "index": 0}]]}

    validate_name = "Code: Validate Dify Response"
  # Insert website branch after validate node
    downstream = con.get(validate_name, {}).get("main", [[]])[0]
    con[validate_name] = {"main": [[{"node": "IF: Website API request?", "type": "main", "index": 0}]]}
    con["IF: Website API request?"] = {
        "main": [
            [{"node": "Code: Format Website API Response", "type": "main", "index": 0}],
            downstream or [{"node": "IF: Structured Hit?", "type": "main", "index": 0}],
        ]
    }
    con["Code: Format Website API Response"] = {"main": [[{"node": "Respond: Website API JSON", "type": "main", "index": 0}]]}

    saved = session.patch(f"{helper.BASE_URL}/rest/workflows/{WORKFLOW_ID}", json=workflow, verify=False, timeout=180)
    saved.raise_for_status()
    version = saved.json().get("data", saved.json()).get("versionId")
    activate = session.post(
        f"{helper.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate",
        json={"versionId": version},
        verify=False,
        timeout=60,
    )
    if activate.status_code == 409:
        fresh = session.get(f"{helper.BASE_URL}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=60).json()["data"]
        version = fresh.get("versionId")
        activate = session.post(
            f"{helper.BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate",
            json={"versionId": version},
            verify=False,
            timeout=60,
        )
    if activate.status_code not in (200, 409):
        activate.raise_for_status()
    helper.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    helper.ssh_run("docker restart n8n-n8n-1")
    helper.wait_for_n8n_ready()
    print(
        json.dumps(
            {
                "workflow_id": WORKFLOW_ID,
                "version_id": version,
                "backup": str(backup_path),
                "webhooks": [PUBLIC_WEBHOOK_PATH, CONTRACT_WEBHOOK_PATH],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
