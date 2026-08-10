"""Publish the isolated WHIEDA meeting-reminder dispatcher.

The VPS invokes its webhook every five minutes.  Each due reminder becomes an
ordinary confirmed broadcast draft, then goes through the existing delivery
worker and delivery journal.  The unique job key prevents duplicates.
"""
import json
import os
import time

import paramiko
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://sysarchn8n.duckdns.org"
EMAIL = os.environ.get("WHIEDA_N8N_EMAIL", "")
PASSWORD = os.environ.get("WHIEDA_N8N_PASSWORD", "")
SERVER_HOST = "185.252.232.93"
SERVER_USER = "root"
SERVER_PASSWORD = os.environ.get("WHIEDA_SSH_PASSWORD", "")
WORKFLOW_NAME = "WHIEDA Meeting Reminder Dispatcher"
WEBHOOK_PATH = "whieda-meeting-reminder-dispatch-v1"
POSTGRES_CREDENTIAL = {"id": "RmjHh3rdZri7axzq", "name": "advisor-dev-postgres"}

DUE_REMINDERS_SQL = """CREATE TABLE IF NOT EXISTS advisor_broadcast_drafts (
  client_id text NOT NULL,
  draft_id text NOT NULL,
  created_by_user_id text NOT NULL,
  text_body text NOT NULL,
  audience text NOT NULL DEFAULT 'partners',
  status text NOT NULL DEFAULT 'draft',
  broadcast_type text NOT NULL DEFAULT 'announcement',
  source_event_id text,
  created_at timestamptz NOT NULL DEFAULT now(),
  confirmed_at timestamptz,
  cancelled_at timestamptz,
  PRIMARY KEY (client_id, draft_id)
);

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

WITH due_jobs AS (
  UPDATE advisor_scheduled_event_jobs j
  SET status = 'dispatching'
  FROM advisor_scheduled_events e
  WHERE j.client_id = e.client_id
    AND j.event_id = e.event_id
    AND j.status = 'pending'
    AND e.status = 'confirmed'
    AND j.scheduled_at <= now()
  RETURNING j.client_id, j.event_id, j.reminder_key, j.scheduled_at
), created_drafts AS (
  INSERT INTO advisor_broadcast_drafts
    (client_id, draft_id, created_by_user_id, text_body, audience, status, broadcast_type, source_event_id, confirmed_at)
  SELECT
    e.client_id,
    'meeting-reminder-' || d.event_id || '-' || d.reminder_key,
    e.created_by_user_id,
    '<b>Напоминание о встрече</b>\n\n' || e.text_body || '\n\n<b>Время:</b> ' || to_char(e.meeting_at AT TIME ZONE 'Europe/Moscow', 'DD.MM.YYYY HH24:MI') || ' (Москва)',
    e.audience,
    'confirmed',
    'meeting',
    e.event_id,
    now()
  FROM due_jobs d
  JOIN advisor_scheduled_events e ON e.client_id = d.client_id AND e.event_id = d.event_id
  ON CONFLICT (client_id, draft_id) DO NOTHING
  RETURNING client_id, draft_id, source_event_id
), marked AS (
  UPDATE advisor_scheduled_event_jobs j
  SET status = 'dispatched',
      draft_id = d.draft_id,
      dispatched_at = now()
  FROM created_drafts d
  WHERE j.client_id = d.client_id
    AND j.event_id = d.source_event_id
    AND d.draft_id = 'meeting-reminder-' || j.event_id || '-' || j.reminder_key
  RETURNING j.client_id, j.event_id, j.reminder_key, j.draft_id
)
SELECT draft_id FROM marked;"""


