import json
import json
import os
import time
from datetime import datetime
from pathlib import Path

import paramiko
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://sysarchn8n.duckdns.org"
EMAIL = os.environ.get("WHIEDA_N8N_EMAIL", "")
PASSWORD = os.environ.get("WHIEDA_N8N_PASSWORD", "")
WORKFLOW_NAME = "WHIEDA Structured Sync Cron"
WEBHOOK_PATH = "whieda-structured-sync-v1"
WORKFLOW_CREDENTIAL = {
    "id": "RmjHh3rdZri7axzq",
    "name": "advisor-dev-postgres",
}

SYNC_AUDIT_QUERY = """CREATE TABLE IF NOT EXISTS advisor_structured_sync_runs (
  run_id bigserial PRIMARY KEY,
  project_id text NOT NULL,
  started_at timestamptz NOT NULL,
  finished_at timestamptz NOT NULL,
  status text NOT NULL,
  rows_products integer NOT NULL DEFAULT 0,
  rows_aliases integer NOT NULL DEFAULT 0,
  rows_resources integer NOT NULL DEFAULT 0,
  rows_product_cards integer NOT NULL DEFAULT 0,
  rows_product_details integer NOT NULL DEFAULT 0,
  rows_product_comparisons integer NOT NULL DEFAULT 0,
  rows_users_access integer NOT NULL DEFAULT 0,
  rows_structure_owners integer NOT NULL DEFAULT 0,
  error_text text
);

INSERT INTO advisor_structured_sync_runs (
  project_id, started_at, finished_at, status,
  rows_products, rows_aliases, rows_resources, rows_product_cards,
  rows_product_details, rows_product_comparisons, rows_users_access, rows_structure_owners
)
VALUES (
  '{{ String($json.project_id || 'whieda').replace(/'/g, '') }}',
  '{{ String($json.sync_started_at || new Date().toISOString()).replace(/'/g, '') }}'::timestamptz,
  now(),
  'success',
  {{ Number($json.rows_products || 0) }},
  {{ Number($json.rows_aliases || 0) }},
  {{ Number($json.rows_resources || 0) }},
  {{ Number($json.rows_product_cards || 0) }},
  {{ Number($json.rows_product_details || 0) }},
  {{ Number($json.rows_product_comparisons || 0) }},
  {{ Number($json.rows_users_access || 0) }},
  {{ Number($json.rows_structure_owners || 0) }}
)
RETURNING run_id, project_id, status, finished_at;"""
SERVER_HOST = "185.252.232.93"
SERVER_USER = "root"
SERVER_PASSWORD = os.environ.get("WHIEDA_SSH_PASSWORD", "")


