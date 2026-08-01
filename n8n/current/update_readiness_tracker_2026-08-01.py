"""Update readiness % and row colors in the WHIEDA roadmap tracker sheet."""

from __future__ import annotations

import importlib.util
import json
import time
import uuid
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
TRACKER_ID = "1Meop_B58xAxOXt-kPc0zwfsrwBlM8fneMvz0U3m9g8U"
TRACKER_GID = 1390269849
CREDENTIAL = {"googleSheetsOAuth2Api": {"id": "XnF6UcslXgxuXbGm", "name": "Google Sheets account"}}

# Row index in sheet (1-based), new readiness, optional note for column I.
UPDATES = {
    "1.2": {
        "percent": "85%",
        "note": "SQL-роли из lead_actors, /leads и /report live, candidate сразу получает ответы. Осталось: API для сайта и полный smoke Dify-off.",
    },
    "1.5": {
        "percent": "92%",
        "note": "Фото/видео/PDF из structured runtime. Осталось: единый smoke по материалам и API сайта.",
    },
    "2.4": {
        "percent": "50%",
        "note": "Structured-сравнения в SQL работают. Осталось: довести формулировки и покрыть edge-кейсы.",
    },
    "5.2": {
        "percent": "92%",
        "note": "/report расширен: пользователи, SQL/RAG/fallback, уточнения, заявки, ref-воронка. Осталось: выгрузка в Sheet по кнопке.",
    },
}

COLOR_BY_PERCENT = [
    (90, {"red": 0.72, "green": 0.88, "blue": 0.72}),  # green
    (70, {"red": 0.98, "green": 0.90, "blue": 0.60}),  # yellow
    (40, {"red": 0.98, "green": 0.80, "blue": 0.55}),  # orange
    (0, {"red": 0.96, "green": 0.70, "blue": 0.70}),   # red
]


def load_helper():
    path = BASE / "publish_and_run_whieda_sync_2026-07-13.py"
    spec = importlib.util.spec_from_file_location("whieda_sync", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def percent_value(text: str) -> int:
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits[:2] or "0")


def color_for_percent(text: str) -> dict:
    value = percent_value(text)
    for threshold, color in COLOR_BY_PERCENT:
        if value >= threshold:
            return color
    return COLOR_BY_PERCENT[-1][1]


def build_batch_body(row_map: dict[int, dict]) -> dict:
    data = []
    formats = []
    for row_number, payload in sorted(row_map.items()):
        zero_row = row_number - 1
        data.append(
            {
                "range": f"E{row_number}",
                "values": [[payload["percent"]]],
            }
        )
        if payload.get("note"):
            data.append(
                {
                    "range": f"I{row_number}",
                    "values": [[payload["note"]]],
                }
            )
        formats.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": TRACKER_GID,
                        "startRowIndex": zero_row,
                        "endRowIndex": zero_row + 1,
                        "startColumnIndex": 4,
                        "endColumnIndex": 5,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": color_for_percent(payload["percent"]),
                        }
                    },
                    "fields": "userEnteredFormat.backgroundColor",
                }
            }
        )
    return {
        "valueBody": {"valueInputOption": "USER_ENTERED", "data": data},
        "formatBody": {"requests": formats},
    }