def workflow():
    return {
        "name": WORKFLOW_NAME,
        "active": True,
        "nodes": [
            {"parameters": {"httpMethod": "POST", "path": WEBHOOK_PATH, "responseMode": "responseNode", "options": {}}, "id": "meeting-hook", "name": "Webhook Trigger", "type": "n8n-nodes-base.webhook", "typeVersion": 1, "position": [-420, 0]},
            {"parameters": {"respondWith": "json", "responseBody": "{ \"status\": \"accepted\" }", "options": {"responseCode": 200}}, "id": "meeting-ack", "name": "Respond: 200 ACK", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1, "position": [-192, -80]},
            {"parameters": {"operation": "executeQuery", "query": DUE_REMINDERS_SQL, "options": {}}, "id": "meeting-due", "name": "Postgres: Create Due Reminder Drafts", "type": "n8n-nodes-base.postgres", "typeVersion": 2.6, "position": [32, 0], "credentials": {"postgres": POSTGRES_CREDENTIAL}},
            {"parameters": {"conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"}, "combinator": "and", "conditions": [{"id": "due-draft", "operator": {"type": "string", "operation": "notEmpty"}, "leftValue": "={{ String($json.draft_id || '') }}", "rightValue": ""}]}, "options": {}}, "id": "meeting-has-draft", "name": "IF: Due Reminder Exists", "type": "n8n-nodes-base.if", "typeVersion": 2, "position": [256, 0]},
            {"parameters": {"method": "POST", "url": "https://sysarchn8n.duckdns.org/webhook/whieda-broadcast-delivery-v1", "sendBody": True, "specifyBody": "json", "jsonBody": "={{ { draft_id: $json.draft_id } }}", "options": {}}, "id": "meeting-dispatch", "name": "HTTP: Trigger Broadcast Delivery", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.4, "position": [480, -80]},
        ],
        "connections": {
            "Webhook Trigger": {"main": [[{"node": "Respond: 200 ACK", "type": "main", "index": 0}]]},
            "Respond: 200 ACK": {"main": [[{"node": "Postgres: Create Due Reminder Drafts", "type": "main", "index": 0}]]},
            "Postgres: Create Due Reminder Drafts": {"main": [[{"node": "IF: Due Reminder Exists", "type": "main", "index": 0}]]},
            "IF: Due Reminder Exists": {"main": [[{"node": "HTTP: Trigger Broadcast Delivery", "type": "main", "index": 0}], []]},
        },
        "settings": {"executionOrder": "v1", "saveExecutionProgress": True},
    }


def ssh_run(command):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(SERVER_HOST, username=SERVER_USER, password=SERVER_PASSWORD, look_for_keys=False, allow_agent=False, timeout=30)
    try:
        _, out, err = client.exec_command(command, timeout=180)
        code = out.channel.recv_exit_status()
        if code:
            raise RuntimeError(err.read().decode("utf-8", errors="replace"))
        return out.read().decode("utf-8", errors="replace")
    finally:
        client.close()


def main():
    if not EMAIL or not PASSWORD or not SERVER_PASSWORD:
        raise RuntimeError("Set WHIEDA_N8N_EMAIL, WHIEDA_N8N_PASSWORD and WHIEDA_SSH_PASSWORD before publishing.")
    session = requests.Session()
    session.post(f"{BASE_URL}/rest/login", json={"emailOrLdapLoginId": EMAIL, "password": PASSWORD}, verify=False, timeout=30).raise_for_status()
    rows = session.get(f"{BASE_URL}/rest/workflows?limit=200", verify=False, timeout=30).json().get("data", [])
    existing = next((row for row in rows if row.get("name") == WORKFLOW_NAME), None)
    payload = workflow()
    if existing:
        workflow_id = existing["id"]
        session.patch(f"{BASE_URL}/rest/workflows/{workflow_id}", json={**payload, "id": workflow_id}, verify=False, timeout=60).raise_for_status()
    else:
        created = session.post(f"{BASE_URL}/rest/workflows", json=payload, verify=False, timeout=60)
        created.raise_for_status()
        workflow_id = created.json().get("id") or created.json().get("data", {}).get("id")
    session.post(f"{BASE_URL}/rest/workflows/{workflow_id}/activate", json={}, verify=False, timeout=30)
    ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={workflow_id}")
    ssh_run("docker restart n8n-n8n-1")
    time.sleep(8)
    cron_line = "*/5 * * * * curl -fsS -X POST https://sysarchn8n.duckdns.org/webhook/whieda-meeting-reminder-dispatch-v1 >/dev/null 2>&1 # whieda-meeting-reminders"
    ssh_run("(crontab -l 2>/dev/null | grep -v 'whieda-meeting-reminders'; echo '" + cron_line + "') | crontab -")
    test = requests.post(f"{BASE_URL}/webhook/{WEBHOOK_PATH}", verify=False, timeout=60)
    test.raise_for_status()
    print(json.dumps({"workflow_id": workflow_id, "webhook": f"{BASE_URL}/webhook/{WEBHOOK_PATH}", "test_status": test.status_code, "cron": "every 5 minutes"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