def build_workflow(code: str) -> dict:
    return {
        "name": WORKFLOW_NAME,
        "active": True,
        "nodes": [
            {
                "parameters": {
                    "httpMethod": "POST",
                    "path": WEBHOOK_PATH,
                    "responseMode": "responseNode",
                    "options": {},
                },
                "id": "whieda-sync-webhook",
                "name": "Webhook Trigger",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 1,
                "position": [-720, -160],
            },
            {
                "parameters": {
                    "respondWith": "json",
                    "responseBody": '{ "status": "accepted" }',
                    "options": {"responseCode": 200},
                },
                "id": "whieda-sync-ack",
                "name": "Respond: 200 ACK",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1,
                "position": [-512, -320],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=1035748906",
                    "options": {},
                },
                "id": "whieda-sync-http-products",
                "name": "HTTP: Products TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [-272, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=2001001",
                    "options": {},
                },
                "id": "whieda-sync-http-aliases",
                "name": "HTTP: Aliases TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [-32, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=2001005",
                    "options": {},
                },
                "id": "whieda-sync-http-resources",
                "name": "HTTP: Resources TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [208, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=2001006",
                    "options": {},
                },
                "id": "whieda-sync-http-cards",
                "name": "HTTP: Product Cards TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [448, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=2001008",
                    "options": {},
                },
                "id": "whieda-sync-http-product-details",
                "name": "HTTP: Product Details TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [672, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=1415928637",
                    "options": {},
                },
                "id": "whieda-sync-http-product-comparisons",
                "name": "HTTP: Product Comparisons TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [896, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=161104189",
                    "options": {},
                },
                "id": "whieda-sync-http-users-access",
                "name": "HTTP: Users Access TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [1120, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=86214234",
                    "options": {},
                },
                "id": "whieda-sync-http-structure-owners",
                "name": "HTTP: Structure Owners TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [1344, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=43717728",
                    "options": {},
                },
                "id": "whieda-sync-http-business-objections",
                "name": "HTTP: Business Objections TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [1568, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=28151102",
                    "options": {},
                },
                "id": "whieda-sync-http-business-faq",
                "name": "HTTP: Business FAQ TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [1792, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=1621722235",
                    "options": {},
                },
                "id": "whieda-sync-http-promotions",
                "name": "HTTP: Promotions TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [2016, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=1468974041",
                    "options": {},
                },
                "id": "whieda-sync-http-recommendation-rules",
                "name": "HTTP: Product Recommendation Rules TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [2240, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=1974396045",
                    "options": {},
                },
                "id": "whieda-sync-http-starter-basket-templates",
                "name": "HTTP: Starter Basket Templates TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [2464, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=1444298798",
                    "options": {},
                },
                "id": "whieda-sync-http-events",
                "name": "HTTP: Events TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [2688, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=947236678",
                    "options": {},
                },
                "id": "whieda-sync-http-community-resources",
                "name": "HTTP: Community Resources TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [2912, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=2001020",
                    "options": {},
                },
                "id": "whieda-sync-http-intent-registry",
                "name": "HTTP: Intent Registry TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [2016, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=2001021",
                    "options": {},
                },
                "id": "whieda-sync-http-clarification-prompts",
                "name": "HTTP: Clarification Prompts TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [2240, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=2001022",
                    "options": {},
                },
                "id": "whieda-sync-http-capability-responses",
                "name": "HTTP: Capability Responses TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [2464, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=1160261466",
                    "options": {},
                },
                "id": "whieda-sync-http-canonical-questions",
                "name": "HTTP: Canonical Questions TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [2688, -144],
            },
            {
                "parameters": {
                    "method": "GET",
                    "url": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=1733124410",
                    "options": {},
                },
                "id": "whieda-sync-http-partners-ref",
                "name": "HTTP: Partners Ref TSV",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.4,
                "position": [2912, -144],
            },
            {
                "parameters": {"jsCode": code},
                "id": "whieda-sync-code",
                "name": "Code: Build Structured Sync SQL",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [1792, -144],
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $json.query_products }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-products",
                "name": "Postgres: Sync Products",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [1136, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_aliases }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-aliases",
                "name": "Postgres: Sync Aliases",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [1360, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_resources }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-resources",
                "name": "Postgres: Sync Resources",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [1584, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_product_cards }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-cards",
                "name": "Postgres: Sync Product Cards",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [1808, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_product_details }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-details",
                "name": "Postgres: Sync Product Details",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [2032, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_product_comparisons }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-comparisons",
                "name": "Postgres: Sync Product Comparisons",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [2704, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_users_access }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-users-access",
                "name": "Postgres: Sync Users Access",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [2928, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_structure_owners }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-structure-owners",
                "name": "Postgres: Sync Structure Owners",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [3152, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_business_objections }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-business-objections",
                "name": "Postgres: Sync Business Objections",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [3376, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_business_faq }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-business-faq",
                "name": "Postgres: Sync Business FAQ",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [3600, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_promotions }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-promotions",
                "name": "Postgres: Sync Promotions",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [3824, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_recommendation_rules }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-recommendation-rules",
                "name": "Postgres: Sync Product Recommendation Rules",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [4048, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_starter_basket_templates }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-starter-basket-templates",
                "name": "Postgres: Sync Starter Basket Templates",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [4272, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_events }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-events",
                "name": "Postgres: Sync Events",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [4496, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_community_resources }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-community-resources",
                "name": "Postgres: Sync Community Resources",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [4720, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_intent_registry }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-intent-registry",
                "name": "Postgres: Sync Intent Registry",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [3824, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_clarification_prompts }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-clarification-prompts",
                "name": "Postgres: Sync Clarification Prompts",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [4048, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_capability_responses }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-capability-responses",
                "name": "Postgres: Sync Capability Responses",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [4272, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_canonical_questions }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-canonical-questions",
                "name": "Postgres: Sync Canonical Questions",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [4496, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_partners_runtime }}',
                    "options": {},
                },
                "id": "whieda-sync-postgres-partners-runtime",
                "name": "Postgres: Sync Partners Runtime",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [4720, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "jsCode": """const source = $('Code: Build Structured Sync SQL').first().json;
return [{
  json: {
    status: 'ok',
    project_id: source.project_id,
    rows_products: source.rows_products,
    rows_aliases: source.rows_aliases,
    rows_resources: source.rows_resources,
    rows_product_cards: source.rows_product_cards,
    rows_product_details: source.rows_product_details,
    rows_product_comparisons: source.rows_product_comparisons,
    rows_users_access: source.rows_users_access,
    rows_structure_owners: source.rows_structure_owners,
    rows_business_objections: source.rows_business_objections,
    rows_business_faq: source.rows_business_faq,
    rows_promotions: source.rows_promotions,
    rows_recommendation_rules: source.rows_recommendation_rules,
    rows_starter_basket_templates: source.rows_starter_basket_templates,
    rows_intent_registry: source.rows_intent_registry,
    rows_clarification_prompts: source.rows_clarification_prompts,
    rows_capability_responses: source.rows_capability_responses,
    rows_canonical_questions: source.rows_canonical_questions,
    rows_partners_ref: source.rows_partners_ref,
    sync_started_at: source.sync_started_at,
    synced_at: new Date().toISOString(),
  },
}];"""
                },
                "id": "whieda-sync-summary",
                "name": "Code: Sync Summary",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [3600, -144],
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": SYNC_AUDIT_QUERY,
                    "options": {},
                },
                "id": "whieda-sync-audit",
                "name": "Postgres: Write Sync Audit",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [3824, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
        ],
        "connections": {
            "Webhook Trigger": {"main": [[{"node": "Respond: 200 ACK", "type": "main", "index": 0}]]},
            "Respond: 200 ACK": {"main": [[{"node": "HTTP: Products TSV", "type": "main", "index": 0}]]},
            "HTTP: Products TSV": {"main": [[{"node": "HTTP: Aliases TSV", "type": "main", "index": 0}]]},
            "HTTP: Aliases TSV": {"main": [[{"node": "HTTP: Resources TSV", "type": "main", "index": 0}]]},
            "HTTP: Resources TSV": {"main": [[{"node": "HTTP: Product Cards TSV", "type": "main", "index": 0}]]},
            "HTTP: Product Cards TSV": {"main": [[{"node": "HTTP: Product Details TSV", "type": "main", "index": 0}]]},
            "HTTP: Product Details TSV": {"main": [[{"node": "HTTP: Product Comparisons TSV", "type": "main", "index": 0}]]},
            "HTTP: Product Comparisons TSV": {"main": [[{"node": "HTTP: Users Access TSV", "type": "main", "index": 0}]]},
            "HTTP: Users Access TSV": {"main": [[{"node": "HTTP: Structure Owners TSV", "type": "main", "index": 0}]]},
            "HTTP: Structure Owners TSV": {"main": [[{"node": "HTTP: Business Objections TSV", "type": "main", "index": 0}]]},
            "HTTP: Business Objections TSV": {"main": [[{"node": "HTTP: Business FAQ TSV", "type": "main", "index": 0}]]},
            "HTTP: Business FAQ TSV": {"main": [[{"node": "HTTP: Promotions TSV", "type": "main", "index": 0}]]},
            "HTTP: Promotions TSV": {"main": [[{"node": "HTTP: Product Recommendation Rules TSV", "type": "main", "index": 0}]]},
            "HTTP: Product Recommendation Rules TSV": {"main": [[{"node": "HTTP: Starter Basket Templates TSV", "type": "main", "index": 0}]]},
            "HTTP: Starter Basket Templates TSV": {"main": [[{"node": "HTTP: Events TSV", "type": "main", "index": 0}]]},
            "HTTP: Events TSV": {"main": [[{"node": "HTTP: Community Resources TSV", "type": "main", "index": 0}]]},
            "HTTP: Community Resources TSV": {"main": [[{"node": "HTTP: Intent Registry TSV", "type": "main", "index": 0}]]},
            "HTTP: Intent Registry TSV": {"main": [[{"node": "HTTP: Clarification Prompts TSV", "type": "main", "index": 0}]]},
            "HTTP: Clarification Prompts TSV": {"main": [[{"node": "HTTP: Capability Responses TSV", "type": "main", "index": 0}]]},
            "HTTP: Capability Responses TSV": {"main": [[{"node": "HTTP: Canonical Questions TSV", "type": "main", "index": 0}]]},
            "HTTP: Canonical Questions TSV": {"main": [[{"node": "HTTP: Partners Ref TSV", "type": "main", "index": 0}]]},
            "HTTP: Partners Ref TSV": {"main": [[{"node": "Code: Build Structured Sync SQL", "type": "main", "index": 0}]]},
            "Code: Build Structured Sync SQL": {"main": [[{"node": "Postgres: Sync Products", "type": "main", "index": 0}]]},
            "Postgres: Sync Products": {"main": [[{"node": "Postgres: Sync Aliases", "type": "main", "index": 0}]]},
            "Postgres: Sync Aliases": {"main": [[{"node": "Postgres: Sync Resources", "type": "main", "index": 0}]]},
            "Postgres: Sync Resources": {"main": [[{"node": "Postgres: Sync Product Cards", "type": "main", "index": 0}]]},
            "Postgres: Sync Product Cards": {"main": [[{"node": "Postgres: Sync Product Details", "type": "main", "index": 0}]]},
            "Postgres: Sync Product Details": {"main": [[{"node": "Postgres: Sync Product Comparisons", "type": "main", "index": 0}]]},
            "Postgres: Sync Product Comparisons": {"main": [[{"node": "Postgres: Sync Users Access", "type": "main", "index": 0}]]},
            "Postgres: Sync Users Access": {"main": [[{"node": "Postgres: Sync Structure Owners", "type": "main", "index": 0}]]},
            "Postgres: Sync Structure Owners": {"main": [[{"node": "Postgres: Sync Business Objections", "type": "main", "index": 0}]]},
            "Postgres: Sync Business Objections": {"main": [[{"node": "Postgres: Sync Business FAQ", "type": "main", "index": 0}]]},
            "Postgres: Sync Business FAQ": {"main": [[{"node": "Postgres: Sync Promotions", "type": "main", "index": 0}]]},
            "Postgres: Sync Promotions": {"main": [[{"node": "Postgres: Sync Product Recommendation Rules", "type": "main", "index": 0}]]},
            "Postgres: Sync Product Recommendation Rules": {"main": [[{"node": "Postgres: Sync Starter Basket Templates", "type": "main", "index": 0}]]},
            "Postgres: Sync Starter Basket Templates": {"main": [[{"node": "Postgres: Sync Events", "type": "main", "index": 0}]]},
            "Postgres: Sync Events": {"main": [[{"node": "Postgres: Sync Community Resources", "type": "main", "index": 0}]]},
            "Postgres: Sync Community Resources": {"main": [[{"node": "Postgres: Sync Intent Registry", "type": "main", "index": 0}]]},
            "Postgres: Sync Intent Registry": {"main": [[{"node": "Postgres: Sync Clarification Prompts", "type": "main", "index": 0}]]},
            "Postgres: Sync Clarification Prompts": {"main": [[{"node": "Postgres: Sync Capability Responses", "type": "main", "index": 0}]]},
            "Postgres: Sync Capability Responses": {"main": [[{"node": "Postgres: Sync Canonical Questions", "type": "main", "index": 0}]]},
            "Postgres: Sync Canonical Questions": {"main": [[{"node": "Postgres: Sync Partners Runtime", "type": "main", "index": 0}]]},
            "Postgres: Sync Partners Runtime": {"main": [[{"node": "Code: Sync Summary", "type": "main", "index": 0}]]},
            "Code: Sync Summary": {"main": [[{"node": "Postgres: Write Sync Audit", "type": "main", "index": 0}]]},
        },
        "settings": {
            "executionOrder": "v1",
            "saveManualExecutions": True,
            "saveExecutionProgress": True,
        },
    }


