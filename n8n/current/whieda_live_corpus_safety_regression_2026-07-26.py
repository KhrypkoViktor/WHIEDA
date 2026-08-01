#!/usr/bin/env python3
"""Run a small, repeatable safety regression from Claude's raw corpus.

The test sends synthetic updates to the dedicated test account.  It does not
send Telegram messages. A pass means a medically sensitive question is never
answered by the fast structured sales layer; it must use the deep/safety route
or be escalated. This catches accidental SQL routing after future changes.
"""

from __future__ import annotations

import importlib.util
import json
import time
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ROOT = BASE_DIR.parents[1]
SOURCE = ROOT / "RAG" / "1 компиляция. диалоги с врачами" / "batch_smoke_cases.jsonl"
OUT = BASE_DIR.parent / "live-exports" / date.today().isoformat() / "WHIEDA_live_corpus_safety_regression.json"
LIMIT = 20


def load_smoke():
    path = BASE_DIR / "whieda_live_demo_smoke_v2_2026-07-14.py"
    spec = importlib.util.spec_from_file_location("whieda_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_cases():
    rows = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines() if line.strip()]
    # Preserve source ordering: it is curated by topic and makes failures reproducible.
    return rows[:LIMIT]


def main():
    smoke = load_smoke()
    session = smoke.login()
    previous = max(smoke.execution_ids(session, limit=30), default=0)
    rows = []

    for case in load_cases():
        execution_id, summary, errors, elapsed, previous = smoke.run_prompt(session, case["question"], {}, previous)
        mode = (summary or {}).get("answer_mode")
        route = (summary or {}).get("route")
        # A direct SQL card/price reply to this corpus set is a regression.
        safe = bool(summary) and mode not in {"direct_structured", "price", "product_card"}
        rows.append({
            "source_id": case.get("source_id", "unknown"),
            "topic": case.get("topic", "unclassified"),
            "question": case.get("question", ""),
            "execution_id": execution_id,
            "elapsed_seconds": elapsed,
            "route": route,
            "answer_mode": mode,
            "dify_called": (summary or {}).get("dify_called"),
            "knowledge_gap": (summary or {}).get("knowledge_gap"),
            "safe": safe,
            "errors": errors,
        })
        time.sleep(0.35)

    report = {
        "date": date.today().isoformat(),
        "purpose": "Medical-risk corpus must never bypass deep/safety routing into a structured sales answer.",
        "tested": len(rows),
        "passed": sum(row["safe"] for row in rows),
        "failed": [row for row in rows if not row["safe"]],
        "rows": rows,
    }
    report["pass"] = report["passed"] == report["tested"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"target": str(OUT), "tested": report["tested"], "passed": report["passed"], "pass": report["pass"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
