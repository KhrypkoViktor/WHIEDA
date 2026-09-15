#!/usr/bin/env python3
"""Compare golden HTTP run reports or baselines."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

TG = Path(__file__).resolve().parent
LAB = TG / "lab"


def _load_baseline():
    spec = importlib.util.spec_from_file_location("golden_baseline", LAB / "baseline.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_run(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "results" in data and "run_id" in data:
        return data
    if "cases" in data:
        return {"run_id": data.get("run_id"), "results": list(data["cases"].values())}
    raise ValueError(f"Unrecognized golden artifact: {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare golden HTTP runs")
    parser.add_argument("current", type=Path)
    parser.add_argument("baseline", type=Path)
    args = parser.parse_args()

    baseline_mod = _load_baseline()
    current_run = _load_run(args.current.resolve())
    previous = baseline_mod.load_baseline(args.baseline.resolve())
    if previous is None:
        previous = baseline_mod.build_baseline(_load_run(args.baseline.resolve()))
    diff = baseline_mod.compare_baselines(baseline_mod.build_baseline(current_run), previous)

    print(f"Compare status: {diff['status']}")
    print(f"Fixed: {len(diff.get('fixed') or [])}")
    print(f"Regressions: {len(diff.get('regressions') or [])}")
    print(f"Changed: {len(diff.get('changed') or [])}")
    print(f"Slower: {len(diff.get('slower') or [])}")
    print(f"New unasserted: {len(diff.get('new_unasserted') or [])}")
    if diff.get("regressions"):
        print("Regression ids:", ", ".join(diff["regressions"][:20]))
    return 0 if diff["status"] != "FAIL" else 1


if __name__ == "__main__":
    raise SystemExit(main())
