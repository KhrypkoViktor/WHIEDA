"""Parity run report writer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_reports(payload: dict[str, Any], md_path: Path, json_path: Path) -> None:
    md_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(payload), encoding="utf-8")


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# Core Local Parity Run",
        "",
        f"- Run id: `{payload.get('run_id')}`",
        f"- Status: **{payload.get('live_status')}**",
        f"- Target: `{payload.get('target')}`",
        f"- Corpus: `{payload.get('corpus')}`",
        "",
        "## Summary",
        "",
        f"- Total: {summary.get('total', 0)}",
        f"- Pass: {summary.get('pass', 0)} | Fail: {summary.get('fail', 0)} | "
        f"Unasserted: {summary.get('unasserted', 0)} | Skip: {summary.get('skip', 0)}",
        f"- P0 fail: {summary.get('p0_fail', 0)}",
        "",
        "## Latency (ms)",
        "",
    ]
    lat = summary.get("latency_ms") or {}
    lines.append(f"- p50: {lat.get('p50')} | p95: {lat.get('p95')} | max: {lat.get('max')}")
    lines.extend(["", "## By capability", ""])
    for cap, bucket in sorted((summary.get("by_capability") or {}).items()):
        lines.append(f"- {cap}: pass {bucket.get('pass', 0)} fail {bucket.get('fail', 0)}")
    lines.extend(["", "## Failures", ""])
    for row in payload.get("results") or []:
        if row.get("status") == "FAIL":
            lines.append(f"- `{row.get('case_id')}` ({row.get('capability_id')}): {row.get('reason')}")
    return "\n".join(lines) + "\n"
