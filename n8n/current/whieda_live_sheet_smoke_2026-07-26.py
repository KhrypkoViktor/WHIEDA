"""Run enabled, non-medical smoke cases directly from the Google Sheet master."""

from __future__ import annotations

import csv
import importlib.util
import json
import time
from datetime import date
from io import StringIO
from pathlib import Path

import requests


BASE_DIR = Path(__file__).resolve().parent
SHEET_URL = "https://docs.google.com/spreadsheets/d/1Lm6ucw1oo0HQjvN2ZuxIGs2vK1lehw93jwqff7ldbz4/export?format=tsv&gid=2001023"
OUT_PATH = BASE_DIR.parent / "live-exports" / date.today().isoformat() / "WHIEDA_live_sheet_smoke.json"
SUITES = {"raw_dialogue", "raw_alias_variant"}


def load_runner():
    path = BASE_DIR / "whieda_live_demo_smoke_v2_2026-07-14.py"
    spec = importlib.util.spec_from_file_location("whieda_demo_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def enabled(value: str) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def fetch_cases():
    response = requests.get(SHEET_URL, timeout=30)
    response.raise_for_status()
    rows = list(csv.DictReader(StringIO(response.content.decode("utf-8-sig")), delimiter="\t"))
    return [row for row in rows if enabled(row.get("enabled")) and row.get("suite") in SUITES]


def main():
    cases = fetch_cases()
    smoke = load_runner()
    session = smoke.login()
    previous_id = max(smoke.execution_ids(session, limit=25), default=0)
    results = []
    for case in cases:
        started = time.monotonic()
        message_id = smoke.send(session, case["input_text"])
        execution_id, summary = smoke.wait_for(session, message_id, previous_id)
        previous_id = max(previous_id, execution_id or previous_id)
        reply = str((summary or {}).get("reply_text") or "")
        errors = []
        if not summary:
            errors.append("execution_not_found")
        else:
            if summary.get("execution_status") != "success":
                errors.append("execution_status=" + str(summary.get("execution_status")))
            if summary.get("route") != case.get("expected_route"):
                errors.append("route=" + str(summary.get("route")))
            expected = str(case.get("expected_answer_contains") or "").strip()
            if expected and expected.lower() not in reply.lower():
                errors.append("missing_text=" + expected)
            forbidden = str(case.get("expected_answer_not_contains") or "").strip()
            if forbidden and forbidden.lower() in reply.lower():
                errors.append("forbidden_text=" + forbidden)
            if forbidden.lower() == "dify" and summary.get("dify_called"):
                errors.append("dify_called")
            expected_photos = str(case.get("expected_photo_count") or "").strip()
            if expected_photos and int(expected_photos) != (1 if summary.get("photo_url") else 0):
                errors.append("photo_count")
        results.append({"case_id": case["case_id"], "prompt": case["input_text"], "execution_id": execution_id, "elapsed_seconds": round(time.monotonic() - started, 2), "status": "pass" if not errors else "fail", "errors": errors, "summary": summary})
        time.sleep(1)
    passed = sum(row["status"] == "pass" for row in results)
    report = {"meta": {"date": date.today().isoformat(), "total": len(results), "passed": passed, "failed": len(results) - passed, "pass_rate": round(100 * passed / len(results), 1) if results else 100}, "results": results}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"meta": report["meta"], "report_path": str(OUT_PATH)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
