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
EMAIL = "khrypko.viktar@gmail.com"
PASSWORD = "***REMOVED***"
WORKFLOW_ID = "advisor-whieda-phase1"
SERVER_HOST = "185.252.232.93"
SERVER_USER = "root"
SERVER_PASSWORD = "***REMOVED***"

STRUCTURED_SHEET_LOOKUP_NODE_ID = "whieda-structured-sheet-lookup"
STRUCTURED_RESOURCE_LOOKUP_NODE_ID = "whieda-structured-resource-lookup"
PRODUCT_DETAILS_NODE_ID = "whieda-product-details"
PRODUCT_COMPARISONS_NODE_ID = "whieda-product-comparisons"
BUSINESS_OBJECTIONS_NODE_ID = "whieda-business-objections"
BUSINESS_FAQ_NODE_ID = "whieda-business-faq"
BROADCAST_SNAPSHOT_NODE_ID = "whieda-broadcast-snapshot"
BROADCAST_COMMAND_NODE_ID = "whieda-broadcast-command"
BROADCAST_MERGE_NODE_ID = "whieda-broadcast-merge"
BROADCAST_DISPATCH_IF_NODE_ID = "whieda-broadcast-dispatch-if"
BROADCAST_DISPATCH_TRIGGER_NODE_ID = "whieda-broadcast-dispatch-trigger"
NEW_CANDIDATE_IF_NODE_ID = "whieda-new-candidate-if"
NEW_CANDIDATE_REGISTER_NODE_ID = "whieda-new-candidate-register"
NEW_CANDIDATE_ALERT_NODE_ID = "whieda-new-candidate-alert"
NEW_CANDIDATE_RESTORE_NODE_ID = "whieda-new-candidate-restore"
NEW_CANDIDATE_SHEET_ROW_NODE_ID = "whieda-new-candidate-sheet-row"
NEW_CANDIDATE_SHEET_APPEND_NODE_ID = "whieda-new-candidate-sheet-append"
ACCESS_LOOKUP_NODE_ID = "whieda-access-lookup"
ACCESS_APPROVED_IF_NODE_ID = "whieda-access-approved-if"
PENDING_ACCESS_REPLY_NODE_ID = "whieda-pending-access-reply"
VALIDATE_DIFY_RESPONSE_NODE_ID = "ad8660b4-cd9e-4ac3-812f-80ccb81e6d21"
REVIEW_QUEUE_SNAPSHOT_NODE_ID = "whieda-review-queue-snapshot"
TELEGRAM_SEND_ANSWER_NODE_ID = "ab89cd60-2996-4ed8-b967-66f4c142ce40"
AUDIT_ANSWER_NODE_ID = "6cd7b608-4fb1-4dfb-bfaf-08b6b25280c6"

PHOTO_CHECK_NODE_ID = "whieda-answer-photo-check-v1"
FOLLOWUP_TEXT_NODE_ID = "whieda-answer-followup-text-v1"

TEST_CHAT_ID = "688931415"
TEST_USERNAME = "SunRaySword"
TEST_FIRST_NAME = "Viktor"
TEST_TEXT = "что это активатор клеток"

USER_AND_CONVERSATION_QUERY = """CREATE TABLE IF NOT EXISTS advisor_telegram_subscriptions (
  client_id text NOT NULL,
  user_id uuid NOT NULL,
  private_chat_id text NOT NULL,
  telegram_user_id text NOT NULL,
  username text,
  display_name text,
  is_subscribed boolean NOT NULL DEFAULT false,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now(),
  blocked_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, user_id),
  UNIQUE (client_id, private_chat_id)
);

WITH
sa_lookup AS (
  SELECT u.user_id
  FROM advisor_surface_accounts sa
  JOIN advisor_users u ON u.user_id = sa.user_id
  WHERE sa.surface = 'telegram'
    AND sa.surface_user_id = '{{ String(($('Code: Normalize Payload').first().json.project_id ?? 'whieda') + ':' + ($('Code: Normalize Payload').first().json.external_user_id ?? '')).replace(/'/g, '') }}'
  LIMIT 1
),
ins_user AS (
  INSERT INTO advisor_users (client_id, display_name)
  SELECT '{{ String($('Code: Normalize Payload').first().json.project_id ?? 'whieda').replace(/'/g, '') }}', '{{ String($('Code: Normalize Payload').first().json.display_name ?? '').replace(/'/g, '') }}'
  WHERE NOT EXISTS (SELECT 1 FROM sa_lookup)
  RETURNING user_id
),
uid AS (
  SELECT user_id FROM sa_lookup
  UNION ALL
  SELECT user_id FROM ins_user
  LIMIT 1
),
upsert_sa AS (
  INSERT INTO advisor_surface_accounts (user_id, surface, surface_user_id)
  SELECT user_id, 'telegram', '{{ String(($('Code: Normalize Payload').first().json.project_id ?? 'whieda') + ':' + ($('Code: Normalize Payload').first().json.external_user_id ?? '')).replace(/'/g, '') }}'
  FROM uid
  ON CONFLICT (surface, surface_user_id, COALESCE(surface_chat_id, ''))
  DO UPDATE SET updated_at = NOW()
  RETURNING user_id
),
upsert_conv AS (
  INSERT INTO advisor_conversations (user_id, surface, surface_thread_id)
  SELECT user_id, 'telegram', '{{ String($('Code: Normalize Payload').first().json.external_chat_id ?? '').replace(/'/g, '') }}'
  FROM uid
  ON CONFLICT (surface, surface_thread_id, user_id)
  DO UPDATE SET updated_at = NOW()
  RETURNING conversation_id
),
upsert_subscription AS (
  INSERT INTO advisor_telegram_subscriptions (
    client_id, user_id, private_chat_id, telegram_user_id, username, display_name, is_subscribed, first_seen_at, last_seen_at, updated_at
  )
  SELECT
    '{{ String($('Code: Normalize Payload').first().json.project_id ?? 'whieda').replace(/'/g, '') }}',
    u.user_id,
    '{{ String($('Code: Normalize Payload').first().json.external_chat_id ?? '').replace(/'/g, '') }}',
    '{{ String($('Code: Normalize Payload').first().json.external_user_id ?? '').replace(/'/g, '') }}',
    '{{ String($('Code: Normalize Payload').first().json.external_username ?? '').replace(/'/g, '') }}',
    '{{ String($('Code: Normalize Payload').first().json.display_name ?? '').replace(/'/g, '') }}',
    CASE WHEN lower('{{ String($('Code: Normalize Payload').first().json.message_text ?? '').replace(/'/g, '') }}') ~ '^/subscribe(?:@\\w+)?(?:\\s|$)' THEN TRUE ELSE FALSE END,
    now(), now(), now()
  FROM uid u
  WHERE '{{ String($('Code: Normalize Payload').first().json.chat_type ?? '').replace(/'/g, '') }}' = 'private'
  ON CONFLICT (client_id, user_id)
  DO UPDATE SET
    private_chat_id = EXCLUDED.private_chat_id,
    telegram_user_id = EXCLUDED.telegram_user_id,
    username = EXCLUDED.username,
    display_name = EXCLUDED.display_name,
    is_subscribed = CASE
      WHEN lower('{{ String($('Code: Normalize Payload').first().json.message_text ?? '').replace(/'/g, '') }}') ~ '^/subscribe(?:@\\w+)?(?:\\s|$)' THEN TRUE
      WHEN lower('{{ String($('Code: Normalize Payload').first().json.message_text ?? '').replace(/'/g, '') }}') ~ '^/unsubscribe(?:@\\w+)?(?:\\s|$)' THEN FALSE
      ELSE advisor_telegram_subscriptions.is_subscribed
    END,
    last_seen_at = now(),
    updated_at = now()
  RETURNING user_id
)
SELECT u.user_id, c.conversation_id
FROM uid u
CROSS JOIN upsert_conv c
LIMIT 1;"""

