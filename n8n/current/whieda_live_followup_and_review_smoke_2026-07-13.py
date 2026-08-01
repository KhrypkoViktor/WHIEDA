import json
import sys
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "https://sysarchn8n.duckdns.org"
EMAIL = "khrypko.viktar@gmail.com"
PASSWORD = "***REMOVED***"
WORKFLOW_ID = "advisor-whieda-phase1"
WEBHOOK_URL = f"{BASE_URL}/webhook/advisor-whieda-v0"

TRUSTED_CHAT_ID = 688931415
TRUSTED_USERNAME = "SunRaySword"
TRUSTED_FIRST_NAME = "Viktor"

CASES = [
    {
        "id": "followup-seq-001",
        "chat_id": TRUSTED_CHAT_ID,
        "username": TRUSTED_USERNAME,
        "first_name": TRUSTED_FIRST_NAME,
        "prompt": "расскажи про активатор",
    },
    {
        "id": "followup-seq-002",
        "chat_id": TRUSTED_CHAT_ID,
        "username": TRUSTED_USERNAME,
        "first_name": TRUSTED_FIRST_NAME,
        "prompt": "расскажи подробнее",
    },
    {
        "id": "followup-seq-003",
        "chat_id": TRUSTED_CHAT_ID,
        "username": TRUSTED_USERNAME,
        "first_name": TRUSTED_FIRST_NAME,
        "prompt": "как использовать",
    },
    {
        "id": "followup-seq-004",
        "chat_id": TRUSTED_CHAT_ID,
        "username": TRUSTED_USERNAME,
        "first_name": TRUSTED_FIRST_NAME,
        "prompt": "материалы",
    },
    {
        "id": "followup-seq-005",
        "chat_id": TRUSTED_CHAT_ID,
        "username": TRUSTED_USERNAME,
        "first_name": TRUSTED_FIRST_NAME,
        "prompt": "а сколько стоит?",
    },
    {
        "id": "followup-seq-006",
        "chat_id": TRUSTED_CHAT_ID,
        "username": TRUSTED_USERNAME,
        "first_name": TRUSTED_FIRST_NAME,
        "prompt": "дай фото",
    },
    {
        "id": "review-stats",
        "chat_id": TRUSTED_CHAT_ID,
        "username": TRUSTED_USERNAME,
        "first_name": TRUSTED_FIRST_NAME,
        "prompt": "/review_stats",
    },
    {
        "id": "review-pending-admin",
        "chat_id": TRUSTED_CHAT_ID,
        "username": TRUSTED_USERNAME,
        "first_name": TRUSTED_FIRST_NAME,
        "prompt": "/review_pending admin",
    },
    {
        "id": "review-pending-business",
        "chat_id": TRUSTED_CHAT_ID,
        "username": TRUSTED_USERNAME,
        "first_name": TRUSTED_FIRST_NAME,
        "prompt": "/review_pending business",
    },
    {
        "id": "review-pending-content",
        "chat_id": TRUSTED_CHAT_ID,
        "username": TRUSTED_USERNAME,
        "first_name": TRUSTED_FIRST_NAME,
        "prompt": "/review_pending content",
    },
]


def login() -> requests.Session:
    session = requests.Session()
    response = session.post(
        f"{BASE_URL}/rest/login",
        json={"emailOrLdapLoginId": EMAIL, "password": PASSWORD},
        verify=False,
        timeout=30,
    )
    response.raise_for_status()
    return session


def send_prompt(session: requests.Session, case: dict) -> int:
    message_id = int(time.time() * 1000) % 2147483647
    payload = {
        "message": {
            "message_id": message_id,
            "date": int(time.time()),
            "text": case["prompt"],
            "chat": {"id": case["chat_id"], "type": "private"},
            "from": {
                "id": case["chat_id"],
                "is_bot": False,
                "first_name": case["first_name"],
                "username": case["username"],
            },
        }
    }
    response = session.post(WEBHOOK_URL, json=payload, verify=False, timeout=60)
    response.raise_for_status()
    return message_id


