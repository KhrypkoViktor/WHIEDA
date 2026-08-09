"""Run parity corpus against local Core."""

from __future__ import annotations

import importlib.util
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Reuse acceptance target + transport from sibling package
ACCEPTANCE_ROOT = Path(__file__).resolve().parents[2] / "acceptance"
if str(ACCEPTANCE_ROOT) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE_ROOT))

from lab.corpus import filter_cases, load_corpus  # noqa: E402
from lab.target import build_advisor_request, extract_response_fields, load_target  # noqa: E402
from lab.transport import Transport, UrllibTransport  # noqa: E402

import importlib.util
from pathlib import Path

ACCEPTANCE_ROOT = Path(__file__).resolve().parents[2] / "acceptance"
if str(ACCEPTANCE_ROOT) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE_ROOT))

_LAB = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("parity_assertions", _LAB / "assertions.py")
assert _spec and _spec.loader
_assertions = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_assertions)
evaluate_parity_case = _assertions.evaluate_parity_case


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
    timeout_seconds: float = 30.0,
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
    for case in selected:
        host = case.get("host") or (target.get("advisor", {}).get("headers") or {}).get("Host", "wwc.best")
        record: dict[str, Any] = {
            "case_id": case.get("case_id"),
            "priority": case.get("priority"),
            "capability_id": case.get("capability_id"),
            "input": case.get("input"),
            "status": "NOT_RUN",
            "reason": "",
            "latency_ms": None,
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

        method, url, headers, body = build_advisor_request(target, case)
        headers = dict(headers)
        headers["Host"] = str(host)
        session_field = target["advisor"]["request"]["session"]["field"]
        if case.get("session"):
            body[str(session_field)] = str(case["session"])

        try:
            status, text, latency_ms = client.request(
                method,
                url,
                headers=headers,
                body=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                timeout_seconds=timeout_seconds,
            )
            record["http_status"] = status
            record["latency_ms"] = round(latency_ms, 2)

            if raw_dir:
                raw_path = raw_dir / f"{run_id}_{case.get('case_id')}.json"
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

    summary = _summarize(results)
    return {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": str(target_path),
        "corpus": str(corpus_path),
        "dry_run": dry_run,
        "results": results,
        "summary": summary,
        "live_status": "NOT_RUN" if dry_run else ("PASS" if summary["fail"] == 0 and summary["p0_fail"] == 0 else "FAIL"),
    }


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"pass": 0, "fail": 0, "skip": 0, "unasserted": 0}
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
        bucket = by_priority.setdefault(pr, {"pass": 0, "fail": 0, "skip": 0, "unasserted": 0})
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
        "total": len(results),
        **counts,
        "p0_fail": p0_fail,
        "by_priority": by_priority,
        "by_capability": by_capability,
        "latency_ms": {
            "p50": pct(0.5),
            "p95": pct(0.95),
            "max": max(latencies_sorted) if latencies_sorted else None,
        },
    }