NEW_CANDIDATE_REGISTRATION_QUERY = """CREATE TABLE IF NOT EXISTS advisor_user_candidate_alerts (
  client_id text NOT NULL,
  telegram_user_id text NOT NULL,
  first_alerted_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, telegram_user_id)
);

WITH inserted AS (
  INSERT INTO advisor_user_candidate_alerts (client_id, telegram_user_id)
  SELECT
    '{{ String($('Code: Normalize Payload').first().json.project_id ?? 'whieda').replace(/'/g, '') }}',
    '{{ String($('Code: Normalize Payload').first().json.external_user_id ?? '').replace(/'/g, '') }}'
  WHERE '{{ String($('Code: Normalize Payload').first().json.chat_type ?? '').replace(/'/g, '') }}' = 'private'
    AND '{{ String($('Code: Normalize Payload').first().json.external_user_id ?? '').replace(/'/g, '') }}' <> '688931415'
  ON CONFLICT DO NOTHING
  RETURNING 1
)
SELECT EXISTS(SELECT 1 FROM inserted) AS new_user_candidate;"""

ACCESS_LOOKUP_QUERY = """SELECT CASE
  WHEN '{{ String($('Code: Normalize Payload').first().json.chat_type ?? '').replace(/'/g, '') }}' <> 'private' THEN 'approved'
  ELSE COALESCE((
    SELECT access_status
    FROM advisor_structured_users_access
    WHERE client_id = '{{ String($('Code: Normalize Payload').first().json.project_id ?? 'whieda').replace(/'/g, '') }}'
      AND telegram_user_id = '{{ String($('Code: Normalize Payload').first().json.external_user_id ?? '').replace(/'/g, '') }}'
    LIMIT 1
  ), 'candidate')
END AS access_status;"""

BROADCAST_SNAPSHOT_QUERY = """CREATE TABLE IF NOT EXISTS advisor_broadcast_drafts (
  client_id text NOT NULL,
  draft_id text NOT NULL,
  created_by_user_id text NOT NULL,
  text_body text NOT NULL,
  audience text NOT NULL DEFAULT 'partners',
  status text NOT NULL DEFAULT 'draft',
  created_at timestamptz NOT NULL DEFAULT now(),
  confirmed_at timestamptz,
  cancelled_at timestamptz,
  PRIMARY KEY (client_id, draft_id)
);

ALTER TABLE advisor_broadcast_drafts
  ADD COLUMN IF NOT EXISTS audience text NOT NULL DEFAULT 'partners';

ALTER TABLE advisor_broadcast_drafts
  ADD COLUMN IF NOT EXISTS broadcast_type text NOT NULL DEFAULT 'announcement';

ALTER TABLE advisor_broadcast_drafts
  ADD COLUMN IF NOT EXISTS source_event_id text;

CREATE TABLE IF NOT EXISTS advisor_scheduled_events (
  client_id text NOT NULL,
  event_id text NOT NULL,
  created_by_user_id text NOT NULL,
  audience text NOT NULL,
  text_body text NOT NULL,
  meeting_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'draft',
  created_at timestamptz NOT NULL DEFAULT now(),
  confirmed_at timestamptz,
  cancelled_at timestamptz,
  PRIMARY KEY (client_id, event_id)
);

CREATE TABLE IF NOT EXISTS advisor_scheduled_event_jobs (
  client_id text NOT NULL,
  event_id text NOT NULL,
  reminder_key text NOT NULL,
  scheduled_at timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'draft',
  draft_id text,
  dispatched_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (client_id, event_id, reminder_key)
);

SELECT
  count(*) FILTER (WHERE lower(COALESCE(a.access_status, '')) = 'candidate')::text AS candidates_subscriber_count,
  count(*) FILTER (WHERE lower(COALESCE(a.access_status, '')) = 'approved' AND lower(COALESCE(a.role, 'partner')) = 'partner')::text AS partners_subscriber_count,
  count(*) FILTER (WHERE lower(COALESCE(a.access_status, '')) = 'approved' AND lower(COALESCE(a.role, '')) IN ('leader', 'admin', 'super_admin'))::text AS leaders_subscriber_count
FROM advisor_telegram_subscriptions s
JOIN advisor_structured_users_access a
  ON a.client_id = s.client_id
 AND a.telegram_user_id = s.telegram_user_id
WHERE s.client_id = 'whieda'
  AND s.is_subscribed IS TRUE
  AND s.blocked_at IS NULL;"""

