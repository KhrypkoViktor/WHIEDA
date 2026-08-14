"""HLR HTTP run report writers."""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _latency_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = sorted(float(r["latency_ms"]) for r in rows if r.get("latency_ms") is not None)
    if not values:
        return {"p50": None, "p95": None, "max": None}
    p50 = statistics.median(values)
    p95_idx = max(0, int(len(values) * 0.95) - 1)
    return {"p50": round(p50, 2), "p95": round(values[p95_idx], 2), "max": round(values[-1], 2)}


def _latency_by_priority(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if row.get("latency_ms") is None:
            continue
        grouped[str(row.get("priority") or "P1")].append(float(row["latency_ms"]))
    out: dict[str, Any] = {}
    for prio, values in sorted(grouped.items()):
        values.sort()
        p50 = statistics.median(values)
        p95_idx = max(0, int(len(values) * 0.95) - 1)
        out[prio] = {
            "p50": round(p50, 2),
            "p95": round(values[p95_idx], 2),
            "max": round(values[-1], 2),
        }
    return out


def summarize_results(results: list[dict[str, Any]], pending: dict[str, Any]) -> dict[str, Any]:
    counters = Counter()
    by_rail: dict[str, Counter] = defaultdict(Counter)
    by_mode: dict[str, Counter] = defaultdict(Counter)
    by_gap: dict[str, Counter] = defaultdict(Counter)
    by_source: dict[str, Counter] = defaultdict(Counter)

    for row in results:
        status = str(row.get("status") or "")
        counters[status] += 1
        if row.get("kind") != "assertion" or row.get("acceptance_status") != "accepted":
            continue
        rail = str(row.get("expected_rail") or "unknown")
        by_rail[rail][status] += 1
        mode = str(row.get("expected_mode") or "none")
        by_mode[mode][status] += 1
        gap = str(row.get("gap_kind") or "none")
        by_gap[gap][status] += 1
        src = str((row.get("source") or {}).get("kind") or "unknown")
        by_source[src][status] += 1

    accepted_executed = [
        r
        for r in results
        if r.get("kind") == "assertion"
        and r.get("acceptance_status") == "accepted"
        and r.get("status") not in {"NOT_RUN_SETUP", "NOT_RUN_TIMEOUT", "NOT_RUN_PENDING"}
    ]
    failures = [
        r
        for r in accepted_executed
        if r.get("status") in {"FAIL", "TIMEOUT"}
    ]
    return {
        "counters": dict(counters),
        "selected": len(
            [
                r
                for r in results
                if r.get("kind") == "assertion" and r.get("acceptance_status") == "accepted"
            ]
        ),
        "executed": len(accepted_executed),
        "pass": sum(1 for r in accepted_executed if r.get("status") == "PASS"),
        "fail": len(failures),
        "not_run_setup": counters.get("NOT_RUN_SETUP", 0),
        "not_run_pending": counters.get("NOT_RUN_PENDING", 0),
        "timeout": counters.get("TIMEOUT", 0),
        "by_rail": {k: dict(v) for k, v in by_rail.items()},
        "by_expected_mode": {k: dict(v) for k, v in by_mode.items()},
        "by_gap_kind": {k: dict(v) for k, v in by_gap.items()},
        "by_source_kind": {k: dict(v) for k, v in by_source.items()},
        "latency_ms": _latency_stats(accepted_executed),
        "latency_by_priority": _latency_by_priority(accepted_executed),
        "failures": [
            {
                "case_id": r.get("case_id"),
                "expected_rail": r.get("expected_rail"),
                "expected_mode": r.get("expected_mode"),
                "answer_mode": r.get("answer_mode"),
                "gap_kind": r.get("gap_kind"),
                "status": r.get("status"),
                "reason": r.get("reason"),
                "answer_preview": r.get("answer_preview"),
                "source_kind": (r.get("source") or {}).get("kind"),
            }
            for r in failures
        ],
    }


def write_reports(payload: dict[str, Any], reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(payload.get("run_id") or "unknown")
    json_path = reports_dir / f"HLR_HTTP_REPORT_{run_id}.json"
    md_path = reports_dir / f"HLR_HTTP_REPORT_{run_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    latest = reports_dir / "latest.json"
    latest.write_text(
        json.dumps(
            {"run_id": run_id, "path": json_path.name, "status": payload.get("status")},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return json_path, md_path


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    pending = payload.get("pending") or {}
    target = payload.get("target") or {}
    lines = [
        "# Human Language Rails HTTP Acceptance Report",
        "",
        f"- **Run ID:** `{payload.get('run_id')}`",
        f"- **Status:** `{payload.get('status')}`",
        f"- **Phase:** `{payload.get('run_phase')}`",
        f"- **Target:** `{target.get('base_url')}`",
        "",
        "## Totals",
        "",
        f"- Selected accepted: {summary.get('selected')}",
        f"- Executed: {summary.get('executed')}",
        f"- PASS: {summary.get('pass')}",
        f"- FAIL: {summary.get('fail')}",
        f"- NOT_RUN_SETUP: {summary.get('not_run_setup')}",
        f"- NOT_RUN_PENDING: {summary.get('not_run_pending')}",
        f"- TIMEOUT: {summary.get('timeout')}",
        "",
        "## Pending (not in pass/fail totals)",
        "",
        f"- Count: {pending.get('count')}",
        f"- By status: `{pending.get('by_status')}`",
        "",
        "## Latency (accepted executed)",
        "",
        f"- Overall: `{summary.get('latency_ms')}`",
        f"- By priority: `{summary.get('latency_by_priority')}`",
        "",
        "## Failures by rail",
        "",
    ]
    by_rail = summary.get("by_rail") or {}
    for rail, counts in sorted(by_rail.items()):
        lines.append(f"- `{rail}`: {counts}")
    lines.extend(["", "## Live failures", ""])
    failures = summary.get("failures") or []
    if not failures:
        lines.append("- none")
    else:
        for row in failures:
            lines.append(
                f"- `{row.get('case_id')}` rail=`{row.get('expected_rail')}` "
                f"mode `{row.get('expected_mode')}` → `{row.get('answer_mode')}`: {row.get('reason')}"
            )
    lines.append("")
    return "\n".join(lines)
