"""Smoke: /report, /leads access via SQL roles, product SQL still green."""

from __future__ import annotations

import importlib.util
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

BASE_DIR = Path(__file__).resolve().parent
HELPER_PATH = BASE_DIR / "publish_and_run_whieda_sync_2026-07-13.py"
WORKFLOW_ID = "advisor-whieda-phase1"
WEBHOOK_URL = "https://sysarchn8n.duckdns.org/webhook/advisor-whieda-v0"
CHAT_ID = 1147735602
USERNAME = "Khrypko_pro"
FIRST_NAME = "Viktor Test"
OUT_PATH = BASE_DIR.parent / "live-exports" / date.today().isoformat() / "WHIEDA_leads_report_smoke_2026-08-01.json"
PACK_PATH = BASE_DIR / "WHIEDA_DEMO_SMOKE_PACK_V1_2026-07-13.json"
PRODUCT_PROMPT = next(
    case["input"]
    for case in json.loads(PACK_PATH.read_text(encoding="utf-8"))["cases"]
    if case.get("id") == "P0-004"
)


def load_helper():
    spec = importlib.util.spec_from_file_location("whieda_sync", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def decode_graph(graph):
    memo, busy = {}, set()

    def resolve(value):
        if isinstance(value, str) and value.isdigit() and int(value) < len(graph):
            return index(int(value))
        if isinstance(value, list):
            return [resolve(item) for item in value]
        if isinstance(value, dict):
            return {key: resolve(item) for key, item in value.items()}
        return value

    def index(i):
        if i in memo:
            return memo[i]
        if i in busy:
            return f"[Circular:{i}]"
        busy.add(i)
        memo[i] = resolve(graph[i])
        busy.remove(i)
        return memo[i]

    return index(0)


def last_json(run_data, name):
    runs = run_data.get(name) or []
    for run in reversed(runs):
        main = run.get("data", {}).get("main", [])
        if main and main[0]:
            return main[0][-1].get("json", {})
    return {}


def lead_reply_text(run_data):
    for name in (
        "Code: Format owner report",
        "Code: Format /leads",
        "Code: Format lead amount",
        "Code: Format lead status",
        "Code: Format lead detail",
        "Telegram: Send /leads",
        "Telegram: Send Message",
        "Code: Validate Dify Response",
    ):
        payload = last_json(run_data, name)
        text = payload.get("telegram_text") or payload.get("reply_text")
        if text:
            return text
    return None


def execution_ids(session, base_url, limit=30):
    response = session.get(f"{base_url}/rest/executions?limit={limit}&workflowId={WORKFLOW_ID}", verify=False, timeout=30)
    rows = response.json().get("data", {}).get("results", [])
    return [int(row["id"]) for row in rows if row.get("workflowId") == WORKFLOW_ID]


def fetch_summary(session, base_url, execution_id):
    detail = session.get(f"{base_url}/rest/executions/{execution_id}?includeData=true", verify=False, timeout=60).json()
    data = detail.get("data", {}).get("data")
    if not isinstance(data, str):
        return None
    run_data = decode_graph(json.loads(data)).get("resultData", {}).get("runData", {})
    normalized = last_json(run_data, "Code: Normalize Payload")
    route = last_json(run_data, "Code: Route /leads")
    validated = last_json(run_data, "Code: Validate Dify Response")
    return {
        "execution_status": detail.get("data", {}).get("status"),
        "message_text": normalized.get("message_text"),
        "lead_actor_id": route.get("lead_actor_id"),
        "leads_scope": route.get("leads_scope"),
        "leads_access": route.get("leads_access"),
        "is_owner_report": route.get("is_owner_report"),
        "route": validated.get("route"),
        "answer_mode": validated.get("answer_mode"),
        "structured_hit": validated.get("structured_hit"),
        "dify_called": any("Dify advisor" in name for name in run_data),
        "telegram_text": lead_reply_text(run_data),
        "execution_nodes": list(run_data.keys()),
    }


def wait_for(session, base_url, prompt, previous_execution_id, *, attempts=45):
    inspected = set()
    for _ in range(attempts):
        time.sleep(1)
        for execution_id in sorted(
            execution_id
            for execution_id in execution_ids(session, base_url, 100)
            if execution_id > previous_execution_id
        ):
            if execution_id in inspected:
                continue
            inspected.add(execution_id)
            summary = fetch_summary(session, base_url, execution_id)
            if summary and str(summary.get("message_text") or "").strip() == prompt.strip():
                return execution_id, summary
    return None, None


def send(session, text):
    message_id = int(time.time() * 1000) % 2147483647
    payload = {
        "whieda_synthetic_test": True,
        "message": {
            "message_id": message_id,
            "date": int(time.time()),
            "text": text,
            "chat": {"id": CHAT_ID, "type": "private"},
            "from": {"id": CHAT_ID, "is_bot": False, "first_name": FIRST_NAME, "username": USERNAME},
        },
    }
    session.post(WEBHOOK_URL, json=payload, verify=False, timeout=60).raise_for_status()
    return message_id


def run_case(session, base_url, prompt, checker, previous_execution_id):
    send(session, prompt)
    execution_id, summary = wait_for(session, base_url, prompt, previous_execution_id)
    errors = checker(summary or {})
    return {
        "prompt": prompt,
        "execution_id": execution_id,
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "summary": summary,
    }, max(previous_execution_id, execution_id or previous_execution_id)


def main():
    helper = load_helper()
    session = helper.login_session()
    last_id = max(execution_ids(session, helper.BASE_URL, 20), default=0)
    cases = []

    checks = [
        (
            "/report",
            lambda s: (
                ([] if s.get("execution_status") == "success" else ["execution_status"])
                + ([] if s.get("lead_actor_id") else ["missing_actor"])
                + ([] if s.get("leads_scope") == "all" else [f"scope={s.get('leads_scope')}"])
                + ([] if s.get("is_owner_report") else ["owner_report_false"])
                + ([] if "Сводка" in str(s.get("telegram_text") or "") else ["report_text_missing"])
            ),
        ),
        (
            "/leads",
            lambda s: (
                ([] if s.get("execution_status") == "success" else ["execution_status"])
                + ([] if s.get("leads_access") else ["leads_access_false"])
                + ([] if "заяв" in str(s.get("telegram_text") or "").lower() else ["leads_text_missing"])
            ),
        ),
        (
            "/lead L-NOTFOUND сумма 99 BYN",
            lambda s: (
                ([] if s.get("execution_status") == "success" else ["execution_status"])
                + ([] if "не удалось" in str(s.get("telegram_text") or "").lower() else ["expected_safe_amount_error"])
            ),
        ),
        (
            PRODUCT_PROMPT,
            lambda s: (
                ([] if s.get("execution_status") == "success" else ["execution_status"])
                + ([] if s.get("structured_hit") is True else ["structured_miss"])
            ),
        ),
    ]

    for prompt, checker in checks:
        row, last_id = run_case(session, helper.BASE_URL, prompt, checker, last_id)
        cases.append(row)
        time.sleep(1.2)

    passed = sum(row["status"] == "pass" for row in cases)
    report = {
        "meta": {
            "date": date.today().isoformat(),
            "total": len(cases),
            "passed": passed,
            "failed": len(cases) - passed,
        },
        "cases": cases,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"meta": report["meta"], "failures": [c for c in cases if c["status"] == "fail"], "report_path": str(OUT_PATH)}, ensure_ascii=False, indent=2))
    if passed != len(cases):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
