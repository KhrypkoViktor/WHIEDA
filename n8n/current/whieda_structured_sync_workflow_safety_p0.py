"""Safety P0 workflow builder for WHIEDA Structured Sync (local artifact only)."""

from __future__ import annotations

from whieda_structured_sync_safety_lib import (
    ERROR_WORKFLOW_ID_PLACEHOLDER,
    STRUCTURED_SYNC_WORKFLOW_ID,
    artifact_main_workflow_settings,
)

WORKFLOW_NAME = "WHIEDA Structured Sync Cron"
WEBHOOK_PATH = "whieda-structured-sync-v1"
WORKFLOW_CREDENTIAL = {
    "id": "RmjHh3rdZri7axzq",
    "name": "advisor-dev-postgres",
}

ERROR_WORKFLOW_NAME = "WHIEDA Structured Sync Error Audit"

ERROR_HANDLER_CODE = r"""const payload = $input.first()?.json || {};
const STRUCTURED_SYNC_WORKFLOW_ID = '""" + STRUCTURED_SYNC_WORKFLOW_ID + r"""';
const failedWorkflowId = payload.workflow?.id ? String(payload.workflow.id) : '';
const originalExecutionId = payload.execution?.id ? String(payload.execution.id) : null;
const errorWorkflowExecutionId = (typeof $execution !== 'undefined' && $execution && $execution.id)
  ? String($execution.id)
  : null;

if (failedWorkflowId !== STRUCTURED_SYNC_WORKFLOW_ID) {
  return [{
    json: {
      skip_failed_audit: true,
      reason: 'not_whieda_structured_sync',
      failed_workflow_id: failedWorkflowId,
      query_failed_audit: 'SELECT 1;',
    },
  }];
}

const rawError = payload.execution?.error?.message
  || payload.execution?.error?.description
  || payload.error?.message
  || payload.message
  || 'structured_sync_failed';

function redactSyncError(message) {
  if (message == null) return 'sync_error';
  let text = String(message).trim();
  const rules = [
    [/((?:password|passwd|pwd|token|api[_-]?key|secret|bearer)\s*[=:]\s*)\S+/gi, '$1[REDACTED]'],
    [/\bBearer\s+[A-Za-z0-9._\-+/=]{8,}\b/gi, 'Bearer [REDACTED]'],
    [/\bsk-[A-Za-z0-9]{16,}\b/gi, 'sk-[REDACTED]'],
    [/\b\d{8,12}\b/g, '[USER_ID_REDACTED]'],
    [/\b(?:INSERT|UPDATE|DELETE|SELECT)\s+(?:INTO|FROM|SET)?\s*[\w."']+/gi, '[SQL_REDACTED]'],
    [/https?:\/\/[^\s]+/gi, '[URL_REDACTED]'],
  ];
  for (const [pattern, replacement] of rules) {
    text = text.replace(pattern, replacement);
  }
  text = text.replace(/\s+/g, ' ').trim();
  if (text.length > 500) text = `${text.slice(0, 497)}...`;
  return text || 'sync_error';
}

function sqlValue(value) {
  if (value === null || value === undefined) return 'NULL';
  return `'${String(value).replace(/'/g, "''")}'`;
}

const summary = redactSyncError(rawError);
const execId = sqlValue(originalExecutionId);
const metadataUnmatched = `'{"unmatched_error_trigger": true}'`;

let queryFailedAudit;
if (!originalExecutionId) {
  queryFailedAudit = `INSERT INTO advisor_structured_sync_runs (
  project_id, started_at, finished_at, status, workflow_execution_id, error_summary, error_metadata
) VALUES ('whieda', now(), now(), 'failed', NULL, ${sqlValue(summary)}, ${metadataUnmatched}::jsonb)
RETURNING run_id, sync_run_uuid, workflow_execution_id, status, error_summary;`;
} else {
  queryFailedAudit = `WITH updated AS (
  UPDATE advisor_structured_sync_runs
  SET status = 'failed', finished_at = now(), error_summary = ${sqlValue(summary)}, error_metadata = NULL
  WHERE workflow_execution_id = ${execId} AND status = 'running'
  RETURNING run_id, sync_run_uuid, workflow_execution_id
)
INSERT INTO advisor_structured_sync_runs (
  project_id, started_at, finished_at, status, workflow_execution_id, error_summary, error_metadata
)
SELECT 'whieda', now(), now(), 'failed', ${execId}, ${sqlValue(summary)}, ${metadataUnmatched}::jsonb
WHERE NOT EXISTS (SELECT 1 FROM updated)
  AND NOT EXISTS (
    SELECT 1 FROM advisor_structured_sync_runs
    WHERE workflow_execution_id = ${execId}
      AND status IN ('running', 'failed')
  )
RETURNING run_id, sync_run_uuid, workflow_execution_id, status, error_summary;`;
}

return [{
  json: {
    skip_failed_audit: false,
    original_execution_id: originalExecutionId,
    error_workflow_execution_id: errorWorkflowExecutionId,
    failed_workflow_id: failedWorkflowId,
    query_failed_audit: queryFailedAudit,
    error_summary: summary,
  },
}];"""


