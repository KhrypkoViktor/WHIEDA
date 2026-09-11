"""WWC Website Leads: answer the site first, deliver to Telegram second.

Before this patch the delivery branch (prepare -> format -> Telegram -> mark)
ran before "Respond: lead accepted" (n8n v1 order goes by canvas position).
A partner without a chat id made the Postgres node emit an empty item, the
Telegram call failed with "chat_id is empty", the execution died and the site
got an empty 200 — "Не удалось отправить заявку" for a lead that was saved
(executions 29672, 29675, 29685 on 2026-09-11).

After the patch the chain is linear:
  save -> Respond 201 -> prepare -> IF has chat -> format -> Telegram -> mark
- a recipient without a chat id is skipped, the attempt stays pending;
- a Telegram failure no longer aborts the run: error_text is recorded on the
  attempt, the site already has its 201.

Usage:
    python patch_wwc_website_leads_respond_first_2026-09-11.py --dry-run
    python patch_wwc_website_leads_respond_first_2026-09-11.py
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
WORKFLOW_ID = "wwc-website-leads-p0"

SAVE = "Postgres: save lead and audit"
RESPOND_OK = "Respond: lead accepted"
PREPARE = "Postgres: prepare lead delivery"
HAS_CHAT = "IF: has recipient chat"
FORMAT = "Code: format lead delivery"
TELEGRAM = "Telegram: send lead delivery"
MARK = "Postgres: mark lead delivery sent"

MARK_QUERY = (
    "={{ (() => {\n"
    "  const src = $('" + FORMAT + "').item.json;\n"
    "  const q = (v) => String(v ?? '').replace(/'/g, \"''\");\n"
    "  const messageId = $json.result && $json.result.message_id ? String($json.result.message_id) : '';\n"
    "  if (messageId) {\n"
    "    return `update lead_delivery_attempts set status='sent', sent_at=now(), telegram_message_id='${q(messageId)}', error_text=null where delivery_id='${q(src.delivery_id)}' and status='pending'`;\n"
    "  }\n"
    "  const raw = $json.error;\n"
    "  const err = !raw ? 'telegram_no_result' : (typeof raw === 'string' ? raw : (raw.message || JSON.stringify(raw)));\n"
    "  return `update lead_delivery_attempts set error_text=left('${q(err)}', 500) where delivery_id='${q(src.delivery_id)}' and status='pending'`;\n"
    "})() }}"
)


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BASE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def node(workflow: dict, name: str) -> dict:
    for item in workflow["nodes"]:
        if item["name"] == name:
            return item
    raise KeyError(name)


def link(name: str) -> dict:
    return {"node": name, "type": "main", "index": 0}


def patch_workflow(workflow: dict) -> dict:
    wf = copy.deepcopy(workflow)
    names = {n["name"] for n in wf["nodes"]}
    for required in (SAVE, RESPOND_OK, PREPARE, FORMAT, TELEGRAM, MARK):
        if required not in names:
            raise RuntimeError(f"node missing, refusing to patch: {required}")
    if HAS_CHAT in names:
        raise RuntimeError("already patched: IF node present")

    # One straight line, left to right: position no longer decides the order.
    node(wf, RESPOND_OK)["position"] = [340, -80]
    node(wf, PREPARE)["position"] = [580, -80]
    node(wf, FORMAT)["position"] = [1040, -80]
    node(wf, TELEGRAM)["position"] = [1280, -80]
    node(wf, MARK)["position"] = [1520, -80]

    wf["nodes"].append({
        "parameters": {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                "combinator": "and",
                "conditions": [{
                    "id": "recipient-chat",
                    "operator": {"type": "string", "operation": "notEmpty"},
                    "leftValue": "={{ String($json.telegram_chat_id || '') }}",
                    "rightValue": "",
                }],
            },
            "options": {},
        },
        "id": "d005-has-recipient-chat",
        "name": HAS_CHAT,
        "type": "n8n-nodes-base.if",
        "typeVersion": 2,
        "position": [810, -80],
    })

    telegram = node(wf, TELEGRAM)
    telegram["onError"] = "continueRegularOutput"
    node(wf, MARK)["parameters"]["query"] = MARK_QUERY

    connections = wf["connections"]
    connections[SAVE] = {"main": [[link(RESPOND_OK)]]}
    connections[RESPOND_OK] = {"main": [[link(PREPARE)]]}
    connections[PREPARE] = {"main": [[link(HAS_CHAT)]]}
    connections[HAS_CHAT] = {"main": [[link(FORMAT)], []]}
    connections[FORMAT] = {"main": [[link(TELEGRAM)]]}
    connections[TELEGRAM] = {"main": [[link(MARK)]]}
    return wf


def main() -> None:
    dry_run = "--dry-run" in sys.argv[1:]
    helper = load_module("whieda_sync", "publish_and_run_whieda_sync_2026-07-13.py")
    session = helper.login_session()
    base = helper.BASE_URL

    current = session.get(f"{base}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=30)
    current.raise_for_status()
    live = current.json()["data"]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = BASE.parent / "backups"
    backup_path = backup_dir / f"wwc-website-leads-p0-before-respond-first-{stamp}.json"
    backup_path.write_text(json.dumps(live, ensure_ascii=False, indent=2), encoding="utf-8")

    patched = patch_workflow(live)
    order = []
    cursor = SAVE
    while cursor:
        order.append(cursor)
        outs = patched["connections"].get(cursor, {}).get("main", [[]])
        cursor = outs[0][0]["node"] if outs and outs[0] else None
    summary = {"backup": str(backup_path), "chain": order, "dry_run": dry_run}
    if dry_run:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return

    body = {
        "name": patched["name"],
        "nodes": patched["nodes"],
        "connections": patched["connections"],
        "settings": patched.get("settings") or {},
        "versionId": live.get("versionId"),
    }
    save = session.patch(f"{base}/rest/workflows/{WORKFLOW_ID}", json=body, verify=False, timeout=120)
    save.raise_for_status()
    saved = save.json().get("data", save.json())
    version_id = saved.get("versionId")
    if not version_id:
        refreshed = session.get(f"{base}/rest/workflows/{WORKFLOW_ID}", verify=False, timeout=30)
        refreshed.raise_for_status()
        version_id = refreshed.json()["data"].get("versionId")
    activation = session.post(
        f"{base}/rest/workflows/{WORKFLOW_ID}/activate",
        json={"versionId": version_id},
        verify=False,
        timeout=60,
    )
    activation.raise_for_status()
    helper.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={WORKFLOW_ID}")
    summary["version_id"] = version_id
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
