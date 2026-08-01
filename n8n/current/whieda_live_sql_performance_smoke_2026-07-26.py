"""Measure live SQL response execution time on the dedicated test account."""

from __future__ import annotations

import importlib.util
import json
import math
import statistics
import time
from datetime import date
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR.parent / "live-exports" / date.today().isoformat()
PROMPTS = [
    "расскажи про активатор", "а сколько стоит?", "дай видео", "компьютерные очки",
    "а сколько стоит?", "палантин", "дай фото", "что ты умеешь", "что такое PV", "Ба-Гуа",
]


def load_smoke():
    path = BASE_DIR / "whieda_live_demo_smoke_v2_2026-07-14.py"
    spec = importlib.util.spec_from_file_location("whieda_smoke", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def execution_ms(session, smoke, execution_id):
    raw = session.get(f"{smoke.BASE_URL}/rest/executions/{execution_id}?includeData=true", verify=False, timeout=60).json()["data"]["data"]
    run_data = smoke.decode_graph(json.loads(raw))["resultData"]["runData"]
    return sum(sum((run.get("executionTime") or 0) for run in runs) for runs in run_data.values()), list(run_data)


def percentile(values, point):
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * point) - 1)
    return ordered[index]


def main():
    smoke = load_smoke(); session = smoke.login(); last = max(smoke.execution_ids(session), default=0)
    rows = []
    for prompt in PROMPTS:
        execution_id, summary, errors, elapsed, last = smoke.run_prompt(session, prompt, {}, last)
        internal_ms, nodes = execution_ms(session, smoke, execution_id) if execution_id else (None, [])
        rows.append({"prompt": prompt, "execution_id": execution_id, "internal_ms": internal_ms, "poll_elapsed_seconds": elapsed, "status": (summary or {}).get("execution_status"), "route": (summary or {}).get("route"), "dify_called": (summary or {}).get("dify_called"), "old_structured_nodes": [n for n in nodes if n in {"Google Sheets: Products_Prices", "Google Sheets: Product_Aliases", "Postgres: Product Cards", "Postgres: Resource Links"}]})
        time.sleep(0.5)
    times = [row["internal_ms"] for row in rows if row["internal_ms"] is not None]
    report = {"date": date.today().isoformat(), "rows": rows, "metrics": {"count": len(times), "p50_ms": statistics.median(times), "p95_ms": percentile(times, 0.95), "max_ms": max(times)}, "pass": all(row["status"] == "success" and row["route"] == "answer" and not row["dify_called"] and not row["old_structured_nodes"] for row in rows) and percentile(times, 0.95) <= 3000}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUT_DIR / "WHIEDA_live_sql_performance_smoke.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"target": str(target), "metrics": report["metrics"], "pass": report["pass"]}, ensure_ascii=False))


if __name__ == "__main__": main()