BROADCAST_COMMAND_QUERY = """INSERT INTO advisor_broadcast_drafts (client_id, draft_id, created_by_user_id, text_body, audience, status, broadcast_type)
SELECT
  '{{ String($('Code: Structured Sheet Lookup').first().json.project_id ?? 'whieda').replace(/'/g, '') }}',
  '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_draft_id ?? '').replace(/'/g, '') }}',
  '{{ String($('Code: Structured Sheet Lookup').first().json.external_user_id ?? '').replace(/'/g, '') }}',
  '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_text ?? '').replace(/'/g, '') }}',
  '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_audience ?? 'partners').replace(/'/g, '') }}',
  'draft',
  'announcement'
WHERE '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_action ?? '').replace(/'/g, '') }}' = 'create'
ON CONFLICT (client_id, draft_id) DO NOTHING;

INSERT INTO advisor_scheduled_events (client_id, event_id, created_by_user_id, audience, text_body, meeting_at, status)
SELECT
  '{{ String($('Code: Structured Sheet Lookup').first().json.project_id ?? 'whieda').replace(/'/g, '') }}',
  '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_draft_id ?? '').replace(/'/g, '') }}',
  '{{ String($('Code: Structured Sheet Lookup').first().json.external_user_id ?? '').replace(/'/g, '') }}',
  '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_audience ?? 'partners').replace(/'/g, '') }}',
  '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_text ?? '').replace(/'/g, '') }}',
  NULLIF('{{ String($('Code: Structured Sheet Lookup').first().json.meeting_at ?? '').replace(/'/g, '') }}', '')::timestamptz,
  'draft'
WHERE '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_action ?? '').replace(/'/g, '') }}' = 'create_meeting'
ON CONFLICT (client_id, event_id) DO NOTHING;

INSERT INTO advisor_scheduled_event_jobs (client_id, event_id, reminder_key, scheduled_at, status)
SELECT e.client_id, e.event_id, j.reminder_key, e.meeting_at - j.delay, 'draft'
FROM advisor_scheduled_events e
CROSS JOIN (VALUES ('24h', interval '24 hours'), ('1h', interval '1 hour')) AS j(reminder_key, delay)
WHERE e.client_id = '{{ String($('Code: Structured Sheet Lookup').first().json.project_id ?? 'whieda').replace(/'/g, '') }}'
  AND e.event_id = '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_draft_id ?? '').replace(/'/g, '') }}'
  AND '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_action ?? '').replace(/'/g, '') }}' = 'create_meeting'
ON CONFLICT (client_id, event_id, reminder_key) DO NOTHING;

UPDATE advisor_broadcast_drafts
SET status = 'confirmed', confirmed_at = now()
WHERE client_id = '{{ String($('Code: Structured Sheet Lookup').first().json.project_id ?? 'whieda').replace(/'/g, '') }}'
  AND draft_id = '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_draft_id ?? '').replace(/'/g, '') }}'
  AND created_by_user_id = '{{ String($('Code: Structured Sheet Lookup').first().json.external_user_id ?? '').replace(/'/g, '') }}'
  AND status = 'draft'
  AND '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_action ?? '').replace(/'/g, '') }}' = 'confirm';

UPDATE advisor_broadcast_drafts
SET status = 'cancelled', cancelled_at = now()
WHERE client_id = '{{ String($('Code: Structured Sheet Lookup').first().json.project_id ?? 'whieda').replace(/'/g, '') }}'
  AND draft_id = '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_draft_id ?? '').replace(/'/g, '') }}'
  AND created_by_user_id = '{{ String($('Code: Structured Sheet Lookup').first().json.external_user_id ?? '').replace(/'/g, '') }}'
  AND status = 'draft'
  AND '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_action ?? '').replace(/'/g, '') }}' = 'cancel';

UPDATE advisor_scheduled_events
SET status = 'confirmed', confirmed_at = now()
WHERE client_id = '{{ String($('Code: Structured Sheet Lookup').first().json.project_id ?? 'whieda').replace(/'/g, '') }}'
  AND event_id = '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_draft_id ?? '').replace(/'/g, '') }}'
  AND created_by_user_id = '{{ String($('Code: Structured Sheet Lookup').first().json.external_user_id ?? '').replace(/'/g, '') }}'
  AND status = 'draft'
  AND '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_action ?? '').replace(/'/g, '') }}' = 'confirm_meeting';

UPDATE advisor_scheduled_event_jobs
SET status = 'pending'
WHERE client_id = '{{ String($('Code: Structured Sheet Lookup').first().json.project_id ?? 'whieda').replace(/'/g, '') }}'
  AND event_id = '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_draft_id ?? '').replace(/'/g, '') }}'
  AND status = 'draft'
  AND '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_action ?? '').replace(/'/g, '') }}' = 'confirm_meeting';

UPDATE advisor_scheduled_events
SET status = 'cancelled', cancelled_at = now()
WHERE client_id = '{{ String($('Code: Structured Sheet Lookup').first().json.project_id ?? 'whieda').replace(/'/g, '') }}'
  AND event_id = '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_draft_id ?? '').replace(/'/g, '') }}'
  AND created_by_user_id = '{{ String($('Code: Structured Sheet Lookup').first().json.external_user_id ?? '').replace(/'/g, '') }}'
  AND status IN ('draft', 'confirmed')
  AND '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_action ?? '').replace(/'/g, '') }}' = 'cancel_meeting';

UPDATE advisor_scheduled_event_jobs
SET status = 'cancelled'
WHERE client_id = '{{ String($('Code: Structured Sheet Lookup').first().json.project_id ?? 'whieda').replace(/'/g, '') }}'
  AND event_id = '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_draft_id ?? '').replace(/'/g, '') }}'
  AND status IN ('draft', 'pending')
  AND '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_action ?? '').replace(/'/g, '') }}' = 'cancel_meeting';

SELECT '{{ String($('Code: Structured Sheet Lookup').first().json.broadcast_action ?? 'noop').replace(/'/g, '') }}' AS action;"""

REVIEW_QUEUE_SNAPSHOT_QUERY = """SELECT COALESCE(json_agg(row_to_json(rows)), '[]'::json) AS review_rows
FROM (
  SELECT
    client_id,
    source_ref,
    source_title,
    source_type,
    status,
    COALESCE(queue_status, source_payload->>'queue_status', status) AS queue_status,
    COALESCE(trust_level, source_payload->>'trust_level', CASE WHEN COALESCE(source_payload->>'trusted_reviewer', 'false') = 'true' THEN 'trusted' ELSE 'candidate' END) AS trust_level,
    CASE
      WHEN COALESCE(NULLIF(source_payload->>'owner', ''), '') <> ''
       AND COALESCE(NULLIF(owner, ''), NULLIF(review_owner, ''), '') = COALESCE(NULLIF(trusted_reviewer_role, ''), NULLIF(source_payload->>'trusted_reviewer_role', ''), '')
       AND COALESCE(NULLIF(source_payload->>'owner', ''), '') <> COALESCE(NULLIF(owner, ''), NULLIF(review_owner, ''), '')
      THEN source_payload->>'owner'
      ELSE COALESCE(NULLIF(owner, ''), NULLIF(review_owner, ''), NULLIF(source_payload->>'owner', ''), NULLIF(source_payload->>'review_owner', ''), '')
    END AS owner,
    COALESCE(NULLIF(priority, ''), NULLIF(review_priority, ''), NULLIF(source_payload->>'priority', ''), NULLIF(source_payload->>'review_priority', ''), '') AS priority,
    COALESCE(NULLIF(review_type, ''), NULLIF(source_payload->>'review_type', ''), '') AS review_type,
    COALESCE(NULLIF(target_layer, ''), NULLIF(source_payload->>'target_layer', ''), '') AS target_layer,
    COALESCE(NULLIF(external_username, ''), NULLIF(source_payload->>'external_username', ''), '') AS external_username,
    COALESCE(NULLIF(trusted_reviewer_name, ''), NULLIF(source_payload->>'trusted_reviewer_name', ''), '') AS trusted_reviewer_name,
    COALESCE(NULLIF(trusted_reviewer_role, ''), NULLIF(source_payload->>'trusted_reviewer_role', ''), '') AS trusted_reviewer_role,
    COALESCE(
      feedback_text,
      gap_reason,
      user_text,
      source_payload->>'feedback_text',
      source_payload->>'gap_reason',
      source_payload->>'user_text',
      source_title,
      ''
    ) AS short_text,
    taken_by,
    taken_at,
    applied_by,
    applied_at,
    verified_by,
    verified_at,
    closed_by,
    closed_at,
    triaged_at,
    created_at
  FROM advisor_review_queue
  WHERE client_id = 'whieda'
    AND COALESCE(queue_status, status, '') <> 'ignored_test'
  ORDER BY created_at DESC
  LIMIT 120
) rows;"""

