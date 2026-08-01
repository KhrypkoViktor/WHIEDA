"""Extended live smoke: structured catalogue and follow-up chains on the test account only."""
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
PASSWORD = "***REMOVED***"
WORKFLOW_ID = "advisor-whieda-phase1"
WEBHOOK_URL = f"{BASE_URL}/webhook/advisor-whieda-v0"
CHAT_ID = 1147735602
USERNAME = "Khrypko_pro"
FIRST_NAME = "Viktor Test"
PACK_PATH = Path(__file__).with_name("WHIEDA_DEMO_SMOKE_PACK_V1_2026-07-13.json")
OUT_PATH = Path(__file__).resolve().parents[1] / "live-exports" / date.today().isoformat() / "WHIEDA_live_demo_smoke_v2.json"


def login():
    session = requests.Session()
    session.post(f"{BASE_URL}/rest/login", json={"emailOrLdapLoginId": EMAIL, "password": PASSWORD}, verify=False, timeout=30).raise_for_status()
    return session


def send(session, text):
    message_id = int(time.time() * 1000) % 2147483647
    payload = {"whieda_synthetic_test": True, "message": {"message_id": message_id, "date": int(time.time()), "text": text,
        "chat": {"id": CHAT_ID, "type": "private"},
        "from": {"id": CHAT_ID, "is_bot": False, "first_name": FIRST_NAME, "username": USERNAME}}}
    session.post(WEBHOOK_URL, json=payload, verify=False, timeout=60).raise_for_status()
    return message_id


def decode_graph(graph):
    memo, busy = {}, set()
    def resolve(value):
        if isinstance(value, str) and value.isdigit() and int(value) < len(graph):
            return index(int(value))
        if isinstance(value, list): return [resolve(item) for item in value]
        if isinstance(value, dict): return {key: resolve(item) for key, item in value.items()}
        return value
    def index(i):
        if i in memo: return memo[i]
        if i in busy: return f"[Circular:{i}]"
        busy.add(i); memo[i] = resolve(graph[i]); busy.remove(i); return memo[i]
    return index(0)


def last_json(run_data, name):
    runs = run_data.get(name) or []
    if not runs: return {}
    main = runs[-1].get("data", {}).get("main", [])
    return main[0][-1].get("json", {}) if main and main[0] else {}


def execution_ids(session, limit=12):
    response = session.get(f"{BASE_URL}/rest/executions?limit={limit}&workflowId={WORKFLOW_ID}", verify=False, timeout=30)
    payload = response.json().get("data", {})
    rows = payload.get("results", payload if isinstance(payload, list) else [])
    return [
        int(row["id"])
        for row in rows
        if row.get("workflowId") == WORKFLOW_ID and str(row.get("id", "")).isdigit()
    ]


def fetch_summary(session, execution_id):
    detail = session.get(f"{BASE_URL}/rest/executions/{execution_id}?includeData=true", verify=False, timeout=60).json()
    data = detail.get("data", {}).get("data")
    if not isinstance(data, str): return None
    run_data = decode_graph(json.loads(data)).get("resultData", {}).get("runData", {})
    normalized = last_json(run_data, "Code: Normalize Payload")
    validated = last_json(run_data, "Code: Validate Dify Response")
    return {
        "execution_status": detail.get("data", {}).get("status"),
        "message_id": normalized.get("raw_payload", {}).get("body", {}).get("message", {}).get("message_id"),
        "prompt": normalized.get("message_text"),
        "route": validated.get("route"),
        "answer_mode": validated.get("answer_mode"),
        "structured_hit": validated.get("structured_hit"),
        "structured_match": validated.get("structured_match"),
        "photo_url": validated.get("telegram_photo_url"),
        "caption": validated.get("telegram_photo_caption"),
        "reply_text": validated.get("reply_text"),
        "knowledge_gap": validated.get("knowledge_gap"),
        "dify_called": any("Dify advisor" in name for name in run_data),
        "execution_nodes": list(run_data.keys()),
    }


def wait_for(session, message_id, previous_execution_id):
    inspected = set()
    for _ in range(18):
        time.sleep(1)
        # n8n also has background executions. Inspect every unseen advisor run
        # after the prompt, rather than assuming the largest ID is ours.
        fresh_ids = [execution_id for execution_id in execution_ids(session, limit=100) if execution_id > previous_execution_id]
        for execution_id in sorted(fresh_ids):
            if execution_id in inspected:
                continue
            inspected.add(execution_id)
            summary = fetch_summary(session, execution_id)
            if summary and str(summary.get("message_id")) == str(message_id):
                return execution_id, summary
    return None, None