def ssh_run(command: str) -> str:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        SERVER_HOST,
        username=SERVER_USER,
        password=SERVER_PASSWORD,
        look_for_keys=False,
        allow_agent=False,
        timeout=30,
    )
    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=180)
        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if exit_code != 0:
            raise RuntimeError(f"SSH command failed: {command}\nSTDOUT:\n{out}\nSTDERR:\n{err}")
        return out.strip() or err.strip()
    finally:
        client.close()


def wait_for_n8n_ready(timeout_seconds: int = 90) -> None:
    """Wait for the public API after this script restarts the n8n container."""
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        try:
            response = requests.get(f"{BASE_URL}/healthz", verify=False, timeout=10)
            if response.ok:
                return
            last_error = f"healthz={response.status_code}"
        except requests.RequestException as error:
            last_error = str(error)
        time.sleep(3)
    raise RuntimeError(f"n8n did not become ready after restart: {last_error}")


def login_session(timeout_seconds: int = 90) -> requests.Session:
    """Wait for the actual n8n REST API, not only the container health endpoint."""
    deadline = time.time() + timeout_seconds
    last_error = None
    while time.time() < deadline:
        session = requests.Session()
        try:
            response = session.post(
                f"{BASE_URL}/rest/login",
                json={"emailOrLdapLoginId": EMAIL, "password": PASSWORD},
                verify=False,
                timeout=15,
            )
            if response.ok:
                return session
            last_error = f"login={response.status_code}"
        except requests.RequestException as error:
            last_error = str(error)
        time.sleep(3)
    raise RuntimeError(f"n8n REST API did not become ready after restart: {last_error}")


