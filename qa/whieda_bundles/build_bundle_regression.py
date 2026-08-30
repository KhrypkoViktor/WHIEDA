#!/usr/bin/env python3
"""Build the offline Solution_Bundles regression corpus from a local TSV export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from corpus_lib import (
    CORPUS_PATH,
    TSV_PATH,
    active_bundles,
    build_corpus,
    read_tsv,
    write_jsonl,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build WHIEDA bundle regression corpus")
    parser.add_argument("--tsv", type=Path, default=TSV_PATH)
    parser.add_argument("--output", type=Path, default=CORPUS_PATH)
    args = parser.parse_args()
    rows = read_tsv(args.tsv)
    cases, broken = build_corpus(rows)
    write_jsonl(cases, args.output)
    live = active_bundles(rows)
    positive = sum(1 for case in cases if case.get("expected_mode") == "structured_solution_bundle")
    negative = sum(1 for case in cases if case.get("must_not_match_bundle") is True)
    print(
        json.dumps(
            {
                "ok": not broken,
                "tsv": str(args.tsv),
                "output": str(args.output),
                "active_bundles": len(live),
                "positive": positive,
                "negative": negative,
                "broken": broken,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main())
