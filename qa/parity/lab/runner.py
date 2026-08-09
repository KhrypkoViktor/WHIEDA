"""Run parity corpus against local Core."""

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

from lab.corpus import filter_cases, load_corpus  # noqa: E402
from lab.target import build_advisor_request, extract_response_fields, load_target  # noqa: E402
from lab.transport import Transport, UrllibTransport  # noqa: E402

_LAB = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("parity_assertions", _LAB / "assertions.py")
assert _spec and _spec.loader
_assertions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_assertions)
evaluate_parity_case = _assertions.evaluate_parity_case


def _run_context_setup(
    *,
    client: Transport,
    target: dict[str, Any],
    case: dict[str, Any],
    host: str,
    timeout_seconds: float,
    session_field: str,
    raw_dir: Path | None,
    run_id: str,
    case_id: str,
) -> tuple[bool, list[dict[str, Any]], float, str | None]:
    """Send prior user turns on the same session before the main case."""
    ctx_before = case.get("context_before") or []
    if not ctx_before:
        return True, [], 0.0, None

    setup_steps: list[dict[str, Any]] = []
    setup_latency = 0.0
    session = str(case.get("session") or "")

    for index, turn in enumerate(ctx_before):
        if str(turn.get("role") or "").lower() != "user":
            continue
        setup_case = dict(case)
        setup_case["input"] = str(turn.get("text") or "")
        setup_case.pop("context_before", None)

        method, url, headers, body = build_advisor_request(target, setup_case)
        headers = dict(headers)
        headers["Host"] = str(host)
        body.pop("context_before", None)
        if session:
            body[str(session_field)] = session

        try:
            status, text, latency_ms = client.request(
                method,
                url,
                headers=headers,
                body=body,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            return False, setup_steps, setup_latency, f"context setup step {index}: {exc}"

        setup_latency += latency_ms
        step_record = {
            "step": index,
            "input": setup_case["input"],
            "http_status": status,
            "latency_ms": round(latency_ms, 2),
        }
        setup_steps.append(step_record)

        if raw_dir:
            raw_path = raw_dir / f"{run_id}_{case_id}_setup_{index}.json"
            raw_path.write_text(
                json.dumps({"status": status, "body": text, "latency_ms": latency_ms}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            step_record["raw_response_path"] = str(raw_path)

        if status >= 400:
            return False, setup_steps, setup_latency, f"context setup step {index}: HTTP {status}"

    return True, setup_steps, setup_latency, None


def run_parity_cases(
    *,
    target_path: Path,
    corpus_path: Path,
    transport: Transport | None = None,
    raw_dir: Path | None = None,
    priority: str | None = None,
    case_id: str | None = None,
    limit: int | None = None,
    fail_fast: bool = False,
    timeout_seconds: float = 5.0,
    run_timeout_seconds: float = 300.0,
    dry_run: bool = False,
) -> dict[str, Any]:
    target = load_target(target_path)
    cases = load_corpus(corpus_path)
    selected = filter_cases(cases, priority=priority, case_id=case_id, limit=limit)
    client = transport or UrllibTransport()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    if raw_dir:
        raw_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    run_started = time.monotonic()
    timed_out = False
    not_run_ids: list[str] = []
    session_field = target["advisor"]["request"]["session"]["field"]

    for index, case in enumerate(selected):
        if time.monotonic() - run_started > run_timeout_seconds:
            timed_out = True
            not_run_ids = [str(c.get("case_id") or f"#{i}") for i, c in enumerate(selected[index:], start=index)]
            break

        host = case.get("host") or (target.get("advisor", {}).get("headers") or {}).get("Host", "wwc.best")
        case_id_str = str(case.get("case_id") or f"case-{index}")
        record: dict[str, Any] = {
            "case_id": case.get("case_id"),
            "priority": case.get("priority"),
            "capability_id": case.get("capability_id"),
            "input": case.get("input"),
            "status": "NOT_RUN",
            "reason": "",
            "latency_ms": None,
            "setup_latency_ms": None,
            "http_status": None,
        }

        if dry_run:
            record["status"] = "SKIP"
            record["reason"] = "dry-run"
            results.append(record)
            continue

        if case.get("send_invalid_json"):
            base = str(target["base_url"]).rstrip("/")
            path = target["advisor"]["path"]
            url = f"{base}{path}"
            headers = {"Host": str(host), "Content-Type": "application/json"}
            try:
                status, text, latency_ms = client.request(
                    "POST", url, headers=headers, body=b"not-json", timeout_seconds=timeout_seconds
                )
                record["http_status"] = status
                record["latency_ms"] = round(latency_ms, 2)
                st, reason = evaluate_parity_case(
                    case=case,
                    http_status=status,
                    latency_ms=latency_ms,
                    extracted=None,
                    raw_payload={"body": text},
                )
                record["status"] = st
                record["reason"] = reason
            except Exception as exc:
                record["status"] = "FAIL"
                record["reason"] = str(exc)
            results.append(record)
            if fail_fast and record["status"] == "FAIL":
                break
            continue

        if case.get("context_before"):
            ok, setup_steps, setup_latency, setup_error = _run_context_setup(
                client=client,
                target=target,
                case=case,
                host=str(host),
                timeout_seconds=timeout_seconds,
                session_field=str(session_field),
                raw_dir=raw_dir,
                run_id=run_id,
                case_id=case_id_str,
            )
            record["setup_latency_ms"] = round(setup_latency, 2)
            record["context_setup"] = setup_steps
            if not ok:
                record["status"] = "NOT_RUN"
                record["reason"] = f"context setup failed: {setup_error}"
                results.append(record)
                if fail_fast:
                    break
                continue

        method, url, headers, body = build_advisor_request(target, case)
        headers = dict(headers)
        headers["Host"] = str(host)
        body.pop("context_before", None)
        if case.get("session"):
            body[str(session_field)] = str(case["session"])

        try:
            status, text, latency_ms = client.request(
                method,
                url,
                headers=headers,
                body=body,
                timeout_seconds=timeout_seconds,
            )
            record["http_status"] = status
            record["latency_ms"] = round(latency_ms, 2)

            if raw_dir:
                raw_path = raw_dir / f"{run_id}_{case_id_str}.json"
                raw_path.write_text(
                    json.dumps({"status": status, "body": text, "latency_ms": latency_ms}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                record["raw_response_path"] = str(raw_path)

            payload: dict[str, Any] | None = None
            extracted: dict[str, Any] | None = None
            parse_error = None
            if status < 400 and text.strip():
                try:
                    payload = json.loads(text)
                    if isinstance(payload, dict):
                        extracted = extract_response_fields(target, payload)
                    else:
                        parse_error = "response JSON is not an object"
                except json.JSONDecodeError:
                    parse_error = "invalid JSON response"

            st, reason = evaluate_parity_case(
                case=case,
                http_status=status,
                latency_ms=latency_ms,
                extracted=extracted,
                raw_payload=payload,
                error=parse_error,
            )
            record["status"] = st
            record["reason"] = reason
            if extracted:
                record["answer_mode"] = extracted.get("answer_mode")
                record["product_name"] = extracted.get("product_name")
        except TimeoutError as exc:
            record["status"] = "FAIL"
            record["reason"] = str(exc)
        except Exception as exc:
            record["status"] = "FAIL"
            record["reason"] = f"transport error: {exc}"

        results.append(record)
        if fail_fast and record["status"] == "FAIL":
            break

    summary = _summarize(results, total_cases=len(selected), not_run=len(not_run_ids), timed_out=timed_out)
    return {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": str(target_path),
        "corpus": str(corpus_path),
        "dry_run": dry_run,
        "timed_out": timed_out,
        "not_run_case_ids": not_run_ids,
        "results": results,
        "summary": summary,
        "live_status": _live_status(dry_run, summary, timed_out, not_run_ids),
    }


def _live_status(dry_run: bool, summary: dict[str, Any], timed_out: bool, not_run_ids: list[str]) -> str:
    if dry_run:
        return "NOT_RUN"
    if timed_out or not_run_ids:
        return "FAIL"
    if summary["fail"] == 0 and summary.get("p0_fail", 0) == 0 and summary.get("not_run", 0) == 0:
        return "PASS"
    return "FAIL"


def _summarize(
    results: list[dict[str, Any]],
    *,
    total_cases: int | None = None,
    not_run: int = 0,
    timed_out: bool = False,
) -> dict[str, Any]:
    counts = {"pass": 0, "fail": 0, "skip": 0, "unasserted": 0, "not_run": 0}
    by_priority: dict[str, dict[str, int]] = {}
    by_capability: dict[str, dict[str, int]] = {}
    latencies = [float(r["latency_ms"]) for r in results if r.get("latency_ms") is not None]
    p0_fail = 0

    for row in results:
        st = str(row.get("status", "")).lower()
        key = st if st in counts else "fail"
        if key in counts:
            counts[key] += 1
        pr = str(row.get("priority") or "P2")
        bucket = by_priority.setdefault(pr, {"pass": 0, "fail": 0, "skip": 0, "unasserted": 0, "not_run": 0})
        bk = st if st in bucket else "fail"
        bucket[bk] = bucket.get(bk, 0) + 1
        if pr == "P0" and st == "fail":
            p0_fail += 1
        cap = str(row.get("capability_id") or "unknown")
        cap_bucket = by_capability.setdefault(cap, {"pass": 0, "fail": 0})
        if st == "pass":
            cap_bucket["pass"] += 1
        elif st == "fail":
            cap_bucket["fail"] += 1

    latencies_sorted = sorted(latencies)

    def pct(p: float) -> float | None:
        if not latencies_sorted:
            return None
        idx = int(round((len(latencies_sorted) - 1) * p))
        return latencies_sorted[idx]

    return {
        "total": total_cases if total_cases is not None else len(results),
        **counts,
        "p0_fail": p0_fail,
        "not_run": not_run + counts.get("not_run", 0),
        "timeout": timed_out,
        "by_priority": by_priority,
        "by_capability": by_capability,
        "latency_ms": {
            "p50": pct(0.5),
            "p95": pct(0.95),
            "max": max(latencies_sorted) if latencies_sorted else None,
        },
    }
