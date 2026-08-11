"""HTTP runner for Telegram experience flows."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LAB = Path(__file__).resolve().parent
ACCEPTANCE = Path(__file__).resolve().parents[2] / "acceptance"
if str(ACCEPTANCE) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE))

from lab.target import build_advisor_request, extract_response_fields, load_target  # noqa: E402
from lab.transport import UrllibTransport  # noqa: E402

_spec = importlib.util.spec_from_file_location("tg_assertions", LAB / "assertions.py")
assert _spec and _spec.loader
_assertions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_assertions)
evaluate_turn = _assertions.evaluate_turn


def run_telegram_flows(
    *,
    target_path: Path,
    flows: list[dict[str, Any]],
    category: str | None = None,
    flow_id: str | None = None,
    timeout_seconds: float = 8.0,
    fail_fast: bool = False,
) -> dict[str, Any]:
    target = load_target(target_path)
    selected = flows
    if category:
        selected = [f for f in selected if f.get("category") == category]
    if flow_id:
        selected = [f for f in selected if f.get("flow_id") == flow_id]

    client = UrllibTransport()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    results: list[dict[str, Any]] = []
    category_stats: dict[str, dict[str, int]] = {}

    for flow in selected:
        cat = str(flow.get("category") or "unknown")
        category_stats.setdefault(cat, {"flows": 0, "flows_passed": 0, "turns": 0, "turns_passed": 0})
        category_stats[cat]["flows"] += 1
        flow_failed = False
        flow_session = f"{flow.get('session')}-{run_id}"
        for turn in flow.get("turns") or []:
            case = {
                **turn,
                "session": flow_session,
                "country": flow.get("country") or "BY",
                "surface": "telegram",
            }
            method, url, headers, body = build_advisor_request(target, case)
            body["surface"] = "telegram"
            t0 = time.perf_counter()
            status, text, latency_ms = client.request(
                method, url, headers=headers, body=body, timeout_seconds=timeout_seconds
            )
            latency_ms = latency_ms or (time.perf_counter() - t0) * 1000
            try:
                payload = json.loads(text) if text else {}
            except json.JSONDecodeError:
                payload = {}
            extracted = extract_response_fields(target, payload)
            extracted["answer_text"] = payload.get("answer_text") or extracted.get("answer_text")
            extracted["answer_mode"] = payload.get("answer_mode") or extracted.get("answer_mode")
            extracted["gap_kind"] = payload.get("gap_kind")
            extracted["media"] = payload.get("media") or {}
            verdict = evaluate_turn(
                flow=flow,
                turn=turn,
                http_status=status,
                extracted=extracted,
                latency_ms=round(latency_ms, 2),
            )
            results.append(verdict)
            category_stats[cat]["turns"] += 1
            if verdict["status"] == "PASS":
                category_stats[cat]["turns_passed"] += 1
            else:
                flow_failed = True
                if fail_fast:
                    break
        if not flow_failed:
            category_stats[cat]["flows_passed"] += 1
        elif fail_fast:
            break

    turn_pass = sum(1 for r in results if r.get("status") == "PASS")
    turn_fail = sum(1 for r in results if r.get("status") != "PASS")
    flows_passed = 0
    for flow in selected:
        flow_id = flow.get("flow_id")
        flow_results = [r for r in results if r.get("flow_id") == flow_id]
        if flow_results and all(r.get("status") == "PASS" for r in flow_results):
            flows_passed += 1

    return {
        "run_id": run_id,
        "status": "PASS" if turn_fail == 0 and selected else ("FAIL" if turn_fail else "NOT_RUN"),
        "flows_total": len(selected),
        "flows_passed": flows_passed,
        "flows_failed": len(selected) - flows_passed,
        "turns_total": len(results),
        "turns_passed": turn_pass,
        "turns_failed": turn_fail,
        "category_stats": category_stats,
        "summary_line": f"Telegram experience: {flows_passed}/{len(selected)} flows, {turn_pass}/{len(results)} turns passed",
        "results": results,
    }