def _http_tsv_node(node_id: str, name: str, gid: str, position: list[int]) -> dict:
    sheet_id = "1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4"
    return {
        "parameters": {
            "method": "GET",
            "url": f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=tsv&gid={gid}",
            "options": {},
        },
        "id": node_id,
        "name": name,
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.4,
        "position": position,
    }


def build_workflow_safety_p0(code: str, *, active: bool = False, error_workflow_id: str | None = None) -> dict:
    http_chain = [
        ("whieda-sync-http-products", "HTTP: Products TSV", "1035748906"),
        ("whieda-sync-http-aliases", "HTTP: Aliases TSV", "2001001"),
        ("whieda-sync-http-resources", "HTTP: Resources TSV", "2001005"),
        ("whieda-sync-http-cards", "HTTP: Product Cards TSV", "2001006"),
        ("whieda-sync-http-product-details", "HTTP: Product Details TSV", "2001008"),
        ("whieda-sync-http-solution-bundles", "HTTP: Solution Bundles TSV", "2001007"),
        ("whieda-sync-http-product-comparisons", "HTTP: Product Comparisons TSV", "1415928637"),
        ("whieda-sync-http-users-access", "HTTP: Users Access TSV", "161104189"),
        ("whieda-sync-http-structure-owners", "HTTP: Structure Owners TSV", "86214234"),
        ("whieda-sync-http-business-objections", "HTTP: Business Objections TSV", "43717728"),
        ("whieda-sync-http-business-faq", "HTTP: Business FAQ TSV", "28151102"),
        ("whieda-sync-http-promotions", "HTTP: Promotions TSV", "1621722235"),
        ("whieda-sync-http-recommendation-rules", "HTTP: Product Recommendation Rules TSV", "1468974041"),
        ("whieda-sync-http-starter-basket-templates", "HTTP: Starter Basket Templates TSV", "1974396045"),
        ("whieda-sync-http-events", "HTTP: Events TSV", "1444298798"),
        ("whieda-sync-http-community-resources", "HTTP: Community Resources TSV", "947236678"),
        ("whieda-sync-http-intent-registry", "HTTP: Intent Registry TSV", "2001020"),
        ("whieda-sync-http-clarification-prompts", "HTTP: Clarification Prompts TSV", "2001021"),
        ("whieda-sync-http-capability-responses", "HTTP: Capability Responses TSV", "2001022"),
        ("whieda-sync-http-canonical-questions", "HTTP: Canonical Questions TSV", "1160261466"),
        ("whieda-sync-http-partners-ref", "HTTP: Partners Ref TSV", "1733124410"),
    ]

    nodes: list[dict] = [
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
    ]

    x = -272
    for node_id, name, gid in http_chain:
        nodes.append(_http_tsv_node(node_id, name, gid, [x, -144]))
        x += 224

    nodes.extend(
        [
            {
                "parameters": {"jsCode": code},
                "id": "whieda-sync-code",
                "name": "Code: Build Structured Sync SQL",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [x, -144],
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": "={{ $json.query_record_running }}",
                    "options": {},
                },
                "id": "whieda-sync-record-running",
                "name": "Postgres: Record Running Sync",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [x + 224, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": '={{ $("Code: Build Structured Sync SQL").first().json.query_apply_all }}',
                    "options": {},
                },
                "id": "whieda-sync-apply-all",
                "name": "Postgres: Apply All Structured Layers",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [x + 448, -144],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
            {
                "parameters": {
                    "jsCode": """const source = $('Code: Build Structured Sync SQL').first().json;
return [{
  json: {
    status: 'ok',
    project_id: source.project_id,
    sync_run_uuid: source.sync_run_uuid,
    row_counts: source.row_counts,
    sync_started_at: source.sync_started_at,
    synced_at: new Date().toISOString(),
  },
}];""",
                },
                "id": "whieda-sync-summary",
                "name": "Code: Sync Summary",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [x + 672, -144],
            },
        ]
    )

    connections: dict = {
        "Webhook Trigger": {"main": [[{"node": "Respond: 200 ACK", "type": "main", "index": 0}]]},
        "Respond: 200 ACK": {"main": [[{"node": http_chain[0][1], "type": "main", "index": 0}]]},
    }
    for index in range(len(http_chain) - 1):
        connections[http_chain[index][1]] = {
            "main": [[{"node": http_chain[index + 1][1], "type": "main", "index": 0}]]
        }
    connections[http_chain[-1][1]] = {
        "main": [[{"node": "Code: Build Structured Sync SQL", "type": "main", "index": 0}]]
    }
    connections["Code: Build Structured Sync SQL"] = {
        "main": [[{"node": "Postgres: Record Running Sync", "type": "main", "index": 0}]]
    }
    connections["Postgres: Record Running Sync"] = {
        "main": [[{"node": "Postgres: Apply All Structured Layers", "type": "main", "index": 0}]]
    }
    connections["Postgres: Apply All Structured Layers"] = {
        "main": [[{"node": "Code: Sync Summary", "type": "main", "index": 0}]]
    }

    settings = artifact_main_workflow_settings()
    if error_workflow_id:
        settings = {
            **settings,
            "errorWorkflow": error_workflow_id,
        }

    return {
        "name": WORKFLOW_NAME,
        "active": active,
        "nodes": nodes,
        "connections": connections,
        "settings": settings,
    }


