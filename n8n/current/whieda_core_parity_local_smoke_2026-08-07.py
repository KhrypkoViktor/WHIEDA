"""Local Core advisor smoke with text/mode + media assertions (no SSH/prod).

Usage (from repo root):
  $env:PYTHONPATH="backend/platform-api"
  python n8n/current/whieda_core_parity_local_smoke_2026-08-07.py
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "platform-api"))

try:
    import httpx
except ImportError:
    print("httpx required", file=sys.stderr)
    raise

from app.advisor.parity.media_assertions import evaluate_media_for_case, media_is_empty

TSV_PATH = Path(__file__).resolve().parent / "source_batches/smoke_cases_sheet_v1/smoke_cases_raw.tsv"
OUT_PATH = Path(__file__).resolve().parent / "_core_parity_local_smoke_report.json"

SERVICE_CASES = [
    {"case_id": "SERVICE-GREETING-NO-MEDIA", "input_text": "привет", "expected_intent": "greeting"},
    {"case_id": "SERVICE-HELP-NO-MEDIA", "input_text": "помощь", "expected_intent": "help"},
]


def load_p0_rows(path: Path, limit: int) -> list[dict[str, str]]:
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if str(row.get("enabled", "TRUE")).upper() not in {"TRUE", "1", "YES"}:
                continue
            if row.get("suite") == "review":
                continue
            rows.append(row)
            if len(rows) >= limit:
                break
    return rows


def query_advisor(client: httpx.Client, base_url: str, question: str, session: str) -> dict:
    payload = {"question": question, "session": session, "tenant": "whieda", "country": "BY"}
    resp = client.post(
        f"{base_url.rstrip('/')}/v1/advisor/query",
        json=payload,
        headers={"host": "wwc.best"},
        timeout=20.0,
    )
    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    return {
        "ok": resp.status_code == 200,
        "status": resp.status_code,
        "answer_mode": body.get("answer_mode"),
        "answer_text": body.get("answer_text"),
        "media": body.get("media"),
        "product": body.get("product"),
        "context": body.get("context"),
        "trace_id": body.get("trace_id"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--p0-limit", type=int, default=20)
    args = parser.parse_args()

    cases = SERVICE_CASES + load_p0_rows(TSV_PATH, args.p0_limit)
    results = []
    with httpx.Client() as client:
        for row in cases:
            case_id = row.get("case_id") or row["input_text"][:24]
            session = f"parity-{case_id}-{uuid.uuid4().hex[:6]}"
            resp = query_advisor(client, args.base_url, row["input_text"], session)
            media_errors = evaluate_media_for_case(
                expected_intent=row.get("expected_intent", ""),
                answer_mode=resp.get("answer_mode"),
                media=resp.get("media"),
                product=resp.get("product"),
                context=resp.get("context"),
            )
            if row.get("expected_intent") == "greeting" and not media_is_empty(resp.get("media")):
                media_errors.append("SERVICE-GREETING-NO-MEDIA")
            ok = resp.get("ok") and not media_errors
            results.append(
                {
                    "case_id": case_id,
                    "ok": ok,
                    "status": resp.get("status"),
                    "answer_mode": resp.get("answer_mode"),
                    "media_errors": media_errors,
                    "media_empty": media_is_empty(resp.get("media")),
                }
            )

    passed = sum(1 for item in results if item["ok"])
    report = {
        "base_url": args.base_url,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "cases": results,
    }
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Local parity smoke: {passed}/{len(results)} -> {OUT_PATH}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