REVIEW_QUEUE_WRITE_QUERY = """INSERT INTO advisor_review_queue
  (
    client_id,
    source_type,
    source_ref,
    source_title,
    source_payload,
    status,
    queue_status,
    trust_level,
    owner,
    priority,
    review_type,
    target_layer,
    trusted_reviewer,
    trusted_reviewer_role,
    trusted_reviewer_name,
    external_username,
    feedback_type,
    feedback_text,
    gap_reason,
    user_text,
    bot_answer,
    review_owner,
    review_priority
  )
VALUES (
  '{{ String($json.project_id ?? 'whieda').replace(/'/g, '') }}',
  '{{ String($json.review_source_type ?? ($json.dify_response?.answer_mode === 'feedback_ack' ? 'feedback' : 'knowledge_gap')).replace(/'/g, '') }}',
  '{{ String($json.idempotency_key ?? '').replace(/'/g, '') }}',
  '{{ String(($json.user_message_text || $json.message_text || '').slice(0, 100)).replace(/'/g, '') }}',
  '{{ JSON.stringify({
    kind: $json.review_source_type ?? ($json.dify_response?.answer_mode === 'feedback_ack' ? 'feedback' : 'knowledge_gap'),
    review_type: $json.review_type || null,
    target_layer: $json.target_layer || null,
    priority: $json.review_priority || null,
    owner: $json.review_owner || null,
    queue_status: $json.review_queue_status || 'pending',
    trust_level: $json.review_trust_level || 'candidate',
    trusted_reviewer: $json.trusted_reviewer === true,
    trusted_reviewer_role: $json.trusted_reviewer_role || null,
    trusted_reviewer_name: $json.trusted_reviewer_name || null,
    external_username: $json.external_username || null,
    feedback_type: $json.feedback_type || $json.dify_response?.feedback_type || null,
    feedback_text: $json.feedback_text || $json.dify_response?.feedback_text || null,
    gap_reason: $json.gap_reason || $json.dify_response?.gap_reason || 'unspecified',
    user_text: $json.user_message_text || '',
    bot_answer: $json.reply_text || $json.message_text || '',
    conversation_id: $json.conversation_id,
    project_id: $json.project_id || 'whieda'
    ,synthetic_test: $json.synthetic_test === true
  }).replace(/'/g, '') }}'::jsonb,
  '{{ String($json.review_queue_status || 'pending').replace(/'/g, '') }}',
  '{{ String($json.review_queue_status || 'pending').replace(/'/g, '') }}',
  '{{ String($json.review_trust_level || 'candidate').replace(/'/g, '') }}',
  '{{ String($json.review_owner || '').replace(/'/g, '') }}',
  '{{ String($json.review_priority || '').replace(/'/g, '') }}',
  '{{ String($json.review_type || '').replace(/'/g, '') }}',
  '{{ String($json.target_layer || '').replace(/'/g, '') }}',
  {{ $json.trusted_reviewer === true ? 'TRUE' : 'FALSE' }},
  '{{ String($json.trusted_reviewer_role || '').replace(/'/g, '') }}',
  '{{ String($json.trusted_reviewer_name || '').replace(/'/g, '') }}',
  '{{ String($json.external_username || '').replace(/'/g, '') }}',
  '{{ String($json.feedback_type || $json.dify_response?.feedback_type || '').replace(/'/g, '') }}',
  '{{ String($json.feedback_text || $json.dify_response?.feedback_text || '').replace(/'/g, '') }}',
  '{{ String($json.gap_reason || $json.dify_response?.gap_reason || 'unspecified').replace(/'/g, '') }}',
  '{{ String($json.user_message_text || '').replace(/'/g, '') }}',
  '{{ String($json.reply_text || $json.message_text || '').replace(/'/g, '') }}',
  '{{ String($json.review_owner || '').replace(/'/g, '') }}',
  '{{ String($json.review_priority || '').replace(/'/g, '') }}'
)
ON CONFLICT (client_id, source_type, source_ref) DO NOTHING"""


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
            raise RuntimeError(f"SSH failed: {command}\nSTDOUT:\n{out}\nSTDERR:\n{err}")
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


def build_photo_check_node(position):
    return {
        "parameters": {
            "conditions": {
                "options": {
                    "caseSensitive": False,
                    "leftValue": "",
                    "typeValidation": "loose",
                    "version": 1,
                },
                "combinator": "and",
                "conditions": [
                    {
                        "id": "cond-answer-photo-exists",
                        "operator": {"type": "boolean", "operation": "equals"},
                        "leftValue": "={{ !!$('Code: Validate Dify Response').first().json.telegram_photo_url }}",
                        "rightValue": True,
                    }
                ],
            },
            "options": {},
        },
        "id": PHOTO_CHECK_NODE_ID,
        "name": "IF: Answer Has Photo?",
        "type": "n8n-nodes-base.if",
        "position": position,
        "typeVersion": 2,
    }


def build_product_details_node(position, credentials):
    return {
        "parameters": {
            "operation": "executeQuery",
            "query": """SELECT COALESCE(json_agg(row_to_json(rows)), '[]'::json) AS product_details
FROM (
  SELECT
    client_id AS project_id,
    detail_id,
    sku,
    topic,
    title,
    answer_text,
    source_id,
    source_url,
    source_locator,
    evidence_type,
    review_status,
    audience,
    active::text AS active,
    priority::text AS priority,
    owner_state,
    content_hash,
    updated_at,
    notes
  FROM advisor_structured_product_details
  WHERE client_id = 'whieda'
    AND active IS TRUE
  ORDER BY priority DESC, detail_id
) rows;""",
            "options": {},
        },
        "id": PRODUCT_DETAILS_NODE_ID,
        "name": "Postgres: Product Details",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.6,
        "position": position,
        "credentials": credentials,
    }


def build_product_comparisons_node(position, credentials):
    return {
        "parameters": {
            "operation": "executeQuery",
            "query": """SELECT COALESCE(json_agg(row_to_json(rows)), '[]'::json) AS product_comparisons
FROM (
  SELECT
    client_id AS project_id,
    comparison_id,
    left_sku,
    right_sku,
    title,
    answer_text,
    why_choose_left,
    why_choose_right,
    important_note,
    source_id,
    source_status,
    review_status,
    active::text AS active,
    priority::text AS priority,
    updated_at,
    notes
  FROM advisor_structured_product_comparisons
  WHERE client_id = 'whieda'
    AND active IS TRUE
  ORDER BY priority DESC, comparison_id
) rows;""",
            "options": {},
        },
        "id": PRODUCT_COMPARISONS_NODE_ID,
        "name": "Postgres: Product Comparisons",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.6,
        "position": position,
        "credentials": credentials,
    }