def build_error_audit_workflow(*, active: bool = False) -> dict:
    return {
        "name": ERROR_WORKFLOW_NAME,
        "active": active,
        "nodes": [
            {
                "parameters": {},
                "id": "whieda-sync-error-trigger",
                "name": "Error Trigger",
                "type": "n8n-nodes-base.errorTrigger",
                "typeVersion": 1,
                "position": [0, 0],
            },
            {
                "parameters": {"jsCode": ERROR_HANDLER_CODE},
                "id": "whieda-sync-error-code",
                "name": "Code: Build Failed Audit SQL",
                "type": "n8n-nodes-base.code",
                "typeVersion": 2,
                "position": [240, 0],
            },
            {
                "parameters": {
                    "conditions": {
                        "options": {"caseSensitive": True, "typeValidation": "strict"},
                        "combinator": "and",
                        "conditions": [
                            {
                                "id": "skip-failed-audit-false",
                                "operator": {"type": "boolean", "operation": "false"},
                                "leftValue": "={{ $json.skip_failed_audit }}",
                                "rightValue": True,
                            }
                        ],
                    },
                    "options": {},
                },
                "id": "whieda-sync-error-if",
                "name": "IF: WHIEDA Sync Failure?",
                "type": "n8n-nodes-base.if",
                "typeVersion": 2,
                "position": [480, 0],
            },
            {
                "parameters": {
                    "operation": "executeQuery",
                    "query": "={{ $json.query_failed_audit }}",
                    "options": {},
                },
                "id": "whieda-sync-error-postgres",
                "name": "Postgres: Write Failed Audit",
                "type": "n8n-nodes-base.postgres",
                "typeVersion": 2.6,
                "position": [720, 0],
                "credentials": {"postgres": WORKFLOW_CREDENTIAL},
            },
        ],
        "connections": {
            "Error Trigger": {"main": [[{"node": "Code: Build Failed Audit SQL", "type": "main", "index": 0}]]},
            "Code: Build Failed Audit SQL": {
                "main": [[{"node": "IF: WHIEDA Sync Failure?", "type": "main", "index": 0}]]
            },
            "IF: WHIEDA Sync Failure?": {
                "main": [[{"node": "Postgres: Write Failed Audit", "type": "main", "index": 0}]]
            },
        },
        "settings": {"executionOrder": "v1"},
    }
