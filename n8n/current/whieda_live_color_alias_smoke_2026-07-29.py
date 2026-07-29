"""Focused synthetic live smoke for Release 3.3 colour and belt aliases."""
from __future__ import annotations

import importlib.util
import json
import time
from datetime import date
from pathlib import Path


BASE = Path(__file__).resolve().parent
RUNNER = BASE / "whieda_live_demo_smoke_v2_2026-07-14.py"
OUT = BASE.parent / "live-exports" / date.today().isoformat() / "WHIEDA_live_color_alias_smoke_2026-07-29.json"


def load_runner():
    spec = importlib.util.spec_from_file_location("whieda_live_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


CASES = [
    {"id": "COLOR-01-RED-PRICE", "prompt": "сколько стоит красный эликсир", "sku": "F001-02", "contains": "цен"},
    {"id": "COLOR-02-GREEN-CLARIFY", "prompt": "расскажи про зелёный", "sku": "F003-02", "contains": "Саньцин", "mode": "direct_structured_clarify"},
    {"id": "COLOR-03-BLUE-PHOTO", "prompt": "дай фото синего эликсира", "sku": "F002-02", "photo": True},
    {"id": "COLOR-04-RED-CLARIFY", "prompt": "красный", "sku": "F001-02", "contains": "Эликсир Фохоу", "mode": "direct_structured_clarify"},
    {"id": "COLOR-05-RED-CONTEXT-PRICE", "prompt": "цена", "sku": "F001-02", "contains": "цен"},
    {"id": "BELT-01-CLARIFY", "prompt": "пояс", "sku": "T003", "contains": "Магнитный пояс", "mode": "direct_structured_clarify"},
    {"id": "BELT-02-PRICE", "prompt": "сколько стоит магнитный пояс", "sku": "T003", "contains": "цен"},
    {"id": "BELT-03-PHOTO", "prompt": "дай фото пояса", "sku": "T003", "photo": True},
    {"id": "BELT-04-RED-BELT", "prompt": "расскажи про красный пояс", "sku": "T003", "contains": "Магнитный пояс", "mode": "direct_structured_clarify", "forbid": "Эликсир Фохоу"},
]


def main() -> None:
    runner = load_runner()
    session = runner.login()
    previous = max(runner.execution_ids(session, limit=25), default=0)
    rows = []
    for case in CASES:
        started = time.monotonic()
        message_id = runner.send(session, case["prompt"])
        execution_id, summary = runner.wait_for(session, message_id, previous)
        previous = max(previous, execution_id or previous)
        errors = []
        if not summary:
            errors.append("execution_not_found")
        else:
            if summary.get("execution_status") != "success": errors.append("execution_not_success")
            if summary.get("route") != "answer": errors.append("route=" + str(summary.get("route")))
            if not summary.get("structured_hit"): errors.append("structured_hit=false")
            if summary.get("dify_called"): errors.append("dify_called=true")
            match = summary.get("structured_match") or {}
            if match.get("sku") != case["sku"]: errors.append("sku=" + str(match.get("sku")))
            reply = str(summary.get("reply_text") or "")
            if case.get("contains") and case["contains"].lower() not in reply.lower(): errors.append("missing=" + case["contains"])
            if case.get("mode") and summary.get("answer_mode") != case["mode"]: errors.append("mode=" + str(summary.get("answer_mode")))
            if case.get("photo") and not summary.get("photo_url"): errors.append("photo_missing")
            if case.get("forbid") and case["forbid"].lower() in reply.lower(): errors.append("forbidden=" + case["forbid"])
        rows.append({
            "id": case["id"], "prompt": case["prompt"], "execution_id": execution_id,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "status": "pass" if not errors else "fail", "errors": errors, "summary": summary,
        })
        time.sleep(1)
    passed = sum(row["status"] == "pass" for row in rows)
    report = {"meta": {"total": len(rows), "passed": passed, "failed": len(rows) - passed}, "results": rows}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"meta": report["meta"], "failures": [{"id": row["id"], "errors": row["errors"]} for row in rows if row["errors"]], "report": str(OUT)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
