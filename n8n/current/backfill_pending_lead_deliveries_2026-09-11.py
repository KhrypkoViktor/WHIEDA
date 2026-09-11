"""Deliver website leads that sat in lead_delivery_attempts as 'pending'.

Why: partners without a Telegram chat id never received their leads (see
link_partner_telegram_chat_ids_2026-09-11.py). Now that the ids are in place,
send each undelivered lead once to its recipient and mark the attempts sent.

The text is the same one WWC Website Leads P0.3 sends for a fresh lead, so a
partner sees exactly what they would have seen on the day. One message per
(lead, recipient) even when several pending attempts exist for it.

Owner-named test leads are excluded on purpose; the list is explicit so the run
is auditable.

Usage:
    python backfill_pending_lead_deliveries_2026-09-11.py --print   # SQL only
    python backfill_pending_lead_deliveries_2026-09-11.py           # send
"""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parent
TENANT_ID = "whieda"

# Pending on 2026-09-11 and worth delivering (real customers or partners' own
# orders on their pages). Not listed = stays pending:
#   L-CFCA8F9835, L-BC3155546B, L-8DD8AC2792 — owner delivery tests, 2026-07-28
#   L-4C20D51B6C, L-56ECD666BF — "Platform Core Smoke", 2026-08-02
#   L-B2987D6E69 — makarova "Проверка доставки", 2026-08-29
#   L-0ABFCD949B — Claude's reproduction order, 2026-09-11
LEAD_PUBLIC_IDS: list[str] = [
    # ladnaya
    "L-81B408277E", "L-1B39323093", "L-96E7D1BC4C", "L-09C0189EAF",
    "L-627F823071", "L-5FD47C56F6", "L-ED81B9CDD7",
    # igoref
    "L-BFDB34475A", "L-8C17D4388C", "L-BA81A1DC3C", "L-A3FB324578", "L-EA2F6C7CE3",
    # fedorov
    "L-CCEEB03CF5", "L-A65AFC4E6F", "L-6833FD4E8D",
]

FORMAT_JS = r"""
return $input.all().map((i) => {
  const x = i.json;
  const note = x.comment ? ('Комментарий: ' + x.comment + '\n') : '';
  const role = x.kind === 'watcher' ? 'Копия: уведомление по заявке.' : 'Ваша заявка.';
  return { json: { ...x, text: 'Новая заявка <b>' + x.public_id + '</b>\nТовар: ' + x.product_name + '\nИмя: ' + x.name + '\nКонтакт: ' + x.contact + '\n' + note + 'Ref: ' + (x.first_ref_code || '—') + '\n' + role } };
});
"""

# Runs once per Telegram result; the lead fields come from the paired item of
# the format node because the HTTP node replaces the item json with the reply.
MARK_SQL = (
    "={{ (() => {\n"
    "  const src = $('Code: format backfill').item.json;\n"
    "  const q = (v) => String(v ?? '').replace(/'/g, \"''\");\n"
    "  const messageId = $json.result && $json.result.message_id ? String($json.result.message_id) : '';\n"
    "  if (!messageId) {\n"
    "    return `select '${q(src.public_id)}' as public_id, '${q(src.recipient_actor_id)}' as recipient, 'telegram_failed' as status, 0 as attempts_marked`;\n"
    "  }\n"
    "  return `with upd as (\n"
    "    update lead_delivery_attempts set status='sent', sent_at=now(), telegram_message_id='${q(messageId)}', error_text=null\n"
    "     where lead_id='${q(src.lead_id)}'::uuid and recipient_actor_id='${q(src.recipient_actor_id)}' and status='pending'\n"
    "    returning delivery_id\n"
    "  ), lead_upd as (\n"
    "    update website_leads set last_touch_at=now(),\n"
    "      delivery_status = case when exists (\n"
    "        select 1 from lead_delivery_attempts x\n"
    "         where x.lead_id=website_leads.lead_id and x.status='pending' and x.recipient_actor_id <> '${q(src.recipient_actor_id)}'\n"
    "      ) then 'pending' else 'sent' end\n"
    "     where lead_id='${q(src.lead_id)}'::uuid\n"
    "    returning lead_id\n"
    "  )\n"
    "  select '${q(src.public_id)}' as public_id, '${q(src.recipient_actor_id)}' as recipient, 'sent' as status, (select count(*) from upd) as attempts_marked`;\n"
    "})() }}"
)


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_select_sql() -> str:
    ids = ", ".join(sql_literal(public_id) for public_id in LEAD_PUBLIC_IDS)
    return f"""
select distinct on (l.lead_id, d.recipient_actor_id)
       l.lead_id::text, l.public_id, l.product_name, l.name, l.contact, l.comment,
       l.first_ref_code, d.recipient_actor_id, a.telegram_chat_id,
       case when d.recipient_actor_id = l.assigned_owner_id then 'owner' else 'watcher' end as kind,
       l.created_at
  from website_leads l
  join lead_delivery_attempts d on d.lead_id = l.lead_id and d.status = 'pending'
  join lead_actors a on a.actor_id = d.recipient_actor_id and a.tenant_id = l.tenant_id
 where l.tenant_id = {sql_literal(TENANT_ID)}
   and l.deleted_at is null
   and a.active
   and coalesce(a.telegram_chat_id, '') <> ''
   and l.public_id in ({ids})
 order by l.lead_id, d.recipient_actor_id, l.created_at;
"""


