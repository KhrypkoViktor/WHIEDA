"""Probe safe SQL-quality candidates against live website API."""

from __future__ import annotations

import argparse
import json
import time
import uuid
from collections import Counter
from datetime import date
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings()

BASE_DIR = Path(__file__).resolve().parent
EXPORT_DIR = BASE_DIR.parent / "live-exports" / date.today().isoformat()
PUBLIC_API = "https://sysarchn8n.duckdns.org/webhook/wwc-advisor-public-v1"


def classify(body: dict, status: int) -> str:
    if status != 200 or not isinstance(body, dict) or not body.get("answer_text"):
        return "infrastructure_error"
    route = str(body.get("route") or "").lower()
    mode = str(body.get("answer_mode") or "").lower()
    if "dify" in route or "dify" in mode:
        return "unexpected_dify"
    if body.get("ok") is True and route in {"answer", "structured", "fallback"}:
        if "structured" in mode or body.get("product"):
            return "covered_sql"
        return "intent_or_route_gap"
    return "intent_or_route_gap"


def load_candidates(path: Path) -> list[dict]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    return [row for row in rows if row.get("review_bucket") == "safe_test_candidate"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, default=EXPORT_DIR / "WHIEDA_question_candidates.json")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if not args.candidates.exists():
        raise SystemExit(f"Candidates file not found: {args.candidates}")

    results = []
    for row in load_candidates(args.candidates)[: args.limit]:
        started = time.monotonic()
        session_id = f"sql-quality-{uuid.uuid4().hex[:10]}"
        response = requests.post(
            PUBLIC_API,
            json={"question": row["canonical_question"], "session_id": session_id, "ref": "ladnaya", "locale": "ru"},
            verify=False,
            timeout=90,
        )
        try:
            body = response.json()
        except ValueError:
            body = {}
        product = body.get("product") if isinstance(body, dict) else {}
        sku = product.get("sku") if isinstance(product, dict) else None
        results.append(
            {
                "candidate_id": row["candidate_id"],
                "question": row["canonical_question"],
                "intent_id": row["intent_id"],
                "entity_id": row["entity_id"],
                "http_status": response.status_code,
                "classification": classify(body, response.status_code),
                "route": body.get("route") if isinstance(body, dict) else None,
                "answer_mode": body.get("answer_mode") if isinstance(body, dict) else None,
                "sku": sku,
                "answer_preview": str((body or {}).get("answer_text") or "")[:160],
                "elapsed_seconds": round(time.monotonic() - started, 2),
            }
        )
        time.sleep(0.3)

    counts = dict(Counter(row["classification"] for row in results))
    report = {
        "date": date.today().isoformat(),
        "api": PUBLIC_API,
        "tested": len(results),
        "counts": counts,
        "results": results,
    }
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = EXPORT_DIR / "WHIEDA_sql_quality_safe_probe.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(out), "tested": len(results), "counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
