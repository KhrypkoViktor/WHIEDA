"""Google Sheets API для таблицы WWC — через n8n, тем же ключом, что wwc_sql.

Зачем. Учёт партнёров ведётся в Google-таблице, а источник правды — Postgres
Core. Чтобы таблица заполнялась сама, нужен доступ к Sheets API; свой OAuth у
агентов нет и не нужен — в n8n уже есть credential «Google Sheets account»
(id XnF6UcslXgxuXbGm, тот же, что у update_partners_sheet_subdomains_2026-08-01.py).
Здесь временный workflow: Webhook → HTTP Request (predefined credential) →
Respond. Секретов на стороне агента нет.

    from wwc_sheets import sheets_get, values_update, values_clear, batch_update
    tabs = sheets_get("sheets.properties")["sheets"]
    values_update("'Партнёры'!A1", [["Имя", "ref"], ["Виктор", "dev"]])
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from typing import Any

import requests

from wwc_sql import _cleanup_stale, _helper, _last_execution_error, n8n_api

SHEET_ID = "1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4"
CREDENTIAL = {"googleSheetsOAuth2Api": {"id": "XnF6UcslXgxuXbGm", "name": "Google Sheets account"}}
API = "https://sheets.googleapis.com/v4/spreadsheets"
TEMP_PREFIX = "TEMP WWC SQL sheets-"


def _call(method: str, url: str, body: dict | None = None, *, timeout: int = 90) -> dict[str, Any]:
    """Один вызов Sheets API через временный workflow n8n."""
    helper = _helper()
    session, api = n8n_api(helper)
    _cleanup_stale(session, api)
    suffix = uuid.uuid4().hex[:10]
    path = f"wwc-sheets-{suffix}"
    http = {
        "method": method,
        "url": url,
        "authentication": "predefinedCredentialType",
        "nodeCredentialType": "googleSheetsOAuth2Api",
        "options": {"response": {"response": {"fullResponse": False, "neverError": True}}},
    }
    if body is not None:
        http.update({"sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json.body) }}"})
    workflow = {
        "name": f"{TEMP_PREFIX}{suffix}",
        "nodes": [
            {"parameters": {"httpMethod": "POST", "path": path, "responseMode": "responseNode", "options": {}},
             "id": "webhook", "name": "Webhook", "type": "n8n-nodes-base.webhook", "typeVersion": 2, "position": [-200, 0]},
            {"parameters": {"jsCode": "const i=$input.first().json; const b=i.body&&typeof i.body==='object'?i.body:i; return [{json:b}];"},
             "id": "normalize", "name": "Code: payload", "type": "n8n-nodes-base.code", "typeVersion": 2, "position": [-20, 0]},
            {"parameters": http, "id": "http", "name": "Sheets API", "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2,
             "position": [180, 0], "credentials": CREDENTIAL},
            {"parameters": {"respondWith": "json", "responseBody": "={{ JSON.stringify($input.first().json) }}", "options": {"responseCode": 200}},
             "id": "respond", "name": "Respond", "type": "n8n-nodes-base.respondToWebhook", "typeVersion": 1.1, "position": [380, 0]},
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Code: payload", "type": "main", "index": 0}]]},
            "Code: payload": {"main": [[{"node": "Sheets API", "type": "main", "index": 0}]]},
            "Sheets API": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }
    workflow_id = None
    try:
        created = session.post(f"{api}/workflows", json=workflow, timeout=60)
        created.raise_for_status()
        workflow_id = created.json()["id"]
        session.post(f"{api}/workflows/{workflow_id}/activate", timeout=60).raise_for_status()
        hook = f"{helper.BASE_URL}/webhook/{path}"
        last = None
        for attempt in range(20):
            if attempt:
                time.sleep(3)
            try:
                resp = requests.post(hook, json={"body": body or {}}, verify=False, timeout=timeout)
            except requests.RequestException as exc:
                last = str(exc)
                continue
            if resp.status_code == 404:
                last = "404"
                continue
            if not resp.text.strip():
                raise RuntimeError("Sheets API: " + _last_execution_error(session, api, workflow_id))
            data = resp.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(f"Sheets API: {json.dumps(data['error'], ensure_ascii=False)[:400]}")
            return data
        raise RuntimeError(f"webhook {path}: {last}")
    finally:
        if workflow_id:
            for action in ("deactivate", None):
                try:
                    if action:
                        session.post(f"{api}/workflows/{workflow_id}/{action}", timeout=30)
                    else:
                        session.delete(f"{api}/workflows/{workflow_id}", timeout=30)
                except Exception:
                    pass


def sheets_get(fields: str = "sheets.properties", sheet_id: str = SHEET_ID) -> dict[str, Any]:
    return _call("GET", f"{API}/{sheet_id}?fields={requests.utils.quote(fields)}")


def batch_update(requests_: list[dict], sheet_id: str = SHEET_ID) -> dict[str, Any]:
    return _call("POST", f"{API}/{sheet_id}:batchUpdate", {"requests": requests_})


def values_update(range_a1: str, values: list[list[Any]], sheet_id: str = SHEET_ID) -> dict[str, Any]:
    url = f"{API}/{sheet_id}/values/{requests.utils.quote(range_a1, safe='')}?valueInputOption=USER_ENTERED"
    return _call("PUT", url, {"range": range_a1, "majorDimension": "ROWS", "values": values})


def values_clear(range_a1: str, sheet_id: str = SHEET_ID) -> dict[str, Any]:
    return _call("POST", f"{API}/{sheet_id}/values/{requests.utils.quote(range_a1, safe='')}:clear", {})


def values_get(range_a1: str, sheet_id: str = SHEET_ID) -> list[list[Any]]:
    return _call("GET", f"{API}/{sheet_id}/values/{requests.utils.quote(range_a1, safe='')}").get("values", [])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    for sheet in sheets_get()["sheets"]:
        p = sheet["properties"]
        print(p["sheetId"], p["title"], p.get("hidden", False))