def fetch_latest_execution_ids(session: requests.Session, limit: int = 25) -> list[int]:
    response = session.get(
        f"{BASE_URL}/rest/executions?limit={limit}&workflowId={WORKFLOW_ID}",
        verify=False,
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json().get("data", {})
    rows = payload.get("results", payload if isinstance(payload, list) else [])
    ids = []
    for item in rows:
        try:
            ids.append(int(item["id"]))
        except Exception:
            continue
    return ids


def fetch_execution_detail(session: requests.Session, execution_id: int) -> dict:
    response = session.get(
        f"{BASE_URL}/rest/executions/{execution_id}?includeData=true",
        verify=False,
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def decode_graph(graph: list) -> dict:
    memo = {}
    in_progress = set()

    def is_ref_string(value):
        return isinstance(value, str) and value.isdigit() and 0 <= int(value) < len(graph)

    def resolve_value(value):
        if is_ref_string(value):
            return resolve_index(int(value))
        if isinstance(value, list):
            return [resolve_value(item) for item in value]
        if isinstance(value, dict):
            return {key: resolve_value(item) for key, item in value.items()}
        return value

    def resolve_index(index: int):
        if index in memo:
            return memo[index]
        if index in in_progress:
            return f"[Circular:{index}]"
        in_progress.add(index)
        node = graph[index]
        resolved = resolve_value(node)
        memo[index] = resolved
        in_progress.remove(index)
        return resolved

    return resolve_index(0)


def decode_execution_payload(detail: dict) -> dict:
    encoded = detail.get("data", {}).get("data")
    if not isinstance(encoded, str):
        return {}
    graph = json.loads(encoded)
    return decode_graph(graph)


def get_latest_json(run_data: dict, node_name: str):
    runs = run_data.get(node_name)
    if not isinstance(runs, list) or not runs:
        return None
    last_run = runs[-1]
    main = last_run.get("data", {}).get("main")
    if not isinstance(main, list) or not main or not isinstance(main[0], list) or not main[0]:
        return None
    return main[0][-1].get("json")


def extract_summary(decoded: dict) -> dict:
    run_data = decoded.get("resultData", {}).get("runData", {})
    normalized = get_latest_json(run_data, "Code: Normalize Payload") or {}
    validated = get_latest_json(run_data, "Code: Validate Dify Response") or {}
    return {
        "message_id": normalized.get("raw_payload", {}).get("body", {}).get("message", {}).get("message_id"),
        "prompt": normalized.get("message_text"),
        "route": validated.get("route"),
        "answer_mode": validated.get("answer_mode"),
        "structured_hit": validated.get("structured_hit"),
        "structured_match": validated.get("structured_match"),
        "telegram_photo_url": validated.get("telegram_photo_url"),
        "telegram_photo_caption": validated.get("telegram_photo_caption"),
        "reply_text": validated.get("reply_text"),
        "knowledge_gap": validated.get("knowledge_gap"),
    }


def execution_contains_message_id(summary: dict, message_id: int) -> bool:
    return str(summary.get("message_id")) == str(message_id)


def wait_for_execution(session: requests.Session, message_id: int) -> tuple[int | None, dict | None, dict | None]:
    for _ in range(25):
        time.sleep(2)
        for execution_id in fetch_latest_execution_ids(session):
            detail = fetch_execution_detail(session, execution_id)
            decoded = decode_execution_payload(detail)
            summary = extract_summary(decoded)
            if execution_contains_message_id(summary, message_id):
                return execution_id, detail, summary
    return None, None, None


def main() -> None:
    output_dir = Path(__file__).resolve().parent
    session = login()
    results = []

    for case in CASES:
        message_id = send_prompt(session, case)
        execution_id, detail, summary = wait_for_execution(session, message_id)
        results.append(
            {
                "id": case["id"],
                "prompt": case["prompt"],
                "chat_id": case["chat_id"],
                "message_id": message_id,
                "execution_id": execution_id,
                "status": "saved" if execution_id is not None else "not_found",
                "summary": summary,
            }
        )
        if execution_id is not None:
            detail_path = output_dir / f"execution_{execution_id}_{case['id']}.json"
            summary_path = output_dir / f"execution_{execution_id}_{case['id']}.decoded-summary.json"
            detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        time.sleep(1)

    report_path = output_dir / "WHIEDA_live_followup_and_review_smoke_2026-07-13.json"
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
