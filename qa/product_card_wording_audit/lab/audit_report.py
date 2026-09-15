"""Report writers for product card wording audit."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from rules import Finding


def summarize(findings: list[Finding], cards: list[Any]) -> dict[str, Any]:
    by_layer = Counter(f.source_layer for f in findings)
    by_rule = Counter(f.rule_id for f in findings)
    by_severity = Counter(f.severity for f in findings)
    skus_with_findings = {f.sku for f in findings if f.source_layer != "cross_card"}
    all_skus = {c.sku for c in cards}
    approved_skus = {c.sku for c in cards if c.source_layer == "approved"}
    candidate_skus = {c.sku for c in cards if c.source_layer == "candidate"}
    return {
        "cards_total": len(cards),
        "approved_cards": len(approved_skus),
        "candidate_cards": len(candidate_skus),
        "findings_total": len(findings),
        "findings_by_severity": dict(by_severity),
        "findings_by_rule": dict(by_rule),
        "findings_by_layer": dict(by_layer),
        "cards_with_findings": len(skus_with_findings),
        "cards_without_findings": len(all_skus - skus_with_findings),
        "no_finding_skus": sorted(all_skus - skus_with_findings),
    }


def write_csv(path: Path, findings: list[Finding]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "finding_id",
                "severity",
                "sku",
                "source_layer",
                "section",
                "matched_span",
                "rule_id",
                "reason",
                "label",
            ],
        )
        writer.writeheader()
        for row in findings:
            writer.writerow(row.as_dict())


def write_review_md(path: Path, findings: list[Finding]) -> None:
    grouped: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        grouped[finding.sku].append(finding)
    lines = ["# Product Card Wording Audit — Review", ""]
    for sku in sorted(grouped):
        lines.append(f"## {sku}")
        lines.append("")
        for finding in grouped[sku]:
            lines.append(
                f"- `{finding.finding_id}` **{finding.severity}** "
                f"`{finding.rule_id}` · `{finding.section}` · {finding.reason}"
            )
            if finding.matched_span:
                lines.append(f"  - span: `{finding.matched_span[:160]}`")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_summary_json(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
