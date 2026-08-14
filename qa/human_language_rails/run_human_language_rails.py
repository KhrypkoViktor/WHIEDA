#!/usr/bin/env python3
"""Offline lint and metrics for human language rails corpus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = Path(__file__).resolve().parent
sys.path.insert(0, str(PKG / "lab"))

from corpus import (  # noqa: E402
    CORPUS_PATH,
    FLOWS_PATH,
    RAIL_MINIMUMS,
    UNIVERSAL_MENU_MUST_CONTAIN_ALL,
    corpus_stats,
    load_cases,
    load_flows,
    validate_cases,
)

DEFAULT_CORPUS = PKG / "whieda_human_language_rails_v1.jsonl"
DEFAULT_FLOWS = PKG / "flows_v1.jsonl"
DEFAULT_REPORT = ROOT / "backend" / "platform-api" / "docs" / "HUMAN_LANGUAGE_RAILS_CORPUS_LOCAL_REPORT.md"


def write_report(path: Path, stats: dict[str, object], *, errors: list[str]) -> None:
    by_rail_accepted = stats.get("by_rail_accepted") or {}
    by_rail_all = stats.get("by_rail_all") or {}
    lines = [
        "# Human Language Rails Corpus — Local Report",
        "",
        "Mode: offline QA corpus only. No Core, Telegram, Postgres or Sheets changes.",
        "",
        "## Totals",
        "",
        f"- Assertion turns (all): **{stats['assertions_total']}**",
        f"- Accepted assertions: **{stats['assertions_accepted']}**",
        f"- Pending surface: {stats['assertions_pending_surface']}",
        f"- Pending policy: {stats['assertions_pending_policy']}",
        f"- Setup turns: {stats['setup_turns']}",
        f"- Flows (reconciled): **{stats['flows_total']}** metadata rows={stats.get('flows_metadata_rows', 'n/a')}",
        f"- Multi-turn flows: {stats['multi_turn_flows']}",
        f"- Malformed accepted assertions: {stats['malformed_accepted']}",
        f"- Vague→useful flows: {stats['vague_to_useful_flows']}",
        f"- Context media/basket follow-ups: {stats['context_media_followups']}",
        "",
        "## Accepted by rail (minimums apply here only)",
        "",
    ]
    for rail, minimum in RAIL_MINIMUMS.items():
        count = by_rail_accepted.get(rail, 0)
        lines.append(f"- `{rail}`: {count} accepted (minimum {minimum})")
    lines.extend(["", "## All assertions by rail (includes pending)", ""])
    for rail in RAIL_MINIMUMS:
        lines.append(f"- `{rail}`: {by_rail_all.get(rail, 0)} total")
    lines.extend(
        [
            "",
            "## Universal menu rule (accepted only)",
            "",
            "Accepted `universal_menu` rows require `must_contain_all`:",
        ]
    )
    for marker in UNIVERSAL_MENU_MUST_CONTAIN_ALL:
        lines.append(f"- `{marker}`")
    lines.extend(["", "## Source kinds (accepted)", ""])
    for kind, count in sorted((stats.get("by_source_kind_accepted") or {}).items()):
        lines.append(f"- `{kind}`: {count}")
    lines.extend(
        [
            "",
            "## Validation",
            "",
            "PASS" if not errors else "FAIL",
            "",
        ]
    )
    if errors:
        lines.extend([f"- {err}" for err in errors[:20]])
        if len(errors) > 20:
            lines.append(f"- … and {len(errors) - 20} more")
    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```bash",
            "python qa/human_language_rails/build_corpus.py",
            "python qa/human_language_rails/run_human_language_rails.py --offline",
            "python -m pytest backend/platform-api/tests/test_human_language_rails_corpus.py -q",
            "```",
            "",
            "## Limitations",
            "",
            "- Corpus validates rail expectations offline; it does not execute Core or Telegram.",
            "- `pending_surface` rows track greeting/capabilities/smalltalk gaps; see `fixtures/pending_assertions.jsonl`.",
            "- `pending_policy` rows require owner decision; they are excluded from accepted minimums.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_offline(*, corpus_path: Path, flows_path: Path, report_path: Path) -> int:
    if not corpus_path.is_file():
        print(f"Missing corpus: {corpus_path}", file=sys.stderr)
        return 1
    cases = load_cases(corpus_path)
    flows = load_flows(flows_path)
    errors = validate_cases(cases, flows=flows)
    stats = corpus_stats(cases, flows=flows)
    write_report(report_path, stats, errors=errors)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    if errors:
        print("VALIDATION FAILED:", file=sys.stderr)
        for err in errors[:10]:
            print(f"  - {err}", file=sys.stderr)
        return 1
    print("OK offline validation passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Human language rails corpus offline runner")
    parser.add_argument("--offline", action="store_true", help="Run offline lint/metrics only")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--flows", type=Path, default=DEFAULT_FLOWS)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    if not args.offline:
        parser.error("only --offline is supported")
    return run_offline(corpus_path=args.corpus, flows_path=args.flows, report_path=args.report)


if __name__ == "__main__":
    raise SystemExit(main())
