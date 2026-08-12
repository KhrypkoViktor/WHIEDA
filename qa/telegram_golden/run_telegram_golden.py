#!/usr/bin/env python3
"""Telegram golden corpus offline runner."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
TG = Path(__file__).resolve().parent
LAB = TG / "lab"
CASES = TG / "whieda_telegram_golden_cases_v1.jsonl"
FLOWS = TG / "whieda_telegram_golden_flows_v1.jsonl"
NEGATIVE = TG / "whieda_telegram_golden_negative_fixtures_v1.jsonl"
GOLDEN_TEST = ROOT / "backend" / "platform-api" / "tests" / "test_telegram_golden_corpus.py"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


corpus_mod = _load("tg_golden_corpus", LAB / "corpus.py")
offline_mod = _load("tg_golden_offline", LAB / "offline_runner.py")
importer_mod = _load("tg_golden_importer", LAB / "importer.py")


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA Telegram golden corpus lab")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--flows", type=Path, default=FLOWS)
    parser.add_argument("--negative", type=Path, default=NEGATIVE)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()

    build = TG / "build_golden_corpus.py"
    if build.is_file() and not args.skip_build:
        code = subprocess.run([sys.executable, str(build)], cwd=str(ROOT)).returncode
        if code != 0:
            print("Build: FAIL", file=sys.stderr)
            return code

    if not args.cases.is_file():
        print(f"FAIL: cases missing {args.cases}", file=sys.stderr)
        return 1
    if not args.flows.is_file():
        print(f"FAIL: flows missing {args.flows}", file=sys.stderr)
        return 1

    cases = corpus_mod.load_jsonl(args.cases)
    flows = corpus_mod.load_jsonl(args.flows)
    negative: list[dict] = []
    if args.negative.is_file():
        negative = corpus_mod.load_jsonl(args.negative)
    result = offline_mod.run_offline(
        cases=cases,
        flows=flows,
        corpus_mod=corpus_mod,
        importer_mod=importer_mod,
        negative_fixtures=negative if negative else None,
    )

    print(result["summary_line"])
    def _safe_print(label: str, errors: list[str]) -> None:
        if not errors:
            return
        print(label)
        for err in errors:
            line = f"  - {err}"
            try:
                print(line)
            except UnicodeEncodeError:
                print(line.encode("ascii", errors="backslashreplace").decode("ascii"))

    if result["case_errors"]:
        _safe_print("Corpus lint: FAIL", result["case_errors"])
    else:
        print(f"Corpus lint: PASS ({result['stats']['cases']} cases)")

    if result["flow_errors"]:
        _safe_print("Flow lint: FAIL", result["flow_errors"])
    else:
        print(f"Flow lint: PASS ({result['flow_stats']['flows']} flows)")

    if result["snapshot_errors"]:
        _safe_print("Snapshot lint: FAIL", result["snapshot_errors"])
    else:
        print("Snapshot lint: PASS")

    if result["roundtrip_errors"]:
        _safe_print("Importer round-trip: FAIL", result["roundtrip_errors"])
    else:
        print("Importer round-trip: PASS")

    if result.get("negative_errors"):
        _safe_print("Negative fixtures: FAIL", result["negative_errors"])
    elif result.get("negative_count"):
        print(f"Negative fixtures: PASS ({result['negative_count']})")
    else:
        print("Negative fixtures: NOT_LOADED")

    if result["status"] != "PASS":
        print("TELEGRAM_GOLDEN: FAIL", file=sys.stderr)
        return 1

    if GOLDEN_TEST.is_file():
        code = subprocess.run(
            [sys.executable, "-m", "pytest", str(GOLDEN_TEST), "-q"],
            cwd=str(ROOT / "backend" / "platform-api"),
        ).returncode
        if code != 0:
            print("Golden tests: FAIL")
            return code
        print("Golden tests: PASS")

    if args.offline:
        print("HTTP E2E: NOT_RUN (offline mode)")

    print("TELEGRAM_GOLDEN: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
