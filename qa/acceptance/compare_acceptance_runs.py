#!/usr/bin/env python3
"""Compare two acceptance run JSON reports or baselines."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ACCEPTANCE_ROOT = ROOT / "qa" / "acceptance"
sys.path.insert(0, str(ACCEPTANCE_ROOT))

from lab.baseline import build_baseline, compare_baselines, load_baseline  # noqa: E402


def _load_run(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "run" in data:
        return data["run"]
    if "results" in data:
        return data
    if "cases" in data:
        return {"results": list(data["cases"].values()), "run_id": data.get("run_id")}
    raise ValueError("Unrecognized acceptance artifact format")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare acceptance runs")
    parser.add_argument("current", type=Path)
    parser.add_argument("baseline", type=Path)
    args = parser.parse_args()

    current_run = _load_run(args.current.resolve())
    baseline_data = load_baseline(args.baseline.resolve())
    if baseline_data is None:
        baseline_data = build_baseline(_load_run(args.baseline.resolve()))

    current_baseline = build_baseline(current_run)
    diff = compare_baselines(current_baseline, baseline_data)

    print(f"Compare status: {diff['status']}")
    print(f"Fixed: {len(diff.get('fixed') or [])}")
    print(f"Regressions: {len(diff.get('regressions') or [])}")
    print(f"Changed: {len(diff.get('changed') or [])}")
    print(f"Slower: {len(diff.get('slower') or [])}")
    if diff.get("regressions"):
        print("Regression case ids:", ", ".join(diff["regressions"][:20]))
    return 0 if diff["status"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
