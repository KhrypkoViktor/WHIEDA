"""HTTP runner for no-blind-zone corpus (localhost Core only)."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ACCEPTANCE_ROOT = Path(__file__).resolve().parents[2] / "acceptance"
if str(ACCEPTANCE_ROOT) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE_ROOT))

_LAB = Path(__file__).resolve().parent
_corpus_spec = importlib.util.spec_from_file_location("nbz_corpus_local", _LAB / "corpus.py")
assert _corpus_spec and _corpus_spec.loader
_corpus = importlib.util.module_from_spec(_corpus_spec)
_corpus_spec.loader.exec_module(_corpus)
load_corpus = _corpus.load_corpus
filter_cases = _corpus.filter_cases
from lab.target import build_advisor_request, extract_response_fields, load_target  # noqa: E402
from lab.transport import UrllibTransport  # noqa: E402

_LAB = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("nbz_assertions", _LAB / "assertions.py")
assert _spec and _spec.loader
_assertions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_assertions)
evaluate_nbz_case = _assertions.evaluate_nbz_case


def run_nbz_cases(
    *,
    target_path: Path,
    corpus_path: Path,
    priority: str | None = None,
    case_id: str | None = None,
    group: str | None = None,
    limit: int | None = None,
    fail_fast: bool = False,
    timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    target = load_target(target_path)
    cases = load_corpus(corpus_path)
    selected = filter_cases(cases, priority=priority, case_id=case_id, group=group, limit=limit)
    client = UrllibTransport()
    session_field = str((target.get("advisor") or {}).get("request", {}).get("session", {}).get("field") or "session")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    results: list[dict[str, Any]] = []
    passed = 0
    failed = 0

    for case in selected:
        case_id_val = str(case.get("case_id") or "")
        ctx_before = case.get("context_before") or []
        session = str(case.get("session") or "")

        for index, turn in enumerate(ctx_before):
            if str(turn.get("role") or "").lower() != "user":
                continue
            setup = dict(case)
            setup["input"] = str(turn.get("text") or "")
            setup.pop("context_before", None)
            method, url, headers, body = build_advisor_request(target, setup)
            if session:
                body[session_field] = session
            status, _text, _lat = client.request(
                method, url, headers=headers, body=body, timeout_seconds=timeout_seconds
            )
            if status >= 400:
                failed += 1
                results.append(
                    {
                        "case_id": case_id_val,
                        "status": "FAIL",
                        "errors": [f"context setup step {index}: HTTP {status}"],
                    }
                )
                if fail_fast:
                    break
                continue

        method, url, headers, body = build_advisor_request(target, case)
        if session:
            body[session_field] = session
        t0 = time.perf_counter()
        status, text, latency_ms = client.request(
            method, url, headers=headers, body=body, timeout_seconds=timeout_seconds
        )
        latency_ms = latency_ms or (time.perf_counter() - t0) * 1000
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError:
            payload = {}
        response = extract_response_fields(target, payload)
        response["answer_text"] = payload.get("answer_text") or response.get("answer_text")
        response["answer_mode"] = payload.get("answer_mode") or response.get("answer_mode")
        response["gap_kind"] = payload.get("gap_kind")
        response["next_steps"] = payload.get("next_steps")
        verdict = evaluate_nbz_case(case, response, http_status=status)
        verdict["latency_ms"] = round(latency_ms, 2)
        verdict["http_status"] = status
        verdict["run_id"] = run_id
        results.append(verdict)
        if verdict["status"] == "PASS":
            passed += 1
        else:
            failed += 1
            if fail_fast:
                break

    p0 = [r for r in results if str(r.get("case_id", "")).startswith("NBZ-P0")]
    p1 = [r for r in results if str(r.get("case_id", "")).startswith("NBZ-P1")]
    p0_pass = sum(1 for r in p0 if r.get("status") == "PASS")
    p1_pass = sum(1 for r in p1 if r.get("status") == "PASS")

    return {
        "run_id": run_id,
        "status": "PASS" if failed == 0 and selected else ("FAIL" if failed else "NOT_RUN"),
        "total": len(selected),
        "passed": passed,
        "failed": failed,
        "p0_line": f"P0: {p0_pass}/{len(p0)} passed" if p0 else None,
        "p1_line": f"P1: {p1_pass}/{len(p1)} passed" if p1 else None,
        "total_line": f"NBZ corpus: {passed}/{len(selected)} passed",
        "results": results,
    }
