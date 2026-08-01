"""Exercise safe Claude-derived questions against live WHIEDA and classify outcomes.

It runs only on the dedicated test account. Medical and acute-risk questions are
kept out of the live batch and remain in the doctor-review queue.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import time
from collections import Counter
from datetime import date
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
SOURCE_DIR = Path(r"D:\Projects\WHIEDA\RAG\1 компиляция. диалоги с врачами")
OUT_DIR = BASE_DIR.parent / "live-exports" / date.today().isoformat()
RISK = re.compile(r"онколог|беремен|кардиостимулятор|стент|инсульт|кровотеч|операц|диагноз|лечить|температур|глауком|эпилеп|варфарин|метастаз|аритми|гипертон|ребен|дет[еяй]|диализ|тромбоз|сетчатк|зрени[ея].*ухудш|кровоизлия|отек|помутнен|щитовид|кист[ауые]|кариес|имплант|зуб|глаз|линз|боль|поясниц|колен|стоп", re.I)


def load_smoke():
    path = BASE_DIR / "whieda_live_demo_smoke_v2_2026-07-14.py"
    spec = importlib.util.spec_from_file_location("whieda_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidates(limit: int):
    seen, rows = set(), []
    for name in ("batch_objections.jsonl", "batch_faq_candidates.jsonl", "batch_questions.jsonl", "batch_smoke_cases.jsonl"):
        path = SOURCE_DIR / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            question = str(row.get("question") or row.get("objection") or "").strip()
            key = re.sub(r"\s+", " ", question.lower())
            if not question or key in seen or RISK.search(question):
                continue
            seen.add(key)
            rows.append({"question": question, "source_id": row.get("source_id", ""), "topic": row.get("topic") or row.get("category") or "", "source_file": name})
            if len(rows) >= limit:
                return rows
    return rows


def classify(summary):
    if not summary:
        return "execution_missing"
    if summary.get("execution_status") != "success":
        return "execution_error"
    if summary.get("dify_called"):
        return "wrongly_sent_to_dify"
    if summary.get("route") == "answer" and summary.get("structured_hit") is True and not summary.get("knowledge_gap"):
        return "covered_sql"
    if summary.get("knowledge_gap"):
        return "structured_gap"
    return "intent_or_route_gap"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    smoke = load_smoke(); session = smoke.login(); last = max(smoke.execution_ids(session), default=0)
    results = []
    for item in candidates(args.limit):
        started = time.monotonic(); message_id = smoke.send(session, item["question"])
        execution_id, summary = smoke.wait_for(session, message_id, last)
        last = max(last, execution_id or 0)
        result = {**item, "execution_id": execution_id, "elapsed_seconds": round(time.monotonic() - started, 2), "classification": classify(summary), "route": (summary or {}).get("route"), "answer_mode": (summary or {}).get("answer_mode"), "sku": ((summary or {}).get("structured_match") or {}).get("sku"), "dify_called": (summary or {}).get("dify_called"), "knowledge_gap": (summary or {}).get("knowledge_gap")}
        results.append(result)
        time.sleep(0.5)
    counts = Counter(row["classification"] for row in results)
    report = {"date": date.today().isoformat(), "tested": len(results), "counts": counts, "results": results, "scope": "safe candidate questions only; medical risk excluded"}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / "WHIEDA_claude_candidate_regression.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"target": str(target), "tested": len(results), "counts": counts}, ensure_ascii=False, default=dict))


if __name__ == "__main__":
    main()
