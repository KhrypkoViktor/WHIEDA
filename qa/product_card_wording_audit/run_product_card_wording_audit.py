#!/usr/bin/env python3
"""Read-only product card wording audit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = Path(__file__).resolve().parent
LAB = PKG / "lab"
OUT_DIR = PKG / "reports"
STATIC_REPORT = ROOT / "backend" / "platform-api" / "docs" / "PRODUCT_CARD_WORDING_AUDIT_LOCAL_REPORT.md"
FIXTURE_SNAPSHOT = PKG / "fixtures" / "mini_snapshot"

sys.path.insert(0, str(LAB))
import parser as parser_mod  # noqa: E402
import audit_report as report_mod  # noqa: E402
import rules as rules_mod  # noqa: E402


def write_static_report(path: Path, *, snapshot_id: str, summary: dict[str, object]) -> None:
    lines = [
        "# Product Card Wording Audit — Local Report",
        "",
        "Mode: read-only review markings. No cards, Sheets, runtime or Telegram changes.",
        "",
        f"- Snapshot: `{snapshot_id}`",
        f"- Approved cards: {summary.get('approved_cards')}",
        f"- Candidate cards: {summary.get('candidate_cards')}",
        f"- Findings total: {summary.get('findings_total')}",
        f"- Cards with findings: {summary.get('cards_with_findings')}",
        f"- Cards without findings: {summary.get('cards_without_findings')}",
        "",
        "## Findings by rule",
        "",
    ]
    for rule, count in sorted((summary.get("findings_by_rule") or {}).items()):
        lines.append(f"- `{rule}`: {count}")
    lines.extend(["", "## Findings by severity", ""])
    for sev, count in sorted((summary.get("findings_by_severity") or {}).items()):
        lines.append(f"- `{sev}`: {count}")
    lines.extend(
        [
            "",
            "## Reproduce",
            "",
            "```bash",
            "python qa/product_card_wording_audit/run_product_card_wording_audit.py",
            "python -m pytest backend/platform-api/tests/test_product_card_wording_audit.py -q",
            "```",
            "",
            "Generated CSV/MD/JSON live under `qa/product_card_wording_audit/reports/` (gitignored).",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    argp = argparse.ArgumentParser(description="Product card wording audit")
    argp.add_argument("--fixture", action="store_true", help="Audit fixture snapshot only")
    argp.add_argument("--snapshot", type=Path, default=None)
    args = argp.parse_args()

    if args.fixture:
        snapshot_id, cards = parser_mod.load_all_cards(snapshot_dir=FIXTURE_SNAPSHOT)
    else:
        snapshot_id, cards = parser_mod.load_all_cards(snapshot_dir=args.snapshot)

    findings = rules_mod.run_audit(cards)
    summary = report_mod.summarize(findings, cards)
    summary["snapshot_id"] = snapshot_id

    prefix = f"PRODUCT_CARD_WORDING_AUDIT_{snapshot_id}"
    report_mod.write_csv(OUT_DIR / f"{prefix}_FINDINGS.csv", findings)
    report_mod.write_review_md(OUT_DIR / f"{prefix}_REVIEW.md", findings)
    report_mod.write_summary_json(OUT_DIR / f"{prefix}_SUMMARY.json", summary)
    write_static_report(STATIC_REPORT, snapshot_id=snapshot_id, summary=summary)

    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