def check_structured(summary, expected_sku=None, expect_photo=None, expected_mode=None, forbid_text=None):
    errors = []
    if not summary:
        return ["execution_not_found"]
    if summary.get("execution_status") != "success": errors.append("execution_status=" + str(summary.get("execution_status")))
    if summary.get("route") != "answer": errors.append("route=" + str(summary.get("route")))
    if summary.get("structured_hit") is not True: errors.append("structured_hit=false")
    if summary.get("dify_called"): errors.append("dify_called=true")
    if summary.get("knowledge_gap") is True: errors.append("knowledge_gap=true")
    sku = (summary.get("structured_match") or {}).get("sku")
    if expected_sku and sku != expected_sku: errors.append("sku=" + str(sku))
    if expect_photo is not None and bool(summary.get("photo_url")) != expect_photo: errors.append("photo=" + str(bool(summary.get("photo_url"))))
    if expect_photo and summary.get("caption") not in (None, ""): errors.append("caption_not_empty")
    if expected_mode and summary.get("answer_mode") != expected_mode: errors.append("answer_mode=" + str(summary.get("answer_mode")))
    reply = str(summary.get("reply_text") or "").lower()
    for phrase in forbid_text or []:
        if str(phrase).lower() in reply:
            errors.append("forbidden_text=" + str(phrase))
    return errors


def run_prompt(session, prompt, expected, previous_execution_id):
    started = time.monotonic()
    message_id = send(session, prompt)
    execution_id, summary = wait_for(session, message_id, previous_execution_id)
    elapsed_seconds = round(time.monotonic() - started, 2)
    errors = check_structured(
        summary,
        expected.get("sku"),
        expected.get("photos", 0) > 0 if "photos" in expected else None,
        expected.get("answer_mode"),
        expected.get("forbid_text"),
    )
    if expected.get("max_seconds") and elapsed_seconds > expected["max_seconds"]:
        errors.append("slow_response=" + str(elapsed_seconds))
    return execution_id, summary, errors, elapsed_seconds, max(previous_execution_id, execution_id or previous_execution_id)


def main():
    pack = json.loads(PACK_PATH.read_text(encoding="utf-8"))
    session = login()
    results = []
    last_execution_id = max(execution_ids(session, limit=12), default=0)

    # Independent product prompts: do not include no-context, group or review cases.
    for case in pack["cases"]:
        if case.get("type") != "independent" or case.get("id") == "P0-019":
            continue
        if not (case["id"].startswith("P0-") or case["id"].startswith("SAFE-") or case["id"].startswith("TYPO-")):
            continue
        expected = case.get("expected", {})
        execution_id, summary, errors, elapsed_seconds, last_execution_id = run_prompt(session, case["input"], expected, last_execution_id)
        results.append({"id": case["id"], "prompt": case["input"], "execution_id": execution_id, "elapsed_seconds": elapsed_seconds, "status": "pass" if not errors else "fail", "errors": errors, "summary": summary})
        time.sleep(1)

    # Each sequence explicitly starts from a named product, so it resets the test conversation before its follow-ups.
    for case in pack["cases"]:
        if case.get("type") != "follow_up_sequence":
            continue
        expected_sku = case.get("expected", {}).get("sku_stable")
        sequence_rows = []
        for step, prompt in enumerate(case["sequence"], start=1):
            # The first sequence message is a product card, so it also sends
            # the product photo before the text even when the word "фото" is absent.
            expected = {"sku": expected_sku, "photos": 1 if step == 1 or "фото" in prompt.lower() else 0}
            if "чем отличается" in prompt.lower():
                expected["answer_mode"] = "direct_structured_comparison_layer"
            execution_id, summary, errors, elapsed_seconds, last_execution_id = run_prompt(session, prompt, expected, last_execution_id)
            sequence_rows.append({"step": step, "prompt": prompt, "execution_id": execution_id, "elapsed_seconds": elapsed_seconds, "errors": errors, "summary": summary})
            time.sleep(1)
        errors = [f"step_{row['step']}: {error}" for row in sequence_rows for error in row["errors"]]
        results.append({"id": case["id"], "sequence": case["sequence"], "status": "pass" if not errors else "fail", "errors": errors, "steps": sequence_rows})

    passed = sum(row["status"] == "pass" for row in results)
    report = {"meta": {"date": date.today().isoformat(), "chat_id": CHAT_ID, "total": len(results), "passed": passed, "failed": len(results) - passed, "pass_rate": round(100 * passed / len(results), 1) if results else 0}, "results": results}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [{"id": row["id"], "errors": row["errors"]} for row in results if row["status"] == "fail"]
    print(json.dumps({"meta": report["meta"], "failures": failures, "report_path": str(OUT_PATH)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