def main() -> None:
    helper = load_helper()
    session = helper.login_session()

    tracker = requests.get(
        f"https://docs.google.com/spreadsheets/d/{TRACKER_ID}/export?format=tsv&gid={TRACKER_GID}",
        timeout=30,
    )
    tracker.raise_for_status()
    rows = [line.split("\t") for line in tracker.content.decode("utf-8").splitlines()]
    row_map: dict[int, dict] = {}
    for index, row in enumerate(rows, start=1):
        if not row:
            continue
        feature_id = row[0].strip()
        if feature_id in UPDATES:
            row_map[index] = UPDATES[feature_id]

    if not row_map:
        raise RuntimeError("No tracker rows matched for update")

    batch = build_batch_body(row_map)
    suffix = uuid.uuid4().hex[:10]
    path = f"whieda-readiness-update-{suffix}"
    workflow = {
        "name": f"TEMP WHIEDA Readiness Update {suffix}",
        "active": False,
        "nodes": [
            {
                "parameters": {"httpMethod": "POST", "path": path, "responseMode": "responseNode", "options": {}},
                "id": "webhook",
                "name": "Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 2,
                "position": [-300, 0],
            },
            {
                "parameters": {
                    "method": "POST",
                    "url": f"https://sheets.googleapis.com/v4/spreadsheets/{TRACKER_ID}/values:batchUpdate",
                    "authentication": "predefinedCredentialType",
                    "nodeCredentialType": "googleSheetsOAuth2Api",
                    "sendBody": True,
                    "specifyBody": "json",
                    "jsonBody": json.dumps(batch["valueBody"], ensure_ascii=False),
                    "options": {},
                },
                "id": "values",
                "name": "Sheets values",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.2,
                "position": [-40, 0],
                "credentials": CREDENTIAL,
            },
            {
                "parameters": {
                    "method": "POST",
                    "url": f"https://sheets.googleapis.com/v4/spreadsheets/{TRACKER_ID}:batchUpdate",
                    "authentication": "predefinedCredentialType",
                    "nodeCredentialType": "googleSheetsOAuth2Api",
                    "sendBody": True,
                    "specifyBody": "json",
                    "jsonBody": json.dumps(batch["formatBody"], ensure_ascii=False),
                    "options": {},
                },
                "id": "formats",
                "name": "Sheets formats",
                "type": "n8n-nodes-base.httpRequest",
                "typeVersion": 4.2,
                "position": [220, 0],
                "credentials": CREDENTIAL,
            },
            {
                "parameters": {
                    "respondWith": "json",
                    "responseBody": "={{ { ok: true, updated_rows: "
                    + json.dumps(list(UPDATES.keys()), ensure_ascii=False)
                    + " } }}",
                    "options": {"responseCode": 200},
                },
                "id": "respond",
                "name": "Respond",
                "type": "n8n-nodes-base.respondToWebhook",
                "typeVersion": 1.1,
                "position": [480, 0],
            },
        ],
        "connections": {
            "Webhook": {"main": [[{"node": "Sheets values", "type": "main", "index": 0}]]},
            "Sheets values": {"main": [[{"node": "Sheets formats", "type": "main", "index": 0}]]},
            "Sheets formats": {"main": [[{"node": "Respond", "type": "main", "index": 0}]]},
        },
        "settings": {"executionOrder": "v1"},
    }

    workflow_id = None
    try:
        created = session.post(f"{helper.BASE_URL}/rest/workflows", json=workflow, verify=False, timeout=60)
        created.raise_for_status()
        data = created.json().get("data", created.json())
        workflow_id = data["id"]
        version = data.get("versionId")
        session.post(
            f"{helper.BASE_URL}/rest/workflows/{workflow_id}/activate",
            json={"versionId": version},
            verify=False,
            timeout=60,
        ).raise_for_status()
        helper.ssh_run(f"docker exec n8n-n8n-1 n8n publish:workflow --id={workflow_id}")
        helper.ssh_run("docker restart n8n-n8n-1")
        helper.wait_for_n8n_ready()
        time.sleep(8)
        response = requests.post(f"{helper.BASE_URL}/webhook/{path}", json={}, verify=False, timeout=90)
        response.raise_for_status()
        print(
            json.dumps(
                {
                    "updated": list(UPDATES.keys()),
                    "rows": row_map,
                    "response": response.json(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        if workflow_id:
            session.delete(f"{helper.BASE_URL}/rest/workflows/{workflow_id}", verify=False, timeout=60)


if __name__ == "__main__":
    main()
