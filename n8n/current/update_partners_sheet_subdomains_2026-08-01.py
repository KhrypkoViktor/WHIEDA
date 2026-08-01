"""Add subdomain/focus-group columns and values to Structure Basic partners sheet."""

from __future__ import annotations

import importlib.util
import json
import time
import uuid
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
SHEET_ID = "1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4"
SHEET_GID = 1733124410
SHEET_TITLE = "Partners_Ref"
CREDENTIAL = {"googleSheetsOAuth2Api": {"id": "XnF6UcslXgxuXbGm", "name": "Google Sheets account"}}

NEW_HEADERS = ["public_site_url", "site_type", "focus_group", "access_tier"]

PARTNERS = {
    "nnm": {
        "public_site_url": "https://wwc.best/?ref=nnm",
        "site_type": "platform_root",
        "focus_group": "FALSE",
        "access_tier": "owner",
        "page_mode": "anonymous_ref",
        "notes": "Платформенный профиль. Анонимная ref-страница; заявки владельцу платформы.",
    },
    "ladnaya": {
        "public_site_url": "https://ladnaya.wwc.best/",
        "site_type": "subdomain_site",
        "focus_group": "TRUE",
        "access_tier": "test_pilot",
        "page_mode": "subdomain_site",
        "notes": "Именной поддомен live. Блок «Ваш консультант: Анна Ладная». Каталог ведёт на wwc.best/catalog с ref ladnaya. Фокус-группа, тестовый доступ; оплата позже (микротариф).",
    },
    "mariam": {
        "public_site_url": "https://mariam.wwc.best/",
        "site_type": "subdomain_site",
        "focus_group": "TRUE",
        "access_tier": "test_pilot",
        "page_mode": "subdomain_site",
        "notes": "Именной поддомен live. Блок консультанта Марьям. Фокус-группа, тестовый доступ.",
    },
    "harold": {
        "public_site_url": "https://wwc.best/?ref=harold",
        "site_type": "ref_query",
        "focus_group": "TRUE",
        "access_tier": "test_pilot",
        "page_mode": "standard_ref",
        "notes": "Пока ref-ссылка. Поддомен в очереди. Фокус-группа, тестовый доступ.",
    },
    "onlineelena": {
        "public_site_url": "https://wwc.best/?ref=onlineelena",
        "site_type": "ref_query",
        "focus_group": "TRUE",
        "access_tier": "test_pilot",
        "page_mode": "standard_ref",
        "notes": "Ref-ссылка. Фокус-группа, тестовый доступ.",
    },
    "olga-samtsova": {
        "public_site_url": "https://wwc.best/?ref=olga-samtsova",
        "site_type": "ref_query",
        "focus_group": "TRUE",
        "access_tier": "test_pilot",
        "page_mode": "standard_ref",
        "notes": "Ref-ссылка. Фокус-группа, тестовый доступ.",
    },
}


def load_helper():
    path = BASE / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_sync", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def append_columns_request(extra_columns: int) -> dict:
    return {
        "appendDimension": {
            "sheetId": SHEET_GID,
            "dimension": "COLUMNS",
            "length": extra_columns,
        }
    }


def call_temp_webhook(
    helper,
    session,
    workflow: dict,
    path: str,
    *,
    method: str = "POST",
    payload: dict | None = None,
    restart_n8n: bool = False,
) -> requests.Response:
    workflow_id = None
    try:
        created = session.post(f"{helper.BASE_URL}/rest/workflows", json=workflow, verify=False, timeout=60)
        created.raise_for_status()
        data = created.json().get("data", created.json())
        workflow_id = data["id"]
        version = data.get("versionId")
        activate = session.post(
            f"{helper.BASE_URL}/rest/workflows/{workflow_id}/activate",
            json={"versionId": version},
            verify=False,
            timeout=60,
        )
        if activate.status_code == 409:
            fresh = session.get(f"{helper.BASE_URL}/rest/workflows/{workflow_id}", verify=False, timeout=60).json()["data"]
            version = fresh.get("versionId")
            activate = session.post(
                f"{helper.BASE_URL}/rest/workflows/{workflow_id}/activate",
                json={"versionId": version},
                verify=False,
                timeout=60,
            )
        activate.raise_for_status()
        helper.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={workflow_id}")
        if restart_n8n:
            helper.ssh_run("docker restart n8n-n8n-1")
            helper.wait_for_n8n_ready()
        else:
            time.sleep(5)
        url = f"{helper.BASE_URL}/webhook/{path}"
        last_error = None
        for attempt in range(8):
            if attempt:
                time.sleep(8)
            try:
                if method.upper() == "GET":
                    response = requests.get(url, verify=False, timeout=90)
                else:
                    response = requests.post(url, json=payload or {}, verify=False, timeout=90)
                if response.status_code == 404:
                    last_error = f"404 on attempt {attempt + 1}"
                    continue
                response.raise_for_status()
                if response.text.strip():
                    return response
                last_error = f"empty body on attempt {attempt + 1}"
            except requests.HTTPError as exc:
                last_error = str(exc)
        raise RuntimeError(f"Webhook {path} not ready: {last_error}")
    finally:
        if workflow_id:
            session.delete(f"{helper.BASE_URL}/rest/workflows/{workflow_id}", verify=False, timeout=60)


