import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BASE_URL = "https://sysarchn8n.duckdns.org"
EMAIL = "khrypko.viktar@gmail.com"
PASSWORD = "XdyLnC73KQGeaiT"
WORKFLOW_ID = "advisor-whieda-phase1"
WEBHOOK_URL = f"{BASE_URL}/webhook/advisor-whieda-v0"

CHAT_ID = 1147735602
USERNAME = "Khrypko_pro"
FIRST_NAME = "Viktor Test"

PACK_PATH = Path(__file__).with_name("WHIEDA_DEMO_SMOKE_PACK_V1_2026-07-13.json")
OUT_PATH = Path(__file__).resolve().parents[1] / "live-exports" / date.today().isoformat() / "WHIEDA_live_p0_smoke_report.json"

INTENT_TO_MODE = {
    "clarify": "direct_structured_clarify",
    "card": "direct_structured_card",
    "price_primary": "direct_structured",
    "price_repeat": "direct_structured",
    "photo": "direct_structured_photo",
}


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


def send_prompt(session: requests.Session, text: str) -> int:
    message_id = int(time.time() * 1000) % 2147483647
    payload = {
        "message": {
            "message_id": message_id,
            "date": int(time.time()),
            "text": text,
            "chat": {"id": CHAT_ID, "type": "private"},
            "from": {
                "id": CHAT_ID,
                "is_bot": False,
                "first_name": FIRST_NAME,
                "username": USERNAME,
            },
        }
    }
    response = session.post(WEBHOOK_URL, json=payload, verify=False, timeout=60)
    response.raise_for_status()
    return message_id


def fetch_latest_execution_ids(session: requests.Session, limit: int = 8) -> list[int]:
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


def fetch_execution_detail(session: requests.Session, execution_id: int) -> dict:
    response = session.get(
        f"{BASE_URL}/rest/executions/{execution_id}?includeData=true",
        verify=False,
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def get_latest_json(run_data: dict, node_name: str):
    runs = run_data.get(node_name)
    if not isinstance(runs, list) or not runs:
        return None
    last_run = runs[-1]
    main = last_run.get("data", {}).get("main")
    if not isinstance(main, list) or not main or not isinstance(main[0], list) or not main[0]:
        return None
    return main[0][-1].get("json")


def extract_summary(detail: dict) -> dict:
    graph = json.loads(detail["data"]["data"])
    decoded = decode_graph(graph)
    run_data = decoded.get("resultData", {}).get("runData", {})
    normalized = get_latest_json(run_data, "Code: Normalize Payload") or {}
    validated = get_latest_json(run_data, "Code: Validate Dify Response") or {}
    return {
        "execution_status": detail.get("data", {}).get("status"),
        "message_id": normalized.get("raw_payload", {}).get("body", {}).get("message", {}).get("message_id"),
        "prompt": normalized.get("message_text"),
        "route": validated.get("route"),
        "answer_mode": validated.get("answer_mode"),
        "structured_hit": validated.get("structured_hit"),
        "structured_source": validated.get("structured_source"),
        "structured_match": validated.get("structured_match"),
        "telegram_photo_url": validated.get("telegram_photo_url"),
        "telegram_photo_caption": validated.get("telegram_photo_caption"),
        "reply_text": validated.get("reply_text"),
        "knowledge_gap": validated.get("knowledge_gap"),
    }


def wait_for_execution(session: requests.Session, message_id: int, previous_ids: set[int]) -> tuple[int | None, dict | None]:
    """Only inspect executions created after this exact test message.

    The old loop fetched detailed payloads for up to thirty historical executions
    on every poll. That made a nineteen-case P0 run take many minutes even when
    the advisor was healthy.
    """
    checked_ids: set[int] = set()
    for _ in range(12):
        time.sleep(1)
        fresh_ids = [execution_id for execution_id in fetch_latest_execution_ids(session) if execution_id not in previous_ids and execution_id not in checked_ids]
        for execution_id in fresh_ids:
            detail = fetch_execution_detail(session, execution_id)
            execution = detail.get("data", {})
            if execution.get("status") == "running":
                continue
            checked_ids.add(execution_id)
            summary = extract_summary(detail)
            if str(summary.get("message_id")) == str(message_id):
                return execution_id, summary
    return None, None


def evaluate(case: dict, summary: dict | None) -> dict:
    expected = case.get("expected", {})
    if not summary:
        return {"passed": False, "reason": "execution_not_found"}

    failures = []
    if summary.get("execution_status") != "success":
        failures.append(f"execution_status={summary.get('execution_status')}")
    expected_mode = INTENT_TO_MODE.get(expected.get("intent"))
    if expected_mode and summary.get("answer_mode") != expected_mode:
        if not (expected.get("intent") == "video" and summary.get("structured_hit") is True):
            failures.append(f"answer_mode={summary.get('answer_mode')}")

    if summary.get("route") != "answer":
        failures.append(f"route={summary.get('route')}")

    if summary.get("structured_hit") is not True:
        failures.append("structured_hit=false")

    expected_sku = expected.get("sku")
    actual_sku = (summary.get("structured_match") or {}).get("sku")
    if expected_sku and actual_sku != expected_sku:
        failures.append(f"sku={actual_sku}")

    want_photo = expected.get("photos", 0) > 0
    has_photo = bool(summary.get("telegram_photo_url"))
    if want_photo != has_photo:
        failures.append(f"photo={has_photo}")

    if expected.get("caption_empty") is True and summary.get("telegram_photo_caption") not in (None, ""):
        failures.append("caption_not_empty")

    if summary.get("knowledge_gap") is True:
        failures.append("knowledge_gap=true")

    return {
        "passed": not failures,
        "reason": "ok" if not failures else ", ".join(failures),
    }


def load_cases() -> list[dict]:
    payload = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    cases = []
    for case in payload.get("cases", []):
        if case.get("type") != "independent":
            continue
        if not str(case.get("id", "")).startswith("P0-"):
            continue
        if case.get("id") == "P0-019":
            continue
        cases.append(case)
    return cases


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-id")
    parser.add_argument("--max-cases", type=int)
    args = parser.parse_args()
    session = login()
    cases = load_cases()
    if args.case_id:
        cases = [case for case in cases if case["id"] == args.case_id]
        if not cases:
            raise SystemExit(f"P0 case not found: {args.case_id}")
    if args.max_cases is not None:
        cases = cases[:max(0, args.max_cases)]
    results = []

    for case in cases:
        previous_ids = set(fetch_latest_execution_ids(session))
        message_id = send_prompt(session, case["input"])
        execution_id, summary = wait_for_execution(session, message_id, previous_ids)
        evaluation = evaluate(case, summary)
        results.append(
            {
                "id": case["id"],
                "prompt": case["input"],
                "execution_id": execution_id,
                "status": "pass" if evaluation["passed"] else "fail",
                "reason": evaluation["reason"],
                "summary": summary,
            }
        )
        time.sleep(1)

    total = len(results)
    passed = sum(1 for row in results if row["status"] == "pass")
    report = {
        "meta": {
            "date": date.today().isoformat(),
            "chat_id": CHAT_ID,
            "cases_total": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round((passed / total) * 100, 1) if total else 0,
            "note": "P0-019 skipped here because no-context needs isolated fresh chat proof.",
        },
        "results": results,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report_path = OUT_PATH
    if args.case_id:
        report_path = OUT_PATH.with_name(f"WHIEDA_live_p0_smoke_{args.case_id}.json")
    elif args.max_cases is not None:
        report_path = OUT_PATH.with_name(f"WHIEDA_live_p0_smoke_first_{args.max_cases}.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
