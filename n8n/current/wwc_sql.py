"""SQL к runtime-базе Core через n8n — без SSH.

Зачем отдельный модуль. Прежний путь (`apply_sql_via_n8n` в
run_partners_ref_runtime_sync_2026-08-01.py) после REST-активации временного
workflow ещё дёргает `ssh_run('n8n publish:workflow')`. Проверено 14.09.2026
на боевом workflow: REST `activate` сам делает версию активной
(`activeVersionId` == `versionId`), SSH-шаг лишний. А SSH-пароль в окружении
к тому же устарел, и весь путь падал на нём.

Плюс прежний путь не умел читать: Respond отдавал статичный `{"status":"ok"}`.
Здесь Respond возвращает строки узла Postgres — SELECT работает.

Использование из кода:
    from wwc_sql import run_sql
    rows = run_sql("select ref_code, owner_id from referral_profiles limit 5")

Из консоли:
    python wwc_sql.py "select count(*) from website_leads"
    python wwc_sql.py --file query.sql
"""
from __future__ import annotations

import importlib.util
import os
import json
import sys
import time
import uuid
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent


def _helper():
    spec = importlib.util.spec_from_file_location("whieda_sync", BASE / "publish_and_run_whieda_sync_2026-07-13.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


API_KEY = os.environ.get("WHIEDA_N8N_API_KEY", "")


def n8n_api(helper):
    """Public API n8n (/api/v1, заголовок X-N8N-API-KEY). Ключ — user-level env
    WHIEDA_N8N_API_KEY (создан владельцем 16.09.2026 при security-hardening),
    личный логин/пароль больше не нужны и не используются: он менялся и
    упирался в лимит 429."""
    if not API_KEY:
        raise RuntimeError("нет WHIEDA_N8N_API_KEY в окружении")
    session = requests.Session()
    session.headers["X-N8N-API-KEY"] = API_KEY
    session.verify = False
    return session, f"{helper.BASE_URL}/api/v1"


def run_sql(sql: str, *, timeout: int = 90) -> list[dict]:
    helper = _helper()
    session, api = n8n_api(helper)
    base = helper.BASE_URL
    suffix = uuid.uuid4().hex[:10]
    path = f"wwc-sql-{suffix}"
    workflow = {
        "name": f"TEMP WWC SQL {suffix}",
        "active": False,
        "nodes": [
            {"parameters": {"httpMethod": "POST", "path": path, "responseMode": "responseNode", "options": {}},
             "id": "webhook", "name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [-200, 0]},
            {"parameters": {"jsCode": "const i=$input.first().json; const b=i.body&&typeof i.body==='object'?i.body:i; return [{json:b}];"},
             "id": "normalize", "name": "Code: payload", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [-20, 0]},
            {"parameters": {"operation": "executeQuery", "query": "={{ $json.sql }}", "options": {}},
             "id": "postgres", "name": "Postgres", "type": "n8n-nodes-base.postgres", "typeVersion": 2.6, "position": [180, 0],
             "credentials": {"postgres": helper.WORKFLOW_CREDENTIAL}},
            {"parameters": {"respondWith": "json",
                            "responseBody": "={{ JSON.stringify({ rows: $input.all().map(i => i.json) }) }}",
                            "options": {"responseCode": 200}},
             "id": "respond", "name": "Respond", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [380, 0]},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Code: payload", "type": "main", "index": 0}]]},
            "Code: payload": {"main": [[{"node": "Postgres", "type": "main", "index": 0}]]},
            "Postgres": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }
    workflow_id = None
    try:
        payload = {k: workflow[k] for k in ("name", "nodes", "connections", "settings")}
        created = session.post(f"{api}/workflows", json=payload, timeout=60)
        created.raise_for_status()
        workflow_id = created.json()["id"]
        act = session.post(f"{api}/workflows/{workflow_id}/activate", timeout=60)
        act.raise_for_status()
        url = f"{base}/webhook/{path}"
        last = None
        for attempt in range(8):
            if attempt:
                time.sleep(4)
            resp = requests.post(url, json={"sql": sql}, verify=False, timeout=timeout)
            if resp.status_code == 404:
                last = f"404 on attempt {attempt + 1}"
                continue
            if resp.status_code >= 500:
                last = f"{resp.status_code}: {resp.text[:300]}"
                break
            resp.raise_for_status()
            body = resp.json() if resp.text.strip() else {"rows": []}
            return body.get("rows", [])
        raise RuntimeError(f"webhook {path}: {last}")
    finally:
        if workflow_id:
            try:
                session.post(f"{api}/workflows/{workflow_id}/deactivate", timeout=30)
            except Exception:
                pass
            try:
                session.delete(f"{api}/workflows/{workflow_id}", timeout=30)
            except Exception:
                pass


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--file" in args:
        sql = Path(args[args.index("--file") + 1]).read_text(encoding="utf-8")
    else:
        sql = " ".join(args)
    print(json.dumps(run_sql(sql), ensure_ascii=False, indent=1, default=str))
