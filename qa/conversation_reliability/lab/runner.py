"""HTTP runner for conversation reliability flows."""

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

_spec = importlib.util.spec_from_file_location("conv_assertions", LAB / "assertions.py")
assert _spec and _spec.loader
_assertions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_assertions)
evaluate_turn = _assertions.evaluate_turn


def run_conversation_flows(
    *,
    target_path: Path,
    flows: list[dict[str, Any]],
    priority: str | None = None,
    flow_id: str | None = None,
    timeout_seconds: float = 8.0,
    fail_fast: bool = False,
) -> dict[str, Any]:
    target = load_target(target_path)
    selected = flows
    if priority:
        selected = [f for f in selected if f.get("priority") == priority]
    if flow_id:
        selected = [f for f in selected if f.get("flow_id") == flow_id]

    client = UrllibTransport()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    results: list[dict[str, Any]] = []
    flows_passed = 0
    flows_failed = 0

    for flow in selected:
        flow_failed = False
        flow_results: list[dict[str, Any]] = []
        flow_session = f"{flow.get('session')}-{run_id}"
        for turn in flow.get("turns") or []:
            case = {**turn, "session": flow_session, "country": flow.get("country") or "BY"}
            method, url, headers, body = build_advisor_request(target, case)
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
            extracted["raw_payload"] = payload
            extracted["answer_text"] = payload.get("answer_text") or extracted.get("answer_text")
            extracted["answer_mode"] = payload.get("answer_mode") or extracted.get("answer_mode")
            extracted["gap_kind"] = payload.get("gap_kind")
            extracted["context"] = payload.get("context") or extracted.get("context")
            verdict = evaluate_turn(
                flow=flow,
                turn=turn,
                http_status=status,
                extracted=extracted,
                latency_ms=round(latency_ms, 2),
            )
            flow_results.append(verdict)
            if verdict["status"] != "PASS":
                flow_failed = True
                if fail_fast:
                    break
        results.extend(flow_results)
        if flow_failed:
            flows_failed += 1
            if fail_fast:
                break
        else:
            flows_passed += 1

    turn_pass = sum(1 for r in results if r.get("status") == "PASS")
    turn_fail = sum(1 for r in results if r.get("status") != "PASS")

    return {
        "run_id": run_id,
        "status": "PASS" if turn_fail == 0 and selected else ("FAIL" if turn_fail else "NOT_RUN"),
        "flows_total": len(selected),
        "flows_passed": flows_passed,
        "flows_failed": flows_failed,
        "turns_total": len(results),
        "turns_passed": turn_pass,
        "turns_failed": turn_fail,
        "summary_line": f"Conversation: {flows_passed}/{len(selected)} flows, {turn_pass}/{len(results)} turns passed",
        "results": results,
    }
