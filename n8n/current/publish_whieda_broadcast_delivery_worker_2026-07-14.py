"""Publish the isolated WHIEDA broadcast delivery worker.

The worker is intentionally separate from the advisor workflow: ordinary product
answers must not depend on a long-running Telegram fan-out.
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
WORKFLOW_NAME = "WHIEDA Broadcast Delivery Worker"
WEBHOOK_PATH = "whieda-broadcast-delivery-v1"
POSTGRES_CREDENTIAL = {"id": "RmjHh3rdZri7axzq", "name": "advisor-dev-postgres"}

QUEUE_SQL = """CREATE TABLE IF NOT EXISTS advisor_broadcast_deliveries (
  client_id text NOT NULL,
  draft_id text NOT NULL,
  private_chat_id text NOT NULL,
  telegram_user_id text NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  queued_at timestamptz NOT NULL DEFAULT now(),
  sent_at timestamptz,
  failed_at timestamptz,
  error_text text,
  PRIMARY KEY (client_id, draft_id, private_chat_id)
);

WITH draft AS (
  SELECT d.client_id, d.draft_id, d.text_body, d.audience, COALESCE(d.structure_code, 'general') AS structure_code
  FROM advisor_broadcast_drafts d
  WHERE d.client_id = 'whieda'
    AND d.draft_id = '{{ String($json.body?.draft_id ?? '').replace(/'/g, '') }}'
    AND d.status = 'confirmed'
), recipients AS (
  SELECT d.client_id, d.draft_id, d.text_body, s.private_chat_id, s.telegram_user_id
  FROM draft d
  JOIN advisor_telegram_subscriptions s ON s.client_id = d.client_id
  JOIN advisor_structured_users_access a ON a.client_id = s.client_id AND a.telegram_user_id = s.telegram_user_id
  WHERE s.is_subscribed IS TRUE AND s.blocked_at IS NULL
    AND COALESCE(a.structure_code, 'general') = ANY(string_to_array(d.structure_code, '|'))
    AND ((d.audience = 'candidates' AND lower(COALESCE(a.access_status, '')) = 'candidate')
      OR (d.audience = 'partners' AND lower(COALESCE(a.access_status, '')) = 'approved' AND lower(COALESCE(a.role, 'partner')) = 'partner')
      OR (d.audience = 'leaders' AND lower(COALESCE(a.access_status, '')) = 'approved' AND lower(COALESCE(a.role, '')) IN ('leader', 'admin', 'super_admin')))
), queued AS (
  INSERT INTO advisor_broadcast_deliveries (client_id, draft_id, private_chat_id, telegram_user_id)
  SELECT client_id, draft_id, private_chat_id, telegram_user_id FROM recipients
  ON CONFLICT DO NOTHING
  RETURNING client_id, draft_id, private_chat_id
)
SELECT r.client_id, r.draft_id, r.private_chat_id, r.telegram_user_id, r.text_body
FROM recipients r JOIN queued q USING (client_id, draft_id, private_chat_id);"""

SENT_SQL = """UPDATE advisor_broadcast_deliveries
SET status = 'sent', sent_at = now(), error_text = NULL
WHERE client_id = '{{ String($('Postgres: Queue Broadcast Deliveries').item.json.client_id).replace(/'/g, '') }}'
  AND draft_id = '{{ String($('Postgres: Queue Broadcast Deliveries').item.json.draft_id).replace(/'/g, '') }}'
  AND private_chat_id = '{{ String($('Postgres: Queue Broadcast Deliveries').item.json.private_chat_id).replace(/'/g, '') }}';"""

