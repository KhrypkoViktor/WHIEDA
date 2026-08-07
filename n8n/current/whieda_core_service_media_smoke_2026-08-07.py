"""Local/staging smoke: service intents must return empty media (SERVICE-GREETING-NO-MEDIA).

Does NOT touch n8n, Telegram webhook, or production routing.
Default target: localhost Core with CORE_ROUTE_ADVISOR=core (owner runs route probe separately).

Usage:
  python whieda_core_service_media_smoke_2026-08-07.py
  python whieda_core_service_media_smoke_2026-08-07.py --base-url http://127.0.0.1:8080
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

try:
    import httpx
except ImportError:
    print("httpx required: pip install httpx", file=sys.stderr)
    raise

OUT_PATH = Path(__file__).resolve().parent / "_core_service_media_smoke_report.json"

CASES = [
    {"id": "SERVICE-GREETING-NO-MEDIA", "question": "привет"},
    {"id": "SERVICE-HELLO-NO-MEDIA", "question": "hello"},
    {"id": "SERVICE-SMALLTALK-NO-MEDIA", "question": "как дела"},
    {"id": "SERVICE-CAPABILITIES-NO-MEDIA", "question": "что ты умеешь"},
    {"id": "SERVICE-HELP-NO-MEDIA", "question": "помощь"},
]


def media_polluted(media: dict | None) -> bool:
    if not media:
        return False
    if media.get("photo_url"):
        return True
    videos = media.get("videos") or []
    documents = media.get("documents") or []
    return bool(videos or documents)


def run_case(client: httpx.Client, base_url: str, case: dict) -> dict:
    session = f"smoke-{case['id']}-{uuid.uuid4().hex[:8]}"
    payload = {
        "question": case["question"],
        "session": session,
        "tenant": "whieda",
        "country": "BY",
    }
    url = f"{base_url.rstrip('/')}/v1/advisor/query"
    try:
        resp = client.post(url, json=payload, headers={"host": "wwc.best"}, timeout=15.0)
    except httpx.HTTPError as exc:
        return {"id": case["id"], "ok": False, "error": str(exc)}

    body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    media = body.get("media") or {}
    polluted = media_polluted(media)
    return {
        "id": case["id"],
        "ok": resp.status_code == 200 and not polluted,
        "status": resp.status_code,
        "answer_mode": body.get("answer_mode"),
        "media": media,
        "polluted": polluted,
        "trace_id": body.get("trace_id"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Core service-intent media smoke")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()

    results = []
    with httpx.Client() as client:
        for case in CASES:
            results.append(run_case(client, args.base_url, case))

    passed = sum(1 for row in results if row.get("ok"))
    report = {
        "base_url": args.base_url,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "cases": results,
    }
    OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Service media smoke: {passed}/{len(results)} passed -> {OUT_PATH}")
    for row in results:
        mark = "OK" if row.get("ok") else "FAIL"
        print(f"  [{mark}] {row['id']}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
