"""Golden HTTP run report writers."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any


def _latency_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = sorted(float(r["latency_ms"]) for r in rows if r.get("latency_ms") is not None)
    if not values:
        return {"p50": None, "p95": None, "max": None}
    p50 = statistics.median(values)
    p95_idx = max(0, int(len(values) * 0.95) - 1)
    return {"p50": round(p50, 2), "p95": round(values[p95_idx], 2), "max": round(values[-1], 2)}


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    counters = {
        "executed": 0,
        "pass": 0,
        "fail": 0,
        "skip_surface": 0,
        "not_run_dependency": 0,
        "timeout": 0,
        "negative_pass": 0,
        "negative_fail": 0,
        "setup_turns": 0,
        "assertion_turns": 0,
    }
    by_class: dict[str, dict[str, int]] = {}
    by_priority: dict[str, dict[str, int]] = {}

    for row in results:
        status = str(row.get("status") or "")
        klass = str(row.get("class") or "unknown")
        priority = str(row.get("priority") or "P1")
        by_class.setdefault(klass, {"executed": 0, "pass": 0, "fail": 0})
        by_priority.setdefault(priority, {"executed": 0, "pass": 0, "fail": 0})
        if status == "NOT_RUN_DEPENDENCY":
            counters["not_run_dependency"] += 1
            continue
        if status == "SKIP_SURFACE":
            counters["skip_surface"] += 1
            continue
        if row.get("kind") == "flow_turn":
            if row.get("turn_role") == "setup":
                counters["setup_turns"] += 1
            else:
                counters["assertion_turns"] += 1
        counters["executed"] += 1
        by_class[klass]["executed"] += 1
        by_priority[priority]["executed"] += 1
        if status == "PASS":
            counters["pass"] += 1
            by_class[klass]["pass"] += 1
            by_priority[priority]["pass"] += 1
        elif status == "TIMEOUT":
            counters["timeout"] += 1
            counters["fail"] += 1
            by_class[klass]["fail"] += 1
            by_priority[priority]["fail"] += 1
        elif status == "NEGATIVE_PASS":
            counters["negative_pass"] += 1
        elif status == "NEGATIVE_FAIL":
            counters["negative_fail"] += 1
            counters["fail"] += 1
        else:
            counters["fail"] += 1
            by_class[klass]["fail"] += 1
            by_priority[priority]["fail"] += 1

    executable = [r for r in results if r.get("status") not in {"NOT_RUN_DEPENDENCY", "SKIP_SURFACE"}]
    return {
        "counters": counters,
        "by_class": by_class,
        "by_priority": by_priority,
        "latency_ms": _latency_stats(executable),
        "failures": [
            {
                "id": r.get("case_id") or r.get("fixture_id") or f"{r.get('flow_id')}:{r.get('turn')}",
                "status": r.get("status"),
                "class": r.get("class"),
                "priority": r.get("priority"),
                "expected_mode": r.get("expected_mode"),
                "answer_mode": r.get("answer_mode"),
                "reason": r.get("reason"),
                "answer_preview": r.get("answer_preview"),
            }
            for r in results
            if r.get("status") in {"FAIL", "TIMEOUT", "NEGATIVE_FAIL"}
        ],
    }


def write_reports(payload: dict[str, Any], reports_dir: Path) -> tuple[Path, Path]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    run_id = str(payload.get("run_id") or "unknown")
    json_path = reports_dir / f"GOLDEN_HTTP_REPORT_{run_id}.json"
    md_path = reports_dir / f"GOLDEN_HTTP_REPORT_{run_id}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    latest = reports_dir / "latest.json"
    latest.write_text(json.dumps({"run_id": run_id, "path": str(json_path.name), "status": payload.get("status")}, ensure_ascii=False, indent=2), encoding="utf-8")
    return json_path, md_path


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    counters = summary.get("counters") or {}
    latency = summary.get("latency_ms") or {}
    target = payload.get("target") or {}
    fixture = payload.get("fixture_parity") or {}
    lines = [
        "# Golden HTTP Acceptance Report",
        "",
        f"- **Run ID:** `{payload.get('run_id')}`",
        f"- **Status:** `{payload.get('status')}`",
        f"- **Phase:** `{payload.get('run_phase') or 'unspecified'}`",
        f"- **Target:** `{target.get('base_url')}{target.get('advisor_path')}`",
        "",
    ]
    if fixture:
        lines.extend(
            [
                "## Fixture parity",
                "",
                f"- Source snapshot: `{fixture.get('generated_from')}`",
                f"- Snapshot captured: `{fixture.get('snapshot_captured_at')}`",
                f"- Fixture SQL sha256: `{fixture.get('fixture_sql_sha256')}`",
                f"- Golden SKU coverage products: `{len((fixture.get('golden_sku_coverage') or {}).get('products_present') or [])}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Counts",
            "",
            f"- Executed: {counters.get('executed', 0)}",
            f"- Pass: {counters.get('pass', 0)}",
            f"- Fail: {counters.get('fail', 0)}",
            f"- Skip surface: {counters.get('skip_surface', 0)}",
            f"- Not run (dependency): {counters.get('not_run_dependency', 0)}",
            f"- Timeout: {counters.get('timeout', 0)}",
            f"- Setup turns: {counters.get('setup_turns', 0)}",
            f"- Assertion turns: {counters.get('assertion_turns', 0)}",
            f"- Negative pass/fail: {counters.get('negative_pass', 0)}/{counters.get('negative_fail', 0)}",
            "",
            "## Latency (ms)",
            "",
            f"- p50: {latency.get('p50')}",
            f"- p95: {latency.get('p95')}",
            f"- max: {latency.get('max')}",
            "",
            "## Failures",
            "",
        ]
    )
    failures = summary.get("failures") or []
    if not failures:
        lines.append("_None_")
    else:
        for row in failures[:50]:
            lines.append(
                f"- `{row.get('id')}` [{row.get('status')}] "
                f"mode {row.get('expected_mode')} -> {row.get('answer_mode')}: {row.get('reason')}"
            )
    lines.append("")
    return "\n".join(lines)
