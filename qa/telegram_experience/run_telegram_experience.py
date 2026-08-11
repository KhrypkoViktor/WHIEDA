#!/usr/bin/env python3
"""Telegram advisor experience acceptance runner."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
TG_ROOT = Path(__file__).resolve().parent
LAB = TG_ROOT / "lab"
DEFAULT_CORPUS = TG_ROOT / "whieda_telegram_experience_flows_v1.jsonl"
DEFAULT_TARGET = ROOT / "qa" / "acceptance" / "acceptance_target.local.json"
REPORT_DIR = ROOT / "backend" / "platform-api" / "reports" / "telegram_experience"
PLATFORM_TESTS = ROOT / "backend" / "platform-api" / "tests" / "test_telegram_experience.py"
COMPILE_FIXTURES = TG_ROOT / "compile_snapshot_fixtures.py"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


corpus_mod = _load("tg_corpus", LAB / "corpus.py")
runner_mod = _load("tg_runner", LAB / "runner.py")


def _pytest() -> int:
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(ROOT / "backend" / "platform-api" / "tests" / "test_telegram_product_card_renderer.py"),
        str(ROOT / "backend" / "platform-api" / "tests" / "test_telegram_chat_sequencer.py"),
        str(ROOT / "backend" / "platform-api" / "tests" / "test_telegram_goal_routing.py"),
        str(PLATFORM_TESTS),
        "-q",
    ]
    return subprocess.run(cmd, cwd=str(ROOT / "backend" / "platform-api")).returncode


def _compile_fixtures() -> int:
    return subprocess.run([sys.executable, str(COMPILE_FIXTURES)], cwd=str(ROOT)).returncode


def _print_category_report(payload: dict) -> None:
    stats = payload.get("category_stats") or {}
    for category in corpus_mod.CATEGORIES:
        row = stats.get(category) or {"flows": 0, "flows_passed": 0, "turns": 0, "turns_passed": 0}
        flows_total = row.get("flows", 0)
        flows_passed = row.get("flows_passed", 0)
        turns_total = row.get("turns", 0)
        turns_passed = row.get("turns_passed", 0)
        status = "PASS" if flows_total and flows_passed == flows_total and turns_passed == turns_total else (
            "FAIL" if flows_total else "NOT_RUN"
        )
        print(f"{category}: {status} ({flows_passed}/{flows_total} flows, {turns_passed}/{turns_total} turns)")


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA Telegram experience lab")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--category")
    parser.add_argument("--flow-id")
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args()

    if _compile_fixtures() != 0:
        print("Fixture compile: FAIL")
        return 1
    print("Fixture compile: PASS")

    if not args.corpus.is_file():
        build_script = TG_ROOT / "build_flows.py"
        if build_script.is_file():
            subprocess.run([sys.executable, str(build_script)], cwd=str(ROOT), check=False)
    if not args.corpus.is_file():
        print(f"FAIL: corpus missing {args.corpus}", file=sys.stderr)
        return 1

    flows = corpus_mod.load_flows(args.corpus)
    lint_errors = corpus_mod.validate_flows(flows)
    stats = corpus_mod.flow_stats(flows)
    if lint_errors:
        print("Corpus lint: FAIL")
        for err in lint_errors:
            print(f"  - {err}")
        return 1
    print(f"Corpus lint: PASS ({stats['flows']} flows, {stats['turns']} turns)")

    if _pytest() != 0:
        print("Unit tests: FAIL")
        return 1
    print("Unit tests: PASS")

    if args.offline and not args.live:
        print("HTTP E2E: NOT_RUN (offline mode)")
        for category in corpus_mod.CATEGORIES:
            count = stats["by_category"].get(category, 0)
            print(f"{category}: NOT_RUN ({count} flows in corpus)")
        print("TELEGRAM_EXPERIENCE: PASS (offline)")
        return 0

    if not args.target.is_file():
        print(f"Live HTTP: NOT_RUN (target missing {args.target})")
        return 0 if args.offline else 1

    from lab.target import load_target

    target = load_target(args.target)
    payload = runner_mod.run_telegram_flows(
        target_path=args.target,
        flows=flows,
        category=args.category,
        flow_id=args.flow_id,
        fail_fast=args.fail_fast,
    )
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "latest_run.json").write_text(
        __import__("json").dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(payload.get("summary_line"))
    _print_category_report(payload)
    if payload.get("status") != "PASS":
        for row in payload.get("results") or []:
            if row.get("status") != "PASS":
                reason = str(row.get("reason") or "").encode("ascii", "backslashreplace").decode("ascii")
                print(f"  FAIL {row.get('flow_id')} turn {row.get('turn')}: {reason}")
        return 1
    print("TELEGRAM_EXPERIENCE: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
