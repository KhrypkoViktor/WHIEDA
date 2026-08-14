#!/usr/bin/env python3
"""Offline lint + summary for product discovery map."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PKG = Path(__file__).resolve().parent
STATIC_REPORT = ROOT / "backend" / "platform-api" / "docs" / "PRODUCT_DISCOVERY_MAP_LOCAL_REPORT.md"
TSV = PKG / "PRODUCT_DISCOVERY_MAP_CANDIDATES_V1.tsv"
POLICY = PKG / "PRODUCT_DISCOVERY_RESOLUTION_POLICY_V1.md"
TRIAGE = PKG / "HLR_DISCOVERY_TRIAGE_V1.md"

sys.path.insert(0, str(PKG))

from lint import lint_policy_skus, lint_rows  # noqa: E402
from hlr_triage import find_latest_hlr_report, write_triage  # noqa: E402
from snapshot_loader import load_bundle  # noqa: E402
from tsv_io import read_rows  # noqa: E402


def write_static_report(
    path: Path,
    *,
    snapshot_id: str,
    layer_hashes: dict[str, str],
    rows: list[dict[str, str]],
    lint_ok: bool,
    hlr_report: str | None = None,
) -> None:
    phrases = sorted({str(r.get("phrase") or "") for r in rows})
    groups = sorted({str(r.get("discovery_group") or "") for r in rows})
    status_counts = Counter(str(r.get("status") or "") for r in rows)
    confidence_counts = Counter(str(r.get("confidence") or "") for r in rows)

    lines = [
        "# Product Discovery Map — Local Report",
        "",
        "Mode: read-only QA artifact for Core owner review. **Not wired into runtime.**",
        "",
        f"- Source snapshot: `{snapshot_id}`",
        f"- products sha256: `{layer_hashes.get('products', '')}`",
        f"- aliases sha256: `{layer_hashes.get('aliases', '')}`",
        f"- product_cards sha256: `{layer_hashes.get('product_cards', '')}`",
        f"- resources sha256: `{layer_hashes.get('resources', '')}`",
        f"- Discovery phrases: {len(phrases)}",
        f"- Candidate rows: {len(rows)}",
        f"- Discovery groups: {len(groups)}",
        f"- Lint: {'PASS' if lint_ok else 'FAIL'}",
        "",
    ]
    if hlr_report:
        lines.append(f"- HLR live report: `{hlr_report}`")
        lines.append("")
    lines.extend([
        "## Status breakdown",
        "",
    ])
    for status, count in sorted(status_counts.items()):
        lines.append(f"- `{status}`: {count}")
    lines.extend(["", "## Confidence breakdown", ""])
    for conf, count in sorted(confidence_counts.items()):
        lines.append(f"- `{conf}`: {count}")
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            "- `qa/product_discovery/PRODUCT_DISCOVERY_MAP_CANDIDATES_V1.tsv`",
            "- `qa/product_discovery/PRODUCT_DISCOVERY_RESOLUTION_POLICY_V1.md`",
            "- `qa/product_discovery/HLR_DISCOVERY_TRIAGE_V1.md`",
            "",
            "## Reproduce",
            "",
            "```bash",
            "python qa/product_discovery/build_product_discovery_map.py",
            "python qa/product_discovery/run_product_discovery_map.py --offline",
            "python -m pytest backend/platform-api/tests/test_product_discovery_map.py -q",
            "```",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    argp = argparse.ArgumentParser(description="Offline product discovery map check")
    argp.add_argument("--offline", action="store_true", help="Validate TSV against latest snapshot")
    argp.add_argument("--snapshot", type=Path, default=None)
    argp.add_argument("--tsv", type=Path, default=TSV)
    args = argp.parse_args()

    if not args.tsv.is_file():
        print(f"Missing TSV: {args.tsv}", file=sys.stderr)
        return 1

    bundle = load_bundle(args.snapshot)
    rows = read_rows(args.tsv)
    issues = lint_rows(rows, bundle)
    issues.extend(lint_policy_skus(POLICY, rows))
    lint_ok = not issues

    if issues:
        for issue in issues:
            print(f"LINT {issue.code}: {issue.message}", file=sys.stderr)

    hlr_path = find_latest_hlr_report()
    hlr_name = hlr_path.name if hlr_path else None
    write_triage(TRIAGE, hlr_path)

    for required in (POLICY, TRIAGE):
        if not required.is_file():
            print(f"Missing deliverable: {required}", file=sys.stderr)
            lint_ok = False

    write_static_report(
        STATIC_REPORT,
        snapshot_id=bundle.snapshot_id,
        layer_hashes={
            "products": bundle.layer_hashes.get("products", ""),
            "aliases": bundle.layer_hashes.get("aliases", ""),
            "product_cards": bundle.layer_hashes.get("product_cards", ""),
            "resources": bundle.layer_hashes.get("resources", ""),
        },
        rows=rows,
        lint_ok=lint_ok,
        hlr_report=hlr_name,
    )
    print(f"Report: {STATIC_REPORT}")
    return 0 if lint_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