def main() -> None:
    helper = load_helper()
    session = helper.login_session()

    sheet = requests.get(
        f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=tsv&gid={SHEET_GID}",
        timeout=30,
    )
    sheet.raise_for_status()
    rows = [line.split("\t") for line in sheet.content.decode("utf-8").splitlines()]
    header = rows[0]
    # Ensure new columns exist at X-AA (24th..27th columns).
    target_len = 27
    while len(header) < target_len:
        header.append("")
    for index, name in enumerate(NEW_HEADERS):
        header[23 + index] = name
    rows[0] = header

    col = {name: idx for idx, name in enumerate(header)}

    def col_letters(idx: int) -> str:
        letters = ""
        n = idx + 1
        while n:
            n, rem = divmod(n - 1, 26)
            letters = chr(65 + rem) + letters
        return letters

    def a1(col_index: int, row_number: int) -> str:
        return f"'{SHEET_TITLE}'!{col_letters(col_index)}{row_number}"

    value_updates = []
    value_updates.append(
        {
            "range": f"'{SHEET_TITLE}'!{col_letters(23)}1:{col_letters(26)}1",
            "values": [NEW_HEADERS],
        }
    )
    for row_number, row in enumerate(rows[1:], start=2):
        while len(row) < len(header):
            row.append("")
        partner_id = row[0].strip()
        payload = PARTNERS.get(partner_id)
        if not payload:
            continue
        row[col["public_site_url"]] = payload["public_site_url"]
        row[col["site_type"]] = payload["site_type"]
        row[col["focus_group"]] = payload["focus_group"]
        row[col["access_tier"]] = payload["access_tier"]
        row[col["page_mode"]] = payload["page_mode"]
        row[col["notes"]] = payload["notes"]
        for field in ("public_site_url", "site_type", "focus_group", "access_tier", "page_mode", "notes"):
            value_updates.append({"range": a1(col[field], row_number), "values": [[row[col[field]]]]})

    need_grid_expand = len(rows[0]) < 27 or not str(rows[0][23]).strip()
    grid_body = {"requests": [append_columns_request(4)]} if need_grid_expand else {"requests": []}
    values_body = {"valueInputOption": "USER_ENTERED", "data": value_updates}

    suffix = uuid.uuid4().hex[:10]
    path = f"whieda-partners-subdomains-{suffix}"
    nodes = [
        {
            "parameters": {"httpMethod": "POST", "path": path, "responseMode": "responseNode", "options": {}},
            "id": "webhook",
            "name": "Webhook",
            "type": "n8n-nodes-base.webhook",
            "typeVersion": 2,
            "position": [-200, 0],
        },
        {
            "parameters": {
                "jsCode": "const input = $input.first().json;\nconst body = input.body && typeof input.body === 'object' ? input.body : input;\nreturn [{ json: body }];",
            },
            "id": "normalize",
            "name": "Code: Normalize payload",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [-20, 0],
        },
    ]
    connections = {
        "Webhook": {"main": [[{"node": "Code: Normalize payload", "type": "main", "index": 0}]]},
    }
    if need_grid_expand:
        nodes.append(
            {
                "parameters": {
                    "method": "POST",
                    "url": f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}:batchUpdate",
                    "authentication": "predefinedCredentialType",
                    "nodeCredentialType": "googleSheetsOAuth2Api",
                    "sendBody": True,
                    "specifyBody": "json",
                    "jsonBody": "={{ $json.grid_body }}",
                    "options": {},
                },
                "id": "grid",
                "name": "Sheets grid",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.2,
                "position": [40, 0],
                "credentials": CREDENTIAL,
            }
        )
        connections["Code: Normalize payload"] = {"main": [[{"node": "Sheets grid", "type": "main", "index": 0}]]}
        connections["Sheets grid"] = {"main": [[{"node": "Sheets values", "type": "main", "index": 0}]]}
    else:
        connections["Code: Normalize payload"] = {"main": [[{"node": "Sheets values", "type": "main", "index": 0}]]}

    nodes.extend(
        [
            {
                "parameters": {
                    "method": "POST",
                    "url": f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate",
                    "authentication": "predefinedCredentialType",
                    "nodeCredentialType": "googleSheetsOAuth2Api",
                    "sendBody": True,
                    "specifyBody": "json",
                    "jsonBody": "={{ $('Code: Normalize payload').item.json.values_body }}",
                    "options": {},
                },
                "id": "values",
                "name": "Sheets values",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.2,
                "position": [120, 0],
                "credentials": CREDENTIAL,
            },
            {
                "parameters": {
                    "respondWith": "firstIncomingItem",
                    "options": {"responseCode": 200},
                },
                "id": "respond",
                "name": "Respond",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1.1,
                "position": [280, 0],
            },
        ]
    )
    connections["Sheets values"] = {"main": [[{"node": "Respond", "type": "main", "index": 0}]]}
    workflow = {
        "name": f"TEMP WHIEDA Partners Subdomains {suffix}",
        "active": False,
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
    }

    response = call_temp_webhook(
        helper,
        session,
        workflow,
        path,
        payload={"grid_body": grid_body, "values_body": values_body},
    )
    try:
        result = response.json()
    except ValueError:
        result = {"raw": response.text[:200]}
    print(json.dumps({"partners": list(PARTNERS.keys()), "response": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
