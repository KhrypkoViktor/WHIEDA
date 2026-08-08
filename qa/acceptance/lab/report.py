"""Acceptance run reports (Markdown + JSON)."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.redact import redact_run_payload, redact_text

ARTIFACTS = ("This needs human review", "Traceback", "Nordman")


def build_report_payload(run_payload: dict[str, Any]) -> dict[str, Any]:
    results = run_payload.get("results") or []
    summary = run_payload.get("summary") or {}
    fail_reasons = Counter(r.get("reason") for r in results if r.get("status") == "FAIL")
    artifacts = [
        {"case_id": r.get("case_id"), "answer_text": redact_text(str(r.get("answer_text") or "")[:200])}
        for r in results
        if any(a.lower() in str(r.get("answer_text") or "").lower() for a in ARTIFACTS)
    ]
    p0_fails = [
        {
            "case_id": r.get("case_id"),
            "input": r.get("input"),
            "reason": r.get("reason"),
            "answer_text": redact_text(str(r.get("answer_text") or "")[:240]),
        }
        for r in results
        if r.get("priority") == "P0" and r.get("status") == "FAIL"
    ]
    unasserted = [r.get("case_id") for r in results if r.get("status") == "UNASSERTED"]
    overall = run_payload.get("live_status", "NOT_RUN")
    if overall not in {"PASS", "FAIL", "NOT_RUN"}:
        overall = "FAIL" if summary.get("fail") else "PASS"

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_payload.get("run_id"),
        "status": overall,
        "summary": summary,
        "top_fail_reasons": fail_reasons.most_common(20),
        "p0_failures": p0_fails,
        "artifacts": artifacts,
        "unasserted_case_ids": unasserted,
        "target": run_payload.get("target"),
        "corpus": run_payload.get("corpus"),
        "dry_run": run_payload.get("dry_run", False),
    }


def write_reports(run_payload: dict[str, Any], md_path: Path, json_path: Path) -> dict[str, Any]:
    report = build_report_payload(run_payload)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    safe_run = redact_run_payload(run_payload)
    json_path.write_text(
        json.dumps({"report": report, "run": safe_run}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(_render_md(report, safe_run), encoding="utf-8")
    return report


def _render_md(report: dict[str, Any], run_payload: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lat = summary.get("latency_ms") or {}
    lines = [
        "# WHIEDA Acceptance Lab Report",
        "",
        f"**Generated:** {report.get('generated_at')}",
        f"**Run id:** `{report.get('run_id')}`",
        f"**Status:** **{report.get('status')}**",
        f"**Dry run:** {report.get('dry_run')}",
        "",
        "## Summary",
        "",
        f"- Total: {summary.get('total', 0)}",
        f"- Pass: {summary.get('pass', 0)}",
        f"- Fail: {summary.get('fail', 0)}",
        f"- Skip: {summary.get('skip', 0)}",
        f"- Unasserted: {summary.get('unasserted', 0)}",
        "",
        "## By priority",
        "",
    ]
    for pr, bucket in sorted((summary.get("by_priority") or {}).items()):
        lines.append(f"- **{pr}**: pass {bucket.get('pass', 0)}, fail {bucket.get('fail', 0)}, unasserted {bucket.get('unasserted', 0)}")

    lines.extend(
        [
            "",
            "## Latency (ms)",
            "",
            f"- p50: {lat.get('p50')}",
            f"- p95: {lat.get('p95')}",
            f"- max: {lat.get('max')}",
            "",
            "## Top failure reasons",
            "",
        ]
    )
    reasons = report.get("top_fail_reasons") or []
    if reasons:
        for reason, count in reasons:
            lines.append(f"- ({count}) {reason}")
    else:
        lines.append("- None")

    lines.extend(["", "## P0 failures", ""])
    p0 = report.get("p0_failures") or []
    if p0:
        for item in p0:
            lines.append(f"- `{item['case_id']}` input={item['input']!r}")
            lines.append(f"  - reason: {item['reason']}")
            lines.append(f"  - answer: {item['answer_text']!r}")
    else:
        lines.append("- None")

    lines.extend(["", "## Technical artifacts", ""])
    if report.get("artifacts"):
        for item in report["artifacts"]:
            lines.append(f"- `{item['case_id']}`: {item['answer_text']!r}")
    else:
        lines.append("- None")

    lines.extend(["", "## Unasserted cases", ""])
    unasserted = report.get("unasserted_case_ids") or []
    if unasserted:
        lines.append(", ".join(f"`{x}`" for x in unasserted))
    else:
        lines.append("- None")

    return "\n".join(lines) + "\n"
