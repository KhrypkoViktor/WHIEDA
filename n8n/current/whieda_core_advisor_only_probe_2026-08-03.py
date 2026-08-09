"""Latency/health probe for Platform Core advisor only (no legacy n8n)."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import uuid

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CORE_URL = "https://sysarchn8n.duckdns.org/whieda-platform/v1/advisor/query"

DEFAULT_CASES = [
    ("greeting", "привет"),
    ("capability", "что умеешь"),
    ("price_alias", "цена спирулина"),
    ("product_card", "активатор клеток"),
    ("clarify_price", "сколько стоит"),
    ("faq_business", "что такое pv"),
    ("photo", "фото спирулина"),
    ("compare", "сравни спирулина и активатор клеток"),
    ("basket", "стартовая корзина на 500"),
]


def run_case(case_id: str, question: str, timeout: float, core_host: str) -> dict:
    payload = {
        "session": f"core-probe-{uuid.uuid4().hex[:8]}",
        "question": question,
        "tenant": "whieda",
        "country": "BY",
        "language": "ru",
    }
    started = time.perf_counter()
    try:
        response = requests.post(
            CORE_URL,
            json=payload,
            timeout=timeout,
            headers={"x-trace-id": f"core-probe-{case_id}"},
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        return {
            "case_id": case_id,
            "question": question,
            "ok": response.status_code == 200,
            "status": response.status_code,
            "latency_ms": elapsed_ms,
            "mode": body.get("answer_mode"),
            "text_preview": str(body.get("answer_text") or "")[:240],
        }
    except requests.RequestException as exc:
        return {
            "case_id": case_id,
            "question": question,
            "ok": False,
            "status": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "error": str(exc),
            "mode": None,
            "text_preview": "",
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--core-host", default="wwc.best")
    args = parser.parse_args()

    results = []
    for index, (case_id, question) in enumerate(DEFAULT_CASES):
        if index:
            time.sleep(args.delay)
        results.append(run_case(case_id, question, args.timeout, args.core_host))

    latencies = [row["latency_ms"] for row in results if row.get("ok")]
    report = {
        "cases": results,
        "summary": {
            "total": len(results),
            "ok": sum(1 for row in results if row.get("ok")),
            "latency_ms": {
                "p50": statistics.median(latencies) if latencies else None,
                "p95": sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)] if latencies else None,
                "max": max(latencies) if latencies else None,
            },
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["summary"]["ok"] == report["summary"]["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