def main() -> None:
    required = {
        "WHIEDA_N8N_EMAIL": EMAIL,
        "WHIEDA_N8N_PASSWORD": PASSWORD,
        "WHIEDA_SSH_PASSWORD": SERVER_PASSWORD,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))

    code = Path(__file__).with_name("whieda_structured_sync_code_2026-07-13.js").read_text(encoding="utf-8")
    workflow = build_workflow(code)

    session = login_session()

    workflows = session.get(f"{BASE_URL}/rest/workflows?limit=200", verify=False, timeout=30)
    workflows.raise_for_status()
    items = workflows.json().get("data", [])
    found = next((item for item in items if item.get("name") == WORKFLOW_NAME), None)

    if found:
        current = session.get(
            f"{BASE_URL}/rest/workflows/{found['id']}",
            verify=False,
            timeout=30,
        )
        current.raise_for_status()
        backup_dir = Path(__file__).resolve().parents[1] / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / ("whieda-structured-sync-before-audit-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
        backup_path.write_text(json.dumps(current.json()["data"], ensure_ascii=False, indent=2), encoding="utf-8")
        workflow["id"] = found["id"]
        save = session.patch(
            f"{BASE_URL}/rest/workflows/{found['id']}",
            json=workflow,
            verify=False,
            timeout=60,
        )
        save.raise_for_status()
        workflow_id = found["id"]
    else:
        create = session.post(
            f"{BASE_URL}/rest/workflows",
            json=workflow,
            verify=False,
            timeout=60,
        )
        create.raise_for_status()
        workflow_id = create.json()["id"]
        backup_path = None

    saved = save.json().get("data", save.json()) if found else create.json().get("data", create.json())
    version_id = saved.get("versionId")
    if not version_id:
        refreshed = session.get(f"{BASE_URL}/rest/workflows/{workflow_id}", verify=False, timeout=30)
        refreshed.raise_for_status()
        version_id = refreshed.json()["data"].get("versionId")
    if not version_id:
        raise RuntimeError("n8n did not return a workflow versionId for structured sync")

    activation = session.post(
        f"{BASE_URL}/rest/workflows/{workflow_id}/activate",
        json={"versionId": version_id},
        verify=False,
        timeout=60,
    )
    activation.raise_for_status()

    ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={workflow_id}")
    ssh_run("docker restart n8n-n8n-1")
    wait_for_n8n_ready()

    # Restart invalidates the n8n browser session; log in again before inspection.
    session = login_session()

    if os.environ.get("WHIEDA_SKIP_SYNC_TRIGGER") == "1":
        print(json.dumps({"workflow_id": workflow_id, "version_id": version_id, "backup": str(backup_path) if backup_path else None, "sync_trigger": "skipped"}, ensure_ascii=False))
        return

    trigger = requests.post(f"{BASE_URL}/webhook/{WEBHOOK_PATH}", verify=False, timeout=60)
    trigger.raise_for_status()

    time.sleep(5)
    executions = session.get(
        f"{BASE_URL}/rest/executions?limit=5&workflowId={workflow_id}",
        verify=False,
        timeout=30,
    )
    executions.raise_for_status()

    execution_payload = executions.json().get("data", {})
    execution_rows = execution_payload.get("results", execution_payload if isinstance(execution_payload, list) else [])
    result = {
        "workflow_id": workflow_id,
        "version_id": version_id,
        "trigger_status": trigger.status_code,
        "trigger_body": trigger.text,
        "executions": [
            {
                "id": item.get("id"),
                "status": item.get("status"),
                "startedAt": item.get("startedAt"),
                "stoppedAt": item.get("stoppedAt"),
            }
            for item in execution_rows[:5]
        ],
        "backup_path": str(backup_path) if backup_path else None,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
