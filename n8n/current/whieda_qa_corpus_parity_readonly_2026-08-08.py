#!/usr/bin/env python3
"""Read-only parity audit: legacy smoke TSV vs QA JSONL corpus (offline, no network)."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TSV = ROOT / "n8n" / "current" / "source_batches" / "smoke_cases_sheet_v1" / "smoke_cases_raw.tsv"
JSONL = ROOT / "qa" / "cases" / "whieda_regression_cases_v1.jsonl"


def load_tsv() -> list[dict[str, str]]:
    with TSV.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def load_jsonl() -> list[dict]:
    rows: list[dict] = []
    with JSONL.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> int:
    if not TSV.is_file():
        print(f"TSV not found: {TSV}", file=sys.stderr)
        return 1
    if not JSONL.is_file():
        print(f"JSONL not found: {JSONL}", file=sys.stderr)
        return 1

    tsv_rows = [r for r in load_tsv() if str(r.get("enabled", "")).upper() == "TRUE"]
    jsonl_rows = load_jsonl()

    tsv_ids = {r["case_id"] for r in tsv_rows}
    jsonl_inputs = {r["input"].casefold() for r in jsonl_rows}

    covered = 0
    for row in tsv_rows:
        text = str(row.get("input_text", "")).casefold()
        if text in jsonl_inputs:
            covered += 1

    print("=== WHIEDA QA corpus parity (read-only) ===")
    print(f"Legacy TSV enabled cases: {len(tsv_rows)}")
    print(f"QA JSONL corpus cases:    {len(jsonl_rows)}")
    print(f"TSV inputs found in JSONL: {covered}/{len(tsv_rows)}")
    print(f"TSV case_ids (reference):  {len(tsv_ids)} unique")
    print("Note: JSONL is superset; full input match is conservative.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
