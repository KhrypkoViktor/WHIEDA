"""Human language rails HTTP runner against local Platform Core."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACCEPTANCE = Path(__file__).resolve().parents[2] / "acceptance"
if str(ACCEPTANCE) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE))

from lab.target import build_advisor_request, extract_response_fields  # noqa: E402
from lab.transport import Transport  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "hlr_http_assertions", Path(__file__).resolve().parent / "http_assertions.py"
)
assert _spec and _spec.loader
_assertions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_assertions)
evaluate_hlr_assertion = _assertions.evaluate_hlr_assertion
evaluate_setup_turn = _assertions.evaluate_setup_turn


def group_flow_turns(cases: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in cases:
        grouped[str(row.get("flow_id") or "")].append(row)
    for fid in grouped:
        grouped[fid].sort(key=lambda r: int(r.get("turn_index") or 0))
    return grouped


def select_flow_ids(
    cases: list[dict[str, Any]],
    *,
    priority: str | None = None,
    accepted_only: bool = True,
) -> set[str]:
    flow_ids: set[str] = set()
    prio = str(priority or "").upper() if priority else None
    for row in cases:
        if row.get("turn_role") != "assertion":
            continue
        if accepted_only and row.get("acceptance_status") != "accepted":
            continue
        if prio and str(row.get("priority") or "").upper() != prio:
            continue
        flow_ids.add(str(row.get("flow_id") or ""))
    return flow_ids


def pending_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    pending = [
        row
        for row in cases
        if row.get("turn_role") == "assertion"
        and row.get("acceptance_status") in {"pending_surface", "pending_policy"}
    ]
    by_status: dict[str, int] = defaultdict(int)
    by_rail: dict[str, int] = defaultdict(int)
    for row in pending:
        by_status[str(row.get("acceptance_status") or "")] += 1
        by_rail[str(row.get("expected_rail") or "")] += 1
    return {
        "count": len(pending),
        "by_status": dict(by_status),
        "by_rail": dict(by_rail),
        "case_ids": [row.get("case_id") for row in pending],
    }


def _advisor_call(
    *,
    target: dict[str, Any],
    turn: dict[str, Any],
    session: str,
    client: Transport,
    timeout_seconds: float,
) -> tuple[int, dict[str, Any], dict[str, Any], float]:
    payload_case = {
        "user_text": turn.get("user_text"),
        "session": session,
        "country": target.get("default_country") or "BY",
        "language": target.get("default_language") or "ru",
        "context_before": turn.get("context_before") or [],
    }
    method, url, headers, body = build_advisor_request(target, payload_case)
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
    return status, payload if isinstance(payload, dict) else {}, extracted, latency_ms


def run_hlr_http(
    *,
    target: dict[str, Any],
    cases: list[dict[str, Any]],
    client: Transport | None,
    priority: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    timeout_seconds = float((target.get("timeouts") or {}).get("request_seconds") or 5.0)
    run_timeout_seconds = float((target.get("timeouts") or {}).get("run_seconds") or 300.0)
    started = time.perf_counter()

    accepted_assertions = [
        c
        for c in cases
        if c.get("turn_role") == "assertion" and c.get("acceptance_status") == "accepted"
    ]
    selected_flow_ids = select_flow_ids(cases, priority=priority, accepted_only=True)
    pending = pending_summary(cases)

    if dry_run:
        selected_accepted = [
            c for c in accepted_assertions if c.get("flow_id") in selected_flow_ids
        ]
        return {
            "run_id": run_id,
            "status": "DRY_RUN",
            "dry_run": True,
            "selected_accepted": len(selected_accepted),
            "selected_flows": len(selected_flow_ids),
            "pending": pending,
            "results": [],
        }

    if client is None:
        raise ValueError("client is required for live runs")

    grouped = group_flow_turns(cases)
    results: list[dict[str, Any]] = [
        {
            "case_id": row.get("case_id"),
            "flow_id": row.get("flow_id"),
            "turn_index": row.get("turn_index"),
            "expected_rail": row.get("expected_rail"),
            "acceptance_status": row.get("acceptance_status"),
            "status": "NOT_RUN_PENDING",
            "kind": "assertion",
        }
        for row in cases
        if row.get("turn_role") == "assertion"
        and row.get("acceptance_status") in {"pending_surface", "pending_policy"}
    ]

    for fid in sorted(selected_flow_ids):
        if time.perf_counter() - started > run_timeout_seconds:
            results.append(
                {
                    "flow_id": fid,
                    "status": "NOT_RUN_TIMEOUT",
                    "reason": "run timeout exceeded",
                    "kind": "flow",
                }
            )
            continue

        session = f"hlr-{fid}-{run_id}"
        flow_failed_setup = False
        flow_context: dict[str, Any] = {}
        explicit_setup_seen = False
        for turn in grouped.get(fid, []):
            if time.perf_counter() - started > run_timeout_seconds:
                if turn.get("turn_role") == "assertion" and turn.get("acceptance_status") == "accepted":
                    results.append(
                        {
                            "case_id": turn.get("case_id"),
                            "flow_id": fid,
                            "status": "NOT_RUN_TIMEOUT",
                            "expected_rail": turn.get("expected_rail"),
                            "kind": "assertion",
                        }
                    )
                continue

            role = turn.get("turn_role")
            if role == "setup":
                status, payload, extracted, latency_ms = _advisor_call(
                    target=target,
                    turn=turn,
                    session=session,
                    client=client,
                    timeout_seconds=timeout_seconds,
                )
                setup_verdict = evaluate_setup_turn(
                    http_status=status, payload=payload, latency_ms=latency_ms
                )
                setup_verdict.update(
                    {
                        "flow_id": fid,
                        "turn_index": turn.get("turn_index"),
                        "kind": "setup",
                        "turn_role": "setup",
                    }
                )
                results.append(setup_verdict)
                if setup_verdict.get("status") != "PASS":
                    flow_failed_setup = True
                explicit_setup_seen = True
                ctx = payload.get("context") or {}
                if isinstance(ctx, dict):
                    flow_context.update(ctx)
                continue

            if role != "assertion":
                continue

            # Some regression cases carry an isolated context_before instead
            # of explicit setup rows. Materialize that context as real user
            # turns so the HTTP contract exercises the same session state as
            # Telegram, rather than trusting a field Core does not consume.
            if not explicit_setup_seen and turn.get("context_before"):
                for setup_index, message in enumerate(turn.get("context_before") or [], start=1):
                    if str(message.get("role") or "user") != "user":
                        continue
                    synthetic = {"user_text": str(message.get("text") or ""), "context_before": []}
                    status, payload, extracted, latency_ms = _advisor_call(
                        target=target,
                        turn=synthetic,
                        session=session,
                        client=client,
                        timeout_seconds=timeout_seconds,
                    )
                    setup_verdict = evaluate_setup_turn(
                        http_status=status, payload=payload, latency_ms=latency_ms
                    )
                    setup_verdict.update(
                        {
                            "flow_id": fid,
                            "turn_index": f"synthetic-{setup_index}",
                            "kind": "setup",
                            "turn_role": "synthetic_setup",
                        }
                    )
                    results.append(setup_verdict)
                    if setup_verdict.get("status") != "PASS":
                        flow_failed_setup = True
                    ctx = payload.get("context") or {}
                    if isinstance(ctx, dict):
                        flow_context.update(ctx)
                explicit_setup_seen = True

            acceptance = turn.get("acceptance_status")
            if acceptance != "accepted":
                # Pending assertions are reported before execution and never
                # make an HTTP call. A later accepted turn cannot rely on an
                # intentionally unexecuted pending turn for session context.
                flow_failed_setup = True
                continue

            if flow_failed_setup:
                results.append(
                    {
                        "case_id": turn.get("case_id"),
                        "flow_id": fid,
                        "turn_index": turn.get("turn_index"),
                        "expected_rail": turn.get("expected_rail"),
                        "expected_mode": turn.get("expected_mode"),
                        "priority": turn.get("priority"),
                        "status": "NOT_RUN_SETUP",
                        "reason": "setup or required context turn failed",
                        "kind": "assertion",
                        "acceptance_status": "accepted",
                    }
                )
                continue

            status, payload, extracted, latency_ms = _advisor_call(
                target=target,
                turn=turn,
                session=session,
                client=client,
                timeout_seconds=timeout_seconds,
            )
            verdict = evaluate_hlr_assertion(
                case=turn,
                http_status=status,
                payload=payload,
                extracted=extracted,
                latency_ms=latency_ms,
                flow_context=flow_context,
            )
            verdict["kind"] = "assertion"
            verdict["acceptance_status"] = "accepted"
            results.append(verdict)
            ctx = payload.get("context") or {}
            if isinstance(ctx, dict):
                flow_context.update(ctx)

    assertion_rows = [r for r in results if r.get("kind") == "assertion" and r.get("acceptance_status") == "accepted"]
    failures = [r for r in assertion_rows if r.get("status") in {"FAIL", "TIMEOUT"}]
    status = "PASS" if assertion_rows and not failures else ("FAIL" if failures else "PASS")
    return {
        "run_id": run_id,
        "status": status,
        "results": results,
        "pending": pending,
        "selected_accepted": len(assertion_rows),
        "run_phase": "p0" if priority else "full_accepted",
    }