def build_business_objections_node(position, credentials):
    return {
        "parameters": {
            "operation": "executeQuery",
            "query": """SELECT COALESCE(json_agg(row_to_json(rows)), '[]'::json) AS business_objections
FROM (
  SELECT
    client_id AS project_id,
    objection_id,
    title,
    aliases,
    first_reply,
    clarify,
    confirmed_answer_rule,
    next_step,
    do_not_say,
    source_id,
    review_status,
    active::text AS active,
    priority::text AS priority,
    owner_state,
    updated_at,
    notes
  FROM advisor_structured_business_objections
  WHERE client_id = 'whieda'
    AND active IS TRUE
  ORDER BY priority DESC, objection_id
) rows;""",
            "options": {},
        },
        "id": BUSINESS_OBJECTIONS_NODE_ID,
        "name": "Postgres: Business Objections",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.6,
        "position": position,
        "credentials": credentials,
    }


def build_business_faq_node(position, credentials):
    return {
        "parameters": {
            "operation": "executeQuery",
            "query": """SELECT COALESCE(json_agg(row_to_json(rows)), '[]'::json) AS business_faq
FROM (
  SELECT
    client_id AS project_id,
    faq_id,
    title,
    aliases,
    answer_text,
    do_not_say,
    source_id,
    review_status,
    active::text AS active,
    priority::text AS priority,
    owner_state,
    updated_at,
    notes
  FROM advisor_structured_business_faq
  WHERE client_id = 'whieda'
    AND active IS TRUE
  ORDER BY priority DESC, faq_id
) rows;""",
            "options": {},
        },
        "id": BUSINESS_FAQ_NODE_ID,
        "name": "Postgres: Business FAQ",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.6,
        "position": position,
        "credentials": credentials,
    }


def build_broadcast_snapshot_node(position, credentials):
    return {
        "parameters": {"operation": "executeQuery", "query": BROADCAST_SNAPSHOT_QUERY, "options": {}},
        "id": BROADCAST_SNAPSHOT_NODE_ID,
        "name": "Postgres: Broadcast Snapshot",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.6,
        "position": position,
        "credentials": credentials,
    }


def build_broadcast_command_node(position, credentials):
    return {
        "parameters": {"operation": "executeQuery", "query": BROADCAST_COMMAND_QUERY, "options": {}},
        "id": BROADCAST_COMMAND_NODE_ID,
        "name": "Postgres: Broadcast Command",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.6,
        "position": position,
        "credentials": credentials,
    }


def build_broadcast_merge_node(position):
    return {
        "parameters": {"jsCode": """const source = $('Code: Structured Sheet Lookup').first().json;
const result = $input.first()?.json ?? {};
return [{ json: { ...source, broadcast_db_action: result.action || null } }];"""},
        "id": BROADCAST_MERGE_NODE_ID,
        "name": "Code: Merge Broadcast Command",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": position,
    }


def build_broadcast_dispatch_if_node(position):
    return {"parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"}, "combinator": "and", "conditions": [{
        "id": "broadcast-confirm", "operator": {"type": "string", "operation": "equals"},
        "leftValue": "={{ String($json.broadcast_action || '') }}", "rightValue": "confirm",
    }]}, "options": {}}, "id": BROADCAST_DISPATCH_IF_NODE_ID, "name": "IF: Broadcast Confirmed?", "type": "n8n-nodes-base.if", "typeVersion": 2, "position": position}


def build_broadcast_dispatch_trigger_node(position):
    return {"parameters": {"method": "POST", "url": "https://sysarchn8n.duckdns.org/webhook/whieda-broadcast-delivery-v1", "sendBody": True, "specifyBody": "json", "jsonBody": "={{ { draft_id: $json.broadcast_draft_id } }}", "options": {}}, "id": BROADCAST_DISPATCH_TRIGGER_NODE_ID, "name": "HTTP: Trigger Broadcast Delivery", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.4, "position": position}


def build_new_candidate_if_node(position):
    return {
        "parameters": {
            "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"}, "combinator": "and", "conditions": [{
                "id": "new-user-candidate", "operator": {"type": "boolean", "operation": "equals"},
                "leftValue": "={{ $json.new_user_candidate === true }}", "rightValue": True,
            }]},
            "options": {},
        },
        "id": NEW_CANDIDATE_IF_NODE_ID,
        "name": "IF: New User Candidate?",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2,
        "position": position,
    }


def build_new_candidate_register_node(position, credentials):
    return {
        "parameters": {"operation": "executeQuery", "query": NEW_CANDIDATE_REGISTRATION_QUERY, "options": {}},
        "id": NEW_CANDIDATE_REGISTER_NODE_ID,
        "name": "Postgres: Register New User Candidate",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.6,
        "position": position,
        "credentials": credentials,
    }


