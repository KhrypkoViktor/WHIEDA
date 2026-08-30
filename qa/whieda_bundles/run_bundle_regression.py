#!/usr/bin/env python3
"""Offline lint for the WHIEDA bundle regression corpus. No network, no bot."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from corpus_lib import (
    CORPUS_PATH,
    REPORT_PATH,
    TSV_PATH,
    active_bundles,
    load_jsonl,
    normalize_text,
    ordered_skus,
    read_tsv,
    split_aliases,
)


def validate(tsv_path: Path, corpus_path: Path) -> dict:
    rows = read_tsv(tsv_path)
    live = active_bundles(rows)
    live_by_id = {str(row.get("bundle_id") or "").strip(): row for row in live}
    cases = load_jsonl(corpus_path)
    errors: list[str] = []

    case_ids = [str(case.get("case_id") or "") for case in cases]
    if len(case_ids) != len(set(case_ids)):
        dupes = [key for key, count in Counter(case_ids).items() if count > 1]
        errors.append(f"duplicate case_id: {dupes}")

    positive = [case for case in cases if case.get("expected_mode") == "structured_solution_bundle"]
    negative = [case for case in cases if case.get("must_not_match_bundle") is True]
    if any("expected_mode" in case for case in negative):
        errors.append("negative case must not set expected_mode")
    if len(negative) != 10:
        errors.append(f"expected 10 negative cases, got {len(negative)}")

    covered = {str(case.get("bundle_id") or "") for case in positive}
    missing = sorted(bundle_id for bundle_id in live_by_id if bundle_id not in covered)
    if missing:
        errors.append(f"corpus missing active bundle_id: {missing}")

    per_bundle = Counter(str(case.get("bundle_id") or "") for case in positive)
    for bundle_id, row in live_by_id.items():
        if per_bundle[bundle_id] < 3:
            errors.append(f"{bundle_id}: need >=3 positive cases, got {per_bundle[bundle_id]}")
        expected = ordered_skus(row.get("sku_groups") or "")
        aliases = {normalize_text(alias) for alias in split_aliases(row.get("aliases") or "")}
        name = str(row.get("bundle_name") or "").strip()
        for case in positive:
            if case.get("bundle_id") != bundle_id:
                continue
            if case.get("expected_skus") != expected:
                errors.append(f"{case.get('case_id')}: expected_skus != sku_groups")
            if case.get("expected_bundle_name") != name:
                errors.append(f"{case.get('case_id')}: expected_bundle_name mismatch")
            if normalize_text(str(case.get("user_text") or "")) not in aliases:
                errors.append(f"{case.get('case_id')}: user_text is not an alias")
            if case.get("priority") != "P0":
                errors.append(f"{case.get('case_id')}: priority must be P0")

    alias_keys = {
        normalize_text(alias)
        for row in live
        for alias in split_aliases(row.get("aliases") or "")
    }
    for case in negative:
        text = normalize_text(str(case.get("user_text") or ""))
        if text in alias_keys:
            errors.append(f"{case.get('case_id')}: negative repeats an alias")
        if case.get("priority") != "P0":
            errors.append(f"{case.get('case_id')}: priority must be P0")

    p0 = sum(1 for case in cases if case.get("priority") == "P0")
    return {
        "ok": not errors,
        "errors": errors,
        "bundles": len(live),
        "positive": len(positive),
        "negative": len(negative),
        "P0": p0,
        "broken": missing,
    }


def write_report(result: dict) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# WHIEDA Bundle Regression Corpus — Local Report",
        "",
        "Mode: offline data lint only. No Core, Telegram, Postgres, n8n, Sheets or production changes.",
        "",
        "## Counts",
        "",
        f"- Active bundles: **{result['bundles']}**",
        f"- Positive cases: **{result['positive']}**",
        f"- Negative cases: **{result['negative']}**",
        f"- P0 cases: **{result['P0']}**",
        "",
        "## Missing / broken rows",
        "",
    ]
    broken = result.get("broken") or []
    errors = result.get("errors") or []
    if not broken and not errors:
        lines.append("None.")
    else:
        for item in broken:
            lines.append(f"- missing bundle_id: `{item}`")
        for item in errors:
            lines.append(f"- {item}")
    lines.extend(["", f"Validation: **{'PASS' if result['ok'] else 'FAIL'}**", ""])
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline WHIEDA bundle regression lint")
    parser.add_argument("--offline", action="store_true", required=True)
    parser.add_argument("--tsv", type=Path, default=TSV_PATH)
    parser.add_argument("--corpus", type=Path, default=CORPUS_PATH)
    args = parser.parse_args()
    result = validate(args.tsv, args.corpus)
    write_report(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
