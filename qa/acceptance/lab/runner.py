"""Run acceptance cases against Core API."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.assertions import evaluate_result
from lab.corpus import filter_cases, load_corpus
from lab.target import build_advisor_request, extract_response_fields, load_target
from lab.transport import Transport, UrllibTransport


def run_cases(
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
        method, url, headers, body = build_advisor_request(target, case)
        record: dict[str, Any] = {
            "case_id": case.get("case_id"),
            "priority": case.get("priority"),
            "input": case.get("input"),
            "context_before": case.get("context_before") or [],
            "http_status": None,
            "latency_ms": None,
            "answer_text": None,
            "answer_mode": None,
            "product_name": None,
            "has_photo": False,
            "has_video": False,
            "has_pdf": False,
            "status": "NOT_RUN",
            "reason": "",
            "request": {"method": method, "url": url, "headers": headers, "body": body},
        }

        if dry_run:
            record["status"] = "SKIP"
            record["reason"] = "dry-run"
            results.append(record)
            continue

        try:
            status, text, latency_ms = client.request(
                method, url, headers=headers, body=body, timeout_seconds=timeout_seconds
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
                        record["answer_text"] = extracted.get("answer_text")
                        record["answer_mode"] = extracted.get("answer_mode")
                        record["product_name"] = extracted.get("product_name")
                        record["has_photo"] = bool(extracted.get("photo"))
                        record["has_video"] = bool(extracted.get("videos"))
                        record["has_pdf"] = bool(extracted.get("pdf_documents"))
                    else:
                        parse_error = "response JSON is not an object"
                except json.JSONDecodeError:
                    parse_error = "invalid JSON response"

            st, reason = evaluate_result(
                case=case,
                http_status=status,
                latency_ms=latency_ms,
                extracted=extracted,
                error=parse_error,
            )
            record["status"] = st
            record["reason"] = reason
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
        "live_status": "NOT_RUN" if dry_run else ("PASS" if summary["fail"] == 0 else "FAIL"),
    }


def _summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"pass": 0, "fail": 0, "skip": 0, "unasserted": 0, "not_run": 0}
    by_priority: dict[str, dict[str, int]] = {}
    latencies = [float(r["latency_ms"]) for r in results if r.get("latency_ms") is not None]

    for row in results:
        st = str(row.get("status", "")).lower()
        if st == "pass":
            counts["pass"] += 1
        elif st == "fail":
            counts["fail"] += 1
        elif st == "skip":
            counts["skip"] += 1
        elif st == "unasserted":
            counts["unasserted"] += 1
        else:
            counts["not_run"] += 1
        pr = str(row.get("priority") or "P2")
        bucket = by_priority.setdefault(pr, {"pass": 0, "fail": 0, "skip": 0, "unasserted": 0})
        key = st if st in bucket else "fail"
        if key in bucket:
            bucket[key] += 1

    latencies_sorted = sorted(latencies)
    def pct(p: float) -> float | None:
        if not latencies_sorted:
            return None
        idx = int(round((len(latencies_sorted) - 1) * p))
        return latencies_sorted[idx]

    return {
        "total": len(results),
        **counts,
        "by_priority": by_priority,
        "latency_ms": {
            "p50": pct(0.5),
            "p95": pct(0.95),
            "max": max(latencies_sorted) if latencies_sorted else None,
        },
    }
