"""Постоянный workflow n8n «WWC учёт: Core → таблица» (19.09.2026).

Что делает. Раз в час (и по кнопке «Execute» в n8n) читает три запроса из
sheet_sync_queries.sql (Партнёры / Платежи / Бонусы) из Postgres Core и
переписывает одноимённые вкладки таблицы WWC целиком: очистка → шапка →
строки. Руки в эти вкладки не лезут; заметки владельца живут в отдельной
вкладке «Заметки» (ref → текст), синк её не трогает.

Почему HTTP Request, а не узел Google Sheets. Узлу нужна OAuth-учётка
владельца, а её refresh-token у Google протухает (приложение в статусе
Testing — 7 дней). Сервисный аккаунт (credential n8n типа googleApi) живёт
бессрочно; HTTP Request с predefinedCredentialType=googleApi подписывает
запросы им. Таблица должна быть расшарена на e-mail сервисного аккаунта
(редактор). Ключ сервисного аккаунта агенты не видят: он вставляется в n8n
владельцем; здесь только id credential.

Запуск:
    python publish_core_sheet_sync.py --credential-id <id googleApi> [--activate]
    python publish_core_sheet_sync.py --credential-id <id> --run-now   # разовый прогон через webhook

Найти id: ssh whieda-n8n 'docker exec n8n-postgres-1 psql -U n8n -d n8n -At -c
  "select id,name,type from credentials_entity where type=''googleApi''"'
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path

import requests

from wwc_sql import _helper, n8n_api

BASE = Path(__file__).resolve().parent
SHEET_ID = "1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4"
WORKFLOW_NAME = "WWC учёт: Core → таблица (Партнёры / Платежи / Бонусы)"
WEBHOOK_PATH = "wwc-sheet-sync-run"
SHEETS = "https://sheets.googleapis.com/v4/spreadsheets"


def load_queries() -> list[tuple[str, str]]:
    text = (BASE / "sheet_sync_queries.sql").read_text(encoding="utf-8")
    parts = re.split(r"^-- @tab (.+)$", text, flags=re.M)
    return [(parts[i].strip(), parts[i + 1].strip()) for i in range(1, len(parts), 2)]


# JS для узла Code: строки Postgres → values для Sheets (шапка + строки), плюс
# batchUpdate-запрос, который создаёт вкладку, если её ещё нет.
_FORMAT_JS = r"""
const tab = $('Config').first().json.tab;
const rows = $input.all().map(i => i.json);
const header = rows.length ? Object.keys(rows[0]) : [];
const values = [header].concat(rows.map(r => header.map(h => r[h] === null || r[h] === undefined ? '' : r[h])));
return [{ json: { tab, values, count: rows.length } }];
"""

_ENSURE_TAB_JS = r"""
// Вкладка есть? Если нет — создаём. Ответ spreadsheets.get приходит из узла «Sheet meta».
const tab = $('Format').first().json.tab;
const meta = $input.first().json;
const exists = (meta.sheets || []).some(s => s.properties && s.properties.title === tab);
return [{ json: { tab, exists, body: exists ? { requests: [] } : { requests: [{ addSheet: { properties: { title: tab, gridProperties: { frozenRowCount: 1 } } } }] } } }];
"""


def _http(name: str, method: str, url: str, credential_id: str, *, body_expr: str | None = None, pos: tuple[int, int]) -> dict:
    params = {
        "method": method,
        "url": url,
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "googleApi",
        "options": {},
    }
    if body_expr:
        params.update({"sendBody": True, "specifyBody": "json", "jsonBody": body_expr})
    return {
        "parameters": params, "id": re.sub(r"\W+", "-", name.lower()), "name": name,
        "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": list(pos),
        "credentials": {"googleApi": {"id": credential_id, "name": "WWC Sheets service account"}},
    }


def build_workflow(credential_id: str, helper) -> dict:
    nodes: list[dict] = [
        {"parameters": {"rule": {"interval": [{"field": "hours", "hoursInterval": 1}]}},
         "id": "cron", "name": "Каждый час", "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2, "position": [-600, 0]},
        {"parameters": {"httpMethod": "POST", "path": WEBHOOK_PATH, "options": {}},
         "id": "hook", "name": "Запуск по webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [-600, 200]},
    ]
    connections: dict = {"Каждый час": {"main": [[]]}, "Запуск по webhook": {"main": [[]]}}
    y = 0
    for tab, sql in load_queries():
        sfx = {"Партнёры": "p", "Платежи": "pay", "Бонусы": "b"}.get(tab, re.sub(r"\W+", "", tab)[:6])
        cfg, pg, fmt, meta, ensure, addsheet, clear, put = (
            f"Config {sfx}", f"Postgres {sfx}", f"Format {sfx}", f"Sheet meta {sfx}", f"Ensure tab {sfx}",
            f"Add tab {sfx}", f"Clear {sfx}", f"Write {sfx}",
        )
        rng = f"'{tab}'!A1:Z10000"
        quoted = requests.utils.quote(rng, safe="")
        nodes += [
            {"parameters": {"jsCode": f"return [{{ json: {{ tab: {json.dumps(tab, ensure_ascii=False)} }} }}];"},
             "id": f"cfg-{sfx}", "name": cfg, "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [-380, y]},
            {"parameters": {"operation": "executeQuery", "query": sql, "options": {}},
             "id": f"pg-{sfx}", "name": pg, "type": "n8n-nodes-base.postgres", "typeVersion": 2.6, "position": [-160, y],
             "credentials": {"postgres": helper.WORKFLOW_CREDENTIAL}},
            {"parameters": {"jsCode": _FORMAT_JS.replace("$('Config')", f"$({json.dumps(cfg, ensure_ascii=False)})")},
             "id": f"fmt-{sfx}", "name": fmt, "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [60, y]},
            _http(meta, "GET", f"{SHEETS}/{SHEET_ID}?fields=sheets.properties.title", credential_id, pos=(280, y)),
            {"parameters": {"jsCode": _ENSURE_TAB_JS.replace("$('Format')", f"$({json.dumps(fmt, ensure_ascii=False)})")},
             "id": f"ens-{sfx}", "name": ensure, "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [500, y]},
            _http(addsheet, "POST", f"{SHEETS}/{SHEET_ID}:batchUpdate", credential_id, body_expr="={{ JSON.stringify($json.body) }}", pos=(720, y)),
            _http(clear, "POST", f"{SHEETS}/{SHEET_ID}/values/{quoted}:clear", credential_id, body_expr="={{ '{}' }}", pos=(940, y)),
            _http(put, "PUT", f"{SHEETS}/{SHEET_ID}/values/{quoted}?valueInputOption=USER_ENTERED", credential_id,
                  body_expr=f"={{{{ JSON.stringify({{ range: {json.dumps(rng, ensure_ascii=False)}, majorDimension: 'ROWS', values: $({json.dumps(fmt, ensure_ascii=False)}).first().json.values }}) }}}}",
                  pos=(1160, y)),
        ]
        connections["Каждый час"]["main"][0].append({"node": cfg, "type": "main", "index": 0})
        connections["Запуск по webhook"]["main"][0].append({"node": cfg, "type": "main", "index": 0})
        chain = [cfg, pg, fmt, meta, ensure, addsheet, clear, put]
        for a, b in zip(chain, chain[1:]):
            connections[a] = {"main": [[{"node": b, "type": "main", "index": 0}]]}
        y += 260
    return {"name": WORKFLOW_NAME, "nodes": nodes, "connections": connections,
            "settings": {"executionOrder": "v1", "timezone": "Europe/Moscow"}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--credential-id", required=True)
    ap.add_argument("--activate", action="store_true")
    ap.add_argument("--run-now", action="store_true")
    args = ap.parse_args()
    helper = _helper()
    session, api = n8n_api(helper)
    payload = build_workflow(args.credential_id, helper)
    existing = [w for w in session.get(f"{api}/workflows", params={"limit": 200}, timeout=60).json().get("data", []) if w["name"] == WORKFLOW_NAME]
    if existing:
        wid = existing[0]["id"]
        session.put(f"{api}/workflows/{wid}", json=payload, timeout=60).raise_for_status()
        print("updated", wid)
    else:
        resp = session.post(f"{api}/workflows", json=payload, timeout=60)
        resp.raise_for_status()
        wid = resp.json()["id"]
        print("created", wid)
    if args.activate or args.run_now:
        session.post(f"{api}/workflows/{wid}/activate", timeout=60).raise_for_status()
        print("active")
    if args.run_now:
        time.sleep(4)
        r = requests.post(f"{helper.BASE_URL}/webhook/{WEBHOOK_PATH}", json={"run": str(uuid.uuid4())}, verify=False, timeout=180)
        print("run:", r.status_code, r.text[:200])
        time.sleep(20)
        ex = session.get(f"{api}/executions", params={"workflowId": wid, "limit": 1, "includeData": "true"}, timeout=60).json()
        for e in ex.get("data", []):
            err = e.get("data", {}).get("resultData", {}).get("error") or {}
            print("execution:", e.get("status"), "|", err.get("message", "ok"))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