def build_new_candidate_alert_node(position):
    return {
        "parameters": {
            "method": "POST",
            "url": "={{ 'https://api.telegram.org/bot' + $env.WHIEDA_ADVISOR_TELEGRAM_BOT_TOKEN + '/sendMessage' }}",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": """={{ {
  chat_id: '688931415',
  text: 'Новый кандидат в доступе: ' + (($('Code: Normalize Payload').first().json.display_name || 'Без имени')) + (($('Code: Normalize Payload').first().json.external_username) ? (' (@' + $('Code: Normalize Payload').first().json.external_username + ')') : ' (без @логина)') + '.\nЗапрошенная структура: ' + ($('Code: Normalize Payload').first().json.referral_code || 'general') + '.\n\nПроверьте строку в Users_Access и укажите approved или blocked.',
  disable_web_page_preview: true
} }}""",
            "options": {},
        },
        "id": NEW_CANDIDATE_ALERT_NODE_ID,
        "name": "Telegram: Alert New User Candidate",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.4,
        "position": position,
    }


def build_new_candidate_sheet_row_node(position):
    return {
        "parameters": {"jsCode": """const source = $('Code: Normalize Payload').first().json;
const username = String(source.external_username || '').trim();
const now = new Date().toISOString();
return [{
  json: {
    telegram_user_id: String(source.external_user_id || ''),
    display_name: String(source.display_name || 'Без имени'),
    username: username ? '@' + username : '',
    role: 'partner',
    access_status: 'candidate',
    subscription_status: 'off',
    last_seen_at: now,
    notes: 'Новый контакт. Нужна проверка администратора.',
    updated_at: now,
    // A referral is a request, never an automatic transfer into a leader's structure.
    structure_code: 'general',
    structure_owner: 'Виктор Хрипко',
    alert_recipient: 'Виктор Хрипко',
    requested_structure_code: String(source.referral_code || 'general'),
    ownership_status: 'candidate',
    assigned_by: '',
    ownership_updated_at: now,
  },
}];"""},
        "id": NEW_CANDIDATE_SHEET_ROW_NODE_ID,
        "name": "Code: Build Users Access Candidate Row",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": position,
    }


def build_new_candidate_sheet_append_node(position):
    return {
        "parameters": {
            "operation": "append",
            "documentId": {"__rl": True, "value": "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/edit", "mode": "url"},
            "sheetName": {"__rl": True, "value": "Users_Access", "mode": "name"},
            "columns": {"mappingMode": "autoMapInputData", "value": {}, "schema": []},
            "options": {},
        },
        "id": NEW_CANDIDATE_SHEET_APPEND_NODE_ID,
        "name": "Google Sheets: Append Users Access Candidate",
        "type": "n8n-nodes-base.googleSheets",
        "typeVersion": 4.7,
        "position": position,
        "credentials": {"googleSheetsOAuth2Api": {"id": "XnF6UcslXgxuXbGm", "name": "Google Sheets account"}},
    }


def build_new_candidate_restore_node(position):
    return {
        "parameters": {"jsCode": "return [{ json: $('Postgres: Upsert User + Conversation').first().json }];"},
        "id": NEW_CANDIDATE_RESTORE_NODE_ID,
        "name": "Code: Restore User Session After Alert",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": position,
    }


def build_access_lookup_node(position, credentials):
    return {
        "parameters": {"operation": "executeQuery", "query": ACCESS_LOOKUP_QUERY, "options": {}},
        "id": ACCESS_LOOKUP_NODE_ID,
        "name": "Postgres: Lookup User Access",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.6,
        "position": position,
        "credentials": credentials,
    }


def build_access_approved_if_node(position):
    return {
        "parameters": {"conditions": {"options": {"caseSensitive": False, "leftValue": "", "typeValidation": "strict"}, "combinator": "and", "conditions": [{
            "id": "access-approved", "operator": {"type": "string", "operation": "equals"},
            "leftValue": "={{ String($json.access_status || '').toLowerCase() }}", "rightValue": "approved",
        }]}, "options": {}},
        "id": ACCESS_APPROVED_IF_NODE_ID,
        "name": "IF: User Access Approved?",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2,
        "position": position,
    }


def build_pending_access_reply_node(position):
    return {
        "parameters": {
            "method": "POST",
            "url": "={{ 'https://api.telegram.org/bot' + $env.WHIEDA_ADVISOR_TELEGRAM_BOT_TOKEN + '/sendMessage' }}",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": """={{ {
  chat_id: $('Code: Normalize Payload').first().json.external_chat_id,
  text: 'Заявка принята. Доступ к советнику подтвердит администратор; после этого здесь можно будет смотреть товары, цены, фото и материалы.',
  disable_web_page_preview: true
} }}""",
            "options": {},
        },
        "id": PENDING_ACCESS_REPLY_NODE_ID,
        "name": "Telegram: Pending Access Reply",
        "type": "n8n-nodes-base.httpRequest",
        "typeVersion": 4.4,
        "position": position,
    }


def build_followup_text_node(position):
    return {
        "parameters": {
            "method": "POST",
            "url": "={{ 'https://api.telegram.org/bot' + $env.WHIEDA_ADVISOR_TELEGRAM_BOT_TOKEN + '/sendMessage' }}",
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": """={{ {
  chat_id: $('Code: Validate Dify Response').first().json.external_chat_id,
  text: $('Code: Validate Dify Response').first().json.reply_text || $('Code: Validate Dify Response').first().json.dify_response?.answer_text || 'No answer text.',
  disable_web_page_preview: true,
  parse_mode: 'HTML'
} }}""",
            "options": {},
        },
        "id": FOLLOWUP_TEXT_NODE_ID,
        "name": "Telegram: Send Followup Text — Answer",
        "type": "n8n-nodes-base.httpRequest",
        "position": position,
        "typeVersion": 4.2,
    }


def main() -> None:
    structured_sheet_code = Path(__file__).with_name("structured_sheet_lookup_current.js").read_text(encoding="utf-8")
    structured_resource_code = Path(__file__).with_name("structured_resource_lookup_current.js").read_text(encoding="utf-8")
    validate_dify_code = Path(__file__).with_name("validate_dify_response_current.js").read_text(encoding="utf-8")

    session = requests.Session()
    session.post(
        f"{BASE_URL}/rest/login",
        json={"emailOrLdapLoginId": EMAIL, "password": PASSWORD},
        verify=False,
        timeout=30,
    ).raise_for_status()

    workflow = session.get(
        f"{BASE_URL}/rest/workflows/{WORKFLOW_ID}",
        verify=False,
        timeout=30,
    ).json()["data"]
    live_backup = json.loads(json.dumps(workflow))

    # Remove a disconnected test fixture from the old Nordman workflow.
    nodes = [node for node in workflow["nodes"] if node["name"] != "Code: Mock Dify Response"]
    connections = workflow["connections"]
    connections.pop("Code: Mock Dify Response", None)

    by_id = {node["id"]: node for node in nodes}
    sheet_node = by_id[STRUCTURED_SHEET_LOOKUP_NODE_ID]
    resource_node = by_id[STRUCTURED_RESOURCE_LOOKUP_NODE_ID]
    validate_node = by_id[VALIDATE_DIFY_RESPONSE_NODE_ID]
    review_snapshot_node = by_id[REVIEW_QUEUE_SNAPSHOT_NODE_ID]
    product_cards_node = next(node for node in nodes if node["name"] == "Postgres: Product Cards")
    resource_node_live = next(node for node in nodes if node["name"] == "Postgres: Resource Links")
    user_session_node = next(node for node in nodes if node["name"] == "Postgres: Upsert User + Conversation")
    structured_node = by_id[STRUCTURED_SHEET_LOOKUP_NODE_ID]
    normalize_node = next(node for node in nodes if node["name"] == "Code: Normalize Payload")
    structured_hit_node = next(node for node in nodes if node["name"] == "IF: Structured Hit?")
    review_write_node = next(node for node in nodes if node["name"] == "Postgres: Write Review Queue")
    answer_node = by_id[TELEGRAM_SEND_ANSWER_NODE_ID]
    audit_node = by_id[AUDIT_ANSWER_NODE_ID]
    dify_request_node = next(node for node in nodes if node["name"] == "HTTP Request: Dify advisor")
    escalation_node = next(node for node in nodes if str(node.get("name", "")).endswith("Escalation"))
    gap_node = next(node for node in nodes if "Telegram: Send Reply" in str(node.get("name", "")) and str(node.get("name", "")).endswith("Gap"))

    sheet_node["parameters"]["jsCode"] = structured_sheet_code
    normalize_code = normalize_node["parameters"]["jsCode"]
    referral_marker = "const startReferralMatch = rawText.trim().match(/^\\/start(?:@\\w+)?\\s+([a-z0-9_-]{1,64})$/i);"
    if referral_marker not in normalize_code:
        normalize_code = normalize_code.replace(
            "const trimmedText = rawText.trim();",
            "const trimmedText = rawText.trim();\n" + referral_marker + "\nconst referralCode = startReferralMatch ? String(startReferralMatch[1]).toLowerCase() : null;",
        )
        normalize_code = normalize_code.replace(
            "raw_payload: item,",
            "referral_code: referralCode,\n    raw_payload: item,",
        )
    normalize_node["parameters"]["jsCode"] = normalize_code
    resource_node["parameters"]["jsCode"] = structured_resource_code
    validate_node["parameters"]["jsCode"] = validate_dify_code
    dify_request_node["parameters"]["jsonBody"] = """={{ { inputs: {
  user_text: $json.message_text,
  user_Text: $json.message_text,
  query: $json.message_text,
  message_text: $json.message_text,
  project_id: $json.project_id,
  channel: $json.channel,
  deep_requested: $json.deep_requested === true,
  structured_match: $json.structured_match || null,
  structured_resources_context: $json.structured_resources_context || [],
  conversation_context: $json.conversation_context || null
}, response_mode: 'blocking', user: String($json.external_user_id || $json.external_chat_id || 'advisor-user') } }}"""
    user_session_node["parameters"]["query"] = USER_AND_CONVERSATION_QUERY
    review_snapshot_node["parameters"]["query"] = REVIEW_QUEUE_SNAPSHOT_QUERY
    review_write_node["parameters"]["query"] = REVIEW_QUEUE_WRITE_QUERY

    answer_node["parameters"]["url"] = "={{ 'https://api.telegram.org/bot' + $env.WHIEDA_ADVISOR_TELEGRAM_BOT_TOKEN + (($('Code: Validate Dify Response').first().json.telegram_photo_url) ? '/sendPhoto' : '/sendMessage') }}"
    answer_node["parameters"]["jsonBody"] = """={{ (() => {
  const data = $('Code: Validate Dify Response').first().json;
  if (data.telegram_photo_url) {
    return {
      chat_id: data.external_chat_id,
      photo: data.telegram_photo_url,
    };
  }
  return {
    chat_id: data.external_chat_id,
    text: data.reply_text || data.dify_response?.answer_text || 'No answer text.',
    disable_web_page_preview: true,
    parse_mode: 'HTML',
  };
})() }}"""
    escalation_node["parameters"]["jsonBody"] = """={{ {
  chat_id: $('Code: Validate Dify Response').first().json.external_chat_id,
  text: 'Такой вопрос лучше уточнить у человека. Передал его на проверку.',
  disable_web_page_preview: true
} }}"""
    gap_node["parameters"]["jsonBody"] = """={{ {
  chat_id: $('Code: Validate Dify Response').first().json.external_chat_id,
  text: $('Code: Validate Dify Response').first().json.reply_text || $('Code: Validate Dify Response').first().json.dify_response?.answer_text || 'Пока нет подтверждённой информации по этому вопросу. Передам его на уточнение.',
  disable_web_page_preview: true
} }}"""

    nodes = [node for node in nodes if node["id"] not in {PHOTO_CHECK_NODE_ID, FOLLOWUP_TEXT_NODE_ID, PRODUCT_DETAILS_NODE_ID, PRODUCT_COMPARISONS_NODE_ID, BUSINESS_OBJECTIONS_NODE_ID, BUSINESS_FAQ_NODE_ID, BROADCAST_SNAPSHOT_NODE_ID, BROADCAST_COMMAND_NODE_ID, BROADCAST_MERGE_NODE_ID, BROADCAST_DISPATCH_IF_NODE_ID, BROADCAST_DISPATCH_TRIGGER_NODE_ID, NEW_CANDIDATE_IF_NODE_ID, NEW_CANDIDATE_REGISTER_NODE_ID, NEW_CANDIDATE_ALERT_NODE_ID, NEW_CANDIDATE_RESTORE_NODE_ID, NEW_CANDIDATE_SHEET_ROW_NODE_ID, NEW_CANDIDATE_SHEET_APPEND_NODE_ID, ACCESS_LOOKUP_NODE_ID, ACCESS_APPROVED_IF_NODE_ID, PENDING_ACCESS_REPLY_NODE_ID}]
    nodes.append(build_photo_check_node([answer_node["position"][0] + 224, answer_node["position"][1]]))
    nodes.append(build_followup_text_node([answer_node["position"][0] + 448, answer_node["position"][1] - 96]))
    nodes.append(build_product_details_node([product_cards_node["position"][0] + 136, product_cards_node["position"][1]], product_cards_node["credentials"]))
    nodes.append(build_product_comparisons_node([product_cards_node["position"][0] + 272, product_cards_node["position"][1]], product_cards_node["credentials"]))
    nodes.append(build_business_objections_node([product_cards_node["position"][0] + 408, product_cards_node["position"][1]], product_cards_node["credentials"]))
    nodes.append(build_business_faq_node([product_cards_node["position"][0] + 544, product_cards_node["position"][1]], product_cards_node["credentials"]))
    nodes.append(build_broadcast_snapshot_node([resource_node_live["position"][0] + 160, resource_node_live["position"][1]], resource_node_live["credentials"]))
    nodes.append(build_broadcast_command_node([structured_node["position"][0] + 220, structured_node["position"][1]], resource_node_live["credentials"]))
    nodes.append(build_broadcast_merge_node([structured_node["position"][0] + 440, structured_node["position"][1]]))
    nodes.append(build_broadcast_dispatch_if_node([structured_node["position"][0] + 660, structured_node["position"][1]]))
    nodes.append(build_broadcast_dispatch_trigger_node([structured_node["position"][0] + 884, structured_node["position"][1] - 96]))
    nodes.append(build_new_candidate_register_node([user_session_node["position"][0] + 208, user_session_node["position"][1]], user_session_node["credentials"]))
    nodes.append(build_new_candidate_if_node([user_session_node["position"][0] + 432, user_session_node["position"][1]]))
    nodes.append(build_new_candidate_sheet_row_node([user_session_node["position"][0] + 656, user_session_node["position"][1] - 96]))
    nodes.append(build_new_candidate_sheet_append_node([user_session_node["position"][0] + 880, user_session_node["position"][1] - 96]))
    nodes.append(build_new_candidate_alert_node([user_session_node["position"][0] + 1104, user_session_node["position"][1] - 96]))
    nodes.append(build_access_lookup_node([user_session_node["position"][0] + 656, user_session_node["position"][1] + 96], user_session_node["credentials"]))
    nodes.append(build_access_approved_if_node([user_session_node["position"][0] + 880, user_session_node["position"][1] + 96]))
    nodes.append(build_pending_access_reply_node([user_session_node["position"][0] + 1104, user_session_node["position"][1] + 192]))
    nodes.append(build_new_candidate_restore_node([user_session_node["position"][0] + 1104, user_session_node["position"][1] + 96]))
    workflow["nodes"] = nodes

    audit_name = audit_node["name"]
    answer_name = answer_node["name"]
    photo_check_name = "IF: Answer Has Photo?"
    followup_name = "Telegram: Send Followup Text — Answer"
    product_cards_name = product_cards_node["name"]
    product_details_name = "Postgres: Product Details"
    product_comparisons_name = "Postgres: Product Comparisons"
    business_objections_name = "Postgres: Business Objections"
    business_faq_name = "Postgres: Business FAQ"
    broadcast_snapshot_name = "Postgres: Broadcast Snapshot"
    broadcast_command_name = "Postgres: Broadcast Command"
    broadcast_merge_name = "Code: Merge Broadcast Command"
    broadcast_dispatch_if_name = "IF: Broadcast Confirmed?"
    broadcast_dispatch_trigger_name = "HTTP: Trigger Broadcast Delivery"
    review_snapshot_name = review_snapshot_node["name"]
    structured_name = structured_node["name"]
    structured_hit_name = structured_hit_node["name"]
    user_session_name = user_session_node["name"]
    merge_session_name = "Code: Merge Envelope + Session"
    new_candidate_register_name = "Postgres: Register New User Candidate"
    new_candidate_if_name = "IF: New User Candidate?"
    new_candidate_sheet_row_name = "Code: Build Users Access Candidate Row"
    new_candidate_sheet_append_name = "Google Sheets: Append Users Access Candidate"
    new_candidate_alert_name = "Telegram: Alert New User Candidate"
    new_candidate_restore_name = "Code: Restore User Session After Alert"
    access_lookup_name = "Postgres: Lookup User Access"
    access_approved_if_name = "IF: User Access Approved?"
    pending_access_reply_name = "Telegram: Pending Access Reply"
    resource_name = resource_node_live["name"]

    connections[product_cards_name] = {
        "main": [[{"node": product_details_name, "type": "main", "index": 0}]]
    }
    connections[product_details_name] = {
        "main": [[{"node": product_comparisons_name, "type": "main", "index": 0}]]
    }
    connections[product_comparisons_name] = {
        "main": [[{"node": business_objections_name, "type": "main", "index": 0}]]
    }
    connections[business_objections_name] = {
        "main": [[{"node": business_faq_name, "type": "main", "index": 0}]]
    }
    connections[business_faq_name] = {
        "main": [[{"node": resource_name, "type": "main", "index": 0}]]
    }
    connections[resource_name] = {
        "main": [[{"node": broadcast_snapshot_name, "type": "main", "index": 0}]]
    }
    connections[broadcast_snapshot_name] = {
        "main": [[{"node": review_snapshot_name, "type": "main", "index": 0}]]
    }
    connections[review_snapshot_name] = {
        "main": [[{"node": structured_name, "type": "main", "index": 0}]]
    }
    connections[structured_name] = {
        "main": [[{"node": broadcast_command_name, "type": "main", "index": 0}]]
    }
    connections[broadcast_command_name] = {
        "main": [[{"node": broadcast_merge_name, "type": "main", "index": 0}]]
    }
    connections[broadcast_merge_name] = {"main": [[{"node": broadcast_dispatch_if_name, "type": "main", "index": 0}]]}
    connections[broadcast_dispatch_if_name] = {"main": [
        [{"node": broadcast_dispatch_trigger_name, "type": "main", "index": 0}],
        [{"node": structured_hit_name, "type": "main", "index": 0}],
    ]}
    connections[broadcast_dispatch_trigger_name] = {"main": [[{"node": structured_hit_name, "type": "main", "index": 0}]]}
    connections[user_session_name] = {"main": [[{"node": new_candidate_register_name, "type": "main", "index": 0}]]}
    connections[new_candidate_register_name] = {"main": [[{"node": new_candidate_if_name, "type": "main", "index": 0}]]}
    connections[new_candidate_if_name] = {"main": [
        [{"node": new_candidate_sheet_row_name, "type": "main", "index": 0}],
        [{"node": access_lookup_name, "type": "main", "index": 0}],
    ]}
    connections[new_candidate_sheet_row_name] = {"main": [[{"node": new_candidate_sheet_append_name, "type": "main", "index": 0}]]}
    connections[new_candidate_sheet_append_name] = {"main": [[{"node": new_candidate_alert_name, "type": "main", "index": 0}]]}
    connections[new_candidate_alert_name] = {"main": [[{"node": pending_access_reply_name, "type": "main", "index": 0}]]}
    connections[access_lookup_name] = {"main": [[{"node": access_approved_if_name, "type": "main", "index": 0}]]}
    connections[access_approved_if_name] = {"main": [
        [{"node": new_candidate_restore_name, "type": "main", "index": 0}],
        [{"node": pending_access_reply_name, "type": "main", "index": 0}],
    ]}
    connections[new_candidate_restore_name] = {"main": [[{"node": merge_session_name, "type": "main", "index": 0}]]}

    connections[audit_name] = {
        "main": [[{"node": answer_name, "type": "main", "index": 0}]]
    }
    connections[answer_name] = {
        "main": [[{"node": photo_check_name, "type": "main", "index": 0}]]
    }
    connections[photo_check_name] = {
        "main": [
            [{"node": followup_name, "type": "main", "index": 0}],
            [],
        ]
    }
    workflow["connections"] = connections

    backup_dir = Path(__file__).resolve().parents[1] / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / ("advisor-whieda-phase1-before-meeting-gap-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    backup_path.write_text(json.dumps(live_backup, ensure_ascii=False, indent=2), encoding="utf-8")

    session.patch(
        f"{BASE_URL}/rest/workflows/{WORKFLOW_ID}",
        json=workflow,
        verify=False,
        timeout=120,
    ).raise_for_status()

    try:
        session.post(
            f"{BASE_URL}/rest/workflows/{WORKFLOW_ID}/activate",
            json={},
            verify=False,
            timeout=30,
        )
    except Exception:
        pass

    ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    ssh_run("docker restart n8n-n8n-1")
    wait_for_n8n_ready()

    run_smoke_test = os.environ.get("WHIEDA_SKIP_SMOKE_TEST", "").strip().lower() not in {"1", "true", "yes"}
    trigger_status = None
    trigger_body = None
    trigger_ok = None
    if run_smoke_test:
        test_payload = {
            "message": {
                "message_id": int(time.time()),
                "date": int(time.time()),
                "text": TEST_TEXT,
                "chat": {"id": int(TEST_CHAT_ID), "type": "private"},
                "from": {
                    "id": int(TEST_CHAT_ID),
                    "is_bot": False,
                    "first_name": TEST_FIRST_NAME,
                    "username": TEST_USERNAME,
                },
            }
        }
        test_payload["whieda_synthetic_test"] = True

        trigger = requests.post(
            f"{BASE_URL}/webhook/advisor-whieda-v0",
            json=test_payload,
            verify=False,
            timeout=60,
        )
        trigger_status = trigger.status_code
        trigger_body = trigger.text
        trigger_ok = 200 <= trigger.status_code < 300

    print(json.dumps({
        "workflow_id": WORKFLOW_ID,
        "workflow_patch_updated": True,
        "smoke_test_ran": run_smoke_test,
        "trigger_status": trigger_status,
        "trigger_body": trigger_body,
        "trigger_ok": trigger_ok,
        "test_chat_id": TEST_CHAT_ID,
        "test_text": TEST_TEXT,
        "backup_path": str(backup_path),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