def build_workflow(helper, path: str) -> dict:
    credential = {"postgres": helper.WORKFLOW_CREDENTIAL}
    return {
        "name": f"TEMP WWC pending lead delivery backfill {path[-8:]}",
        "active": False,
        "nodes": [
            {
                "parameters": {"httpMethod": "POST", "path": path, "responseMode": "responseNode", "options": {}},
                "id": "webhook", "name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [-400, 0],
            },
            {
                "parameters": {"operation": "executeQuery", "query": build_select_sql(), "options": {}},
                "id": "select", "name": "Postgres: pending deliveries", "type": "n8n-nodes-base.postgres", "typeVersion": 2.6,
                "position": [-180, 0], "credentials": credential,
            },
            {
                "parameters": {"jsCode": FORMAT_JS},
                "id": "format", "name": "Code: format backfill", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [40, 0],
            },
            {
                "parameters": {
                    "method": "POST", "specifyBody": "json", "sendBody": True,
                    "url": "={{ 'https://api.telegram.org/bot' + $env.WHIEDA_ADVISOR_TELEGRAM_BOT_TOKEN + '/sendMessage' }}",
                    "jsonBody": "={{ {chat_id: $json.telegram_chat_id, text: $json.text, parse_mode: 'HTML'} }}",
                    "options": {},
                },
                "id": "telegram", "name": "Telegram: send backfill", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
                "position": [260, 0], "onError": "continueRegularOutput",
            },
            {
                "parameters": {"operation": "executeQuery", "query": MARK_SQL, "options": {}},
                "id": "mark", "name": "Postgres: mark sent", "type": "n8n-nodes-base.postgres", "typeVersion": 2.6,
                "position": [480, 0], "credentials": credential,
            },
            {
                "parameters": {"respondWith": "allIncomingItems", "options": {"responseCode": 200}},
                "id": "respond", "name": "Respond", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [700, 0],
            },
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Postgres: pending deliveries", "type": "main", "index": 0}]]},
            "Postgres: pending deliveries": {"main": [[{"node": "Code: format backfill", "type": "main", "index": 0}]]},
            "Code: format backfill": {"main": [[{"node": "Telegram: send backfill", "type": "main", "index": 0}]]},
            "Telegram: send backfill": {"main": [[{"node": "Postgres: mark sent", "type": "main", "index": 0}]]},
            "Postgres: mark sent": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }


def main() -> None:
    if "--print" in sys.argv[1:]:
        print(build_select_sql())
        return
    helper = load_module("whieda_sync", "publish_and_run_whieda_sync_2026-07-13.py")
    partners_mod = load_module("partners_upd", "update_partners_sheet_subdomains_2026-08-01.py")
    session = helper.login_session()
    path = f"wwc-lead-backfill-{uuid.uuid4().hex[:10]}"
    workflow = build_workflow(helper, path)
    response = partners_mod.call_temp_webhook(helper, session, workflow, path, payload={})
    try:
        result = response.json()
    except ValueError:
        result = {"raw": response.text[:800]}
    print(json.dumps({"leads": LEAD_PUBLIC_IDS, "result": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