def workflow():
    return {"name": WORKFLOW_NAME, "active": True, "nodes": [
      {"parameters":{"httpMethod":"POST","path":WEBHOOK_PATH,"responseMode":"responseNode","options":{}},"id":"delivery-hook","name":"Webhook Trigger","type":"n8n-nodes-base.webhook","typeVersion":1,"position":[-420,0]},
      {"parameters":{"respondWith":"json","responseBody":"{ \"status\": \"accepted\" }","options":{"responseCode":200}},"id":"delivery-ack","name":"Respond: 200 ACK","type":"n8n-nodes-base.respondToWebhook","typeVersion":1,"position":[-200,-80]},
      {"parameters":{"operation":"executeQuery","query":QUEUE_SQL,"options":{}},"id":"delivery-queue","name":"Postgres: Queue Broadcast Deliveries","type":"n8n-nodes-base.postgres","typeVersion":2.6,"position":[0,0],"credentials":{"postgres":POSTGRES_CREDENTIAL}},
      {"parameters":{"conditions":{"options":{"caseSensitive":True,"leftValue":"","typeValidation":"strict"},"combinator":"and","conditions":[{"id":"recipient","operator":{"type":"string","operation":"notEmpty"},"leftValue":"={{ String($json.private_chat_id || '') }}","rightValue":""},{"id":"text","operator":{"type":"string","operation":"notEmpty"},"leftValue":"={{ String($json.text_body || '') }}","rightValue":""}]},"options":{}},"id":"delivery-has-recipient","name":"IF: Delivery Has Recipient","type":"n8n-nodes-base.if","typeVersion":2,"position":[240,0]},
      {"parameters":{"method":"POST","url":"={{ 'https://api.telegram.org/bot' + $env.WHIEDA_ADVISOR_TELEGRAM_BOT_TOKEN + '/sendMessage' }}","sendBody":True,"specifyBody":"json","jsonBody":"={{ { chat_id: $json.private_chat_id, text: $json.text_body, disable_web_page_preview: true, parse_mode: 'HTML' } }}","options":{}},"id":"delivery-send","name":"Telegram: Send Broadcast","type":"n8n-nodes-base.httpRequest","typeVersion":4.4,"position":[480,-80]},
      {"parameters":{"operation":"executeQuery","query":SENT_SQL,"options":{}},"id":"delivery-sent","name":"Postgres: Mark Broadcast Sent","type":"n8n-nodes-base.postgres","typeVersion":2.6,"position":[720,-80],"credentials":{"postgres":POSTGRES_CREDENTIAL}}
    ],"connections":{"Webhook Trigger":{"main":[[{"node":"Respond: 200 ACK","type":"main","index":0}]]},"Respond: 200 ACK":{"main":[[{"node":"Postgres: Queue Broadcast Deliveries","type":"main","index":0}]]},"Postgres: Queue Broadcast Deliveries":{"main":[[{"node":"IF: Delivery Has Recipient","type":"main","index":0}]]},"IF: Delivery Has Recipient":{"main":[[{"node":"Telegram: Send Broadcast","type":"main","index":0}],[]]},"Telegram: Send Broadcast":{"main":[[{"node":"Postgres: Mark Broadcast Sent","type":"main","index":0}]]}},"settings":{"executionOrder":"v1","saveExecutionProgress":True}}

def ssh_run(command):
    client=paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(SERVER_HOST,username=SERVER_USER,password=SERVER_PASSWORD,look_for_keys=False,allow_agent=False,timeout=30)
    try:
        _,out,err=client.exec_command(command,timeout=180); code=out.channel.recv_exit_status()
        if code: raise RuntimeError(err.read().decode())
    finally: client.close()

def main():
    if not EMAIL or not PASSWORD or not SERVER_PASSWORD:
        raise RuntimeError("Set WHIEDA_N8N_EMAIL, WHIEDA_N8N_PASSWORD and WHIEDA_SSH_PASSWORD before publishing.")
    s=requests.Session(); s.post(f"{BASE_URL}/rest/login",json={"emailOrLdapLoginId":EMAIL,"password":PASSWORD},verify=False,timeout=30).raise_for_status()
    items=s.get(f"{BASE_URL}/rest/workflows?limit=200",verify=False,timeout=30).json().get("data",[])
    found=next((x for x in items if x.get("name")==WORKFLOW_NAME),None); payload=workflow()
    if found:
        wid=found["id"]; s.patch(f"{BASE_URL}/rest/workflows/{wid}",json={**payload,"id":wid},verify=False,timeout=60).raise_for_status()
    else:
        created=s.post(f"{BASE_URL}/rest/workflows",json=payload,verify=False,timeout=60)
        if not created.ok:
            raise RuntimeError(f"Workflow create failed: {created.status_code} {created.text[:1000]}")
        created_data=created.json(); wid=created_data.get("id") or created_data.get("data",{}).get("id")
        if not wid: raise RuntimeError(f"Workflow create returned no id: {created_data}")
    s.post(f"{BASE_URL}/rest/workflows/{wid}/activate",json={},verify=False,timeout=30)
    ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={wid}"); ssh_run("docker restart n8n-n8n-1"); time.sleep(8)
    print(json.dumps({"workflow_id":wid,"webhook":f"{BASE_URL}/webhook/{WEBHOOK_PATH}"}))

if __name__ == "__main__": main()
