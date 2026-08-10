#!/usr/bin/env python3
"""Conversation reliability lab runner."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
CONV_ROOT = Path(__file__).resolve().parent
LAB = CONV_ROOT / "lab"
DEFAULT_CORPUS = CONV_ROOT / "whieda_conversation_flows_v1.jsonl"
DEFAULT_TARGET = ROOT / "qa" / "acceptance" / "acceptance_target.local.json"
REPORT_DIR = ROOT / "backend" / "platform-api" / "reports" / "conversation_reliability"
PLATFORM_TESTS = ROOT / "backend" / "platform-api" / "tests" / "test_conversation_reliability.py"


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


corpus_mod = _load("conv_corpus", LAB / "corpus.py")
runner_mod = _load("conv_runner", LAB / "runner.py")
guard_mod = _load("conv_guard", LAB / "target_guard.py")


def _pytest() -> int:
    cmd = [sys.executable, "-m", "pytest", str(PLATFORM_TESTS), "-q"]
    return subprocess.run(cmd, cwd=str(ROOT / "backend" / "platform-api")).returncode


def _safe_report(payload: dict) -> dict:
    """Strip session ids and credentials from published report."""
    cleaned = json.loads(json.dumps(payload))
    cleaned.pop("raw_bodies", None)
    for row in cleaned.get("results") or []:
        row.pop("session", None)
    return cleaned


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA conversation reliability lab")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--priority", choices=("P0", "P1"))
    parser.add_argument("--flow-id")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--turn-timeout", type=float, default=8.0)
    args = parser.parse_args()

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
        print("CONVERSATION_RELIABILITY: PASS (offline)")
        return 0

    if not args.target.is_file():
        print(f"Live HTTP: NOT_RUN (target missing {args.target})")
        return 0 if args.offline else 1

    try:
        from lab.target import load_target

        target = load_target(args.target)
        guard_mod.validate_local_target(str(target["base_url"]))
    except Exception as exc:
        print(f"Live HTTP: NOT_RUN ({exc})")
        return 0 if args.offline else 1

    payload = runner_mod.run_conversation_flows(
        target_path=args.target,
        flows=flows,
        priority=args.priority,
        flow_id=args.flow_id,
        timeout_seconds=args.turn_timeout,
        fail_fast=args.fail_fast,
    )
    safe = _safe_report(payload)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "latest_run.json").write_text(json.dumps(safe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(payload.get("summary_line"))
    if payload.get("status") != "PASS":
        for row in payload.get("results") or []:
            if row.get("status") != "PASS":
                print(f"  FAIL {row.get('flow_id')} turn {row.get('turn')}: {row.get('reason')}")
        return 1
    print("CONVERSATION_RELIABILITY: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
