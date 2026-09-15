"""Golden corpus HTTP runner against local Platform Core."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACCEPTANCE = Path(__file__).resolve().parents[2] / "acceptance"
if str(ACCEPTANCE) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE))

from lab.target import build_advisor_request, extract_response_fields  # noqa: E402
from lab.transport import Transport  # noqa: E402

_spec = importlib.util.spec_from_file_location("golden_http_assertions", Path(__file__).resolve().parent / "http_assertions.py")
assert _spec and _spec.loader
_assertions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_assertions)
evaluate_flow_turn = _assertions.evaluate_flow_turn
evaluate_negative_fixture = _assertions.evaluate_negative_fixture
evaluate_positive_case = _assertions.evaluate_positive_case


def _case_request_payload(case: dict[str, Any], *, session: str) -> dict[str, Any]:
    inp = case.get("input") or {}
    ctx = case.get("context_before") or {}
    return {
        "input": inp.get("user_text"),
        "session": session,
        "country": inp.get("country") or "BY",
        "language": inp.get("language") or "ru",
        "surface": "telegram",
        "context_before": ctx if isinstance(ctx, list) else [],
    }


def _advisor_call(
    *,
    target: dict[str, Any],
    case_payload: dict[str, Any],
    client: Transport,
    timeout_seconds: float,
) -> tuple[int, dict[str, Any], dict[str, Any], float]:
    method, url, headers, body = build_advisor_request(target, case_payload)
    body["surface"] = "telegram"
    t0 = time.perf_counter()
    try:
        status, text, latency_ms = client.request(
            method,
            url,
            headers=headers,
            body=body,
            timeout_seconds=timeout_seconds,
        )
    except TimeoutError:
        return 0, {}, {}, (time.perf_counter() - t0) * 1000
    latency_ms = latency_ms or (time.perf_counter() - t0) * 1000
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        payload = {}
    extracted = extract_response_fields(target, payload if isinstance(payload, dict) else {})
    if isinstance(payload, dict):
        extracted["answer_text"] = payload.get("answer_text") or extracted.get("answer_text")
        extracted["answer_mode"] = payload.get("answer_mode") or extracted.get("answer_mode")
        extracted["gap_kind"] = payload.get("gap_kind")
        extracted["context"] = payload.get("context")
        extracted["media"] = payload.get("media")
    return status, payload if isinstance(payload, dict) else {}, extracted, latency_ms


def run_golden_http(
    *,
    target: dict[str, Any],
    cases: list[dict[str, Any]],
    flows: list[dict[str, Any]],
    negative_fixtures: list[dict[str, Any]] | None = None,
    client: Transport,
    priority: str | None = None,
    case_id: str | None = None,
    include_negative: bool = False,
    negative_only: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    timeout_seconds = float((target.get("timeouts") or {}).get("request_seconds") or 12.0)
    priority_filter = str(priority or "").upper() if priority else None
    selected_cases: list[dict[str, Any]] = []
    selected_flows: list[dict[str, Any]] = []
    if negative_only:
        selected_cases = []
        selected_flows = []
    else:
        selected_cases = cases
        selected_flows = flows
        if priority_filter:
            selected_cases = [
                c for c in cases if str(c.get("priority") or "").upper() == priority_filter
            ]
        if case_id:
            selected_cases = [c for c in selected_cases if c.get("case_id") == case_id]

        flow_case_ids = {
            str(turn.get("case_id") or "")
            for flow in selected_flows
            for turn in (flow.get("turns") or [])
        }
        selected_cases = [
            case for case in selected_cases if str(case.get("case_id") or "") not in flow_case_ids
        ]

    results: list[dict[str, Any]] = []
    if dry_run:
        planned_negative = len(negative_fixtures or []) if (include_negative or negative_only) else 0
        return {
            "run_id": run_id,
            "status": "DRY_RUN",
            "dry_run": True,
            "planned_cases": len(selected_cases),
            "planned_flows": len(selected_flows),
            "planned_negative": planned_negative,
            "results": [],
        }

    for case in selected_cases:
        session = f"golden-case-{run_id}-{case.get('case_id')}"
        if str(case.get("execution_surface") or "advisor_http") != "advisor_http":
            verdict = evaluate_positive_case(
                case=case,
                http_status=0,
                payload={},
                extracted={},
                latency_ms=0.0,
            )
            verdict["kind"] = "case"
            results.append(verdict)
            continue
        case_payload = _case_request_payload(case, session=session)
        status, payload, extracted, latency_ms = _advisor_call(
            target=target,
            case_payload=case_payload,
            client=client,
            timeout_seconds=timeout_seconds,
        )
        verdict = evaluate_positive_case(
            case=case,
            http_status=status,
            payload=payload,
            extracted=extracted,
            latency_ms=latency_ms,
        )
        verdict["kind"] = "case"
        results.append(verdict)

    for flow in selected_flows:
        flow_session = f"{flow.get('session') or flow.get('flow_id')}-{run_id}"
        flow_context: dict[str, Any] = {}
        flow_failed = False
        for turn in flow.get("turns") or []:
            turn_prio = str(turn.get("priority") or flow.get("priority") or "P1").upper()
            turn_role = "setup" if priority_filter and turn_prio != priority_filter else "assertion"
            if flow_failed:
                results.append(
                    {
                        "flow_id": flow.get("flow_id"),
                        "turn": turn.get("turn"),
                        "case_id": turn.get("case_id"),
                        "class": turn.get("class"),
                        "priority": turn.get("priority") or flow.get("priority"),
                        "turn_role": turn_role,
                        "status": "NOT_RUN_DEPENDENCY",
                        "reason": "previous turn failed",
                        "kind": "flow_turn",
                    }
                )
                continue
            if str(turn.get("execution_surface") or "advisor_http") != "advisor_http":
                verdict = evaluate_flow_turn(
                    flow=flow,
                    turn=turn,
                    flow_context=flow_context,
                    http_status=0,
                    payload={},
                    extracted={},
                    latency_ms=0.0,
                )
                verdict["kind"] = "flow_turn"
                verdict["turn_role"] = turn_role
                results.append(verdict)
                if verdict.get("status") not in {"PASS", "SKIP_SURFACE"}:
                    flow_failed = True
                continue
            case_payload = _case_request_payload(turn, session=flow_session)
            status, payload, extracted, latency_ms = _advisor_call(
                target=target,
                case_payload=case_payload,
                client=client,
                timeout_seconds=timeout_seconds,
            )
            verdict = evaluate_flow_turn(
                flow=flow,
                turn=turn,
                flow_context=flow_context,
                http_status=status,
                payload=payload,
                extracted=extracted,
                latency_ms=latency_ms,
            )
            verdict["kind"] = "flow_turn"
            verdict["turn_role"] = turn_role
            results.append(verdict)
            if verdict.get("status") not in {"PASS", "SKIP_SURFACE"}:
                flow_failed = True
            elif verdict.get("status") == "PASS":
                flow_context = dict(payload.get("context") or flow_context)

    if (include_negative or negative_only) and negative_fixtures:
        for fixture in negative_fixtures:
            session = f"golden-neg-{run_id}-{fixture.get('fixture_id')}"
            inp = fixture.get("input") or {}
            case_payload = {
                "input": inp.get("user_text"),
                "session": session,
                "country": inp.get("country") or "BY",
                "language": inp.get("language") or "ru",
                "surface": "telegram",
            }
            status, payload, extracted, latency_ms = _advisor_call(
                target=target,
                case_payload=case_payload,
                client=client,
                timeout_seconds=timeout_seconds,
            )
            verdict = evaluate_negative_fixture(
                fixture=fixture,
                http_status=status,
                payload=payload,
                extracted=extracted,
                latency_ms=latency_ms,
            )
            verdict["kind"] = "negative"
            verdict["priority"] = fixture.get("priority")
            results.append(verdict)

    fail_count = sum(
        1
        for r in results
        if r.get("status") in {"FAIL", "TIMEOUT", "NEGATIVE_FAIL"}
    )
    status = "PASS" if fail_count == 0 and results else ("FAIL" if fail_count else "NOT_RUN")
    return {
        "run_id": run_id,
        "status": status,
        "results": results,
    }
