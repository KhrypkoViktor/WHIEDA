#!/usr/bin/env python3
"""No blind zone regression runner — offline lint + optional live Core HTTP."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType


def _load(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ROOT = Path(__file__).resolve().parents[2]
NBZ_ROOT = Path(__file__).resolve().parent
LAB = NBZ_ROOT / "lab"
ACCEPTANCE = ROOT / "qa" / "acceptance"
DEFAULT_CORPUS = NBZ_ROOT / "whieda_no_blind_zone_cases_v1.jsonl"
DEFAULT_TARGET = ACCEPTANCE / "acceptance_target.local.json"
PLATFORM_TESTS = ROOT / "backend" / "platform-api" / "tests" / "test_no_blind_zone.py"

corpus_mod = _load("nbz_corpus", LAB / "corpus.py")
runner_mod = _load("nbz_runner", LAB / "runner.py")


def _run_pytest() -> int:
    cmd = [sys.executable, "-m", "pytest", str(PLATFORM_TESTS), "-q"]
    print(" ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(ROOT))
    return proc.returncode


def _try_live_http(args: argparse.Namespace) -> dict | None:
    target_path = args.target.resolve()
    if not target_path.is_file():
        print(f"Live HTTP skipped: target missing ({target_path})")
        return None
    try:
        check = importlib.util.spec_from_file_location(
            "parity_check_target",
            ROOT / "qa" / "parity" / "lab" / "check_target.py",
        )
        assert check and check.loader
        mod = importlib.util.module_from_spec(check)
        check.loader.exec_module(mod)
        from lab.target import load_target

        target = load_target(target_path)
        mod.validate_local_target(str(target["base_url"]))
        ready = mod.check_target_ready(target_path, timeout_seconds=args.case_timeout)
        if ready.get("status") != "PASS":
            print("Live HTTP skipped: target not ready")
            for err in ready.get("errors") or []:
                print(f"  - {err}")
            return None
    except Exception as exc:
        print(f"Live HTTP skipped: {exc}")
        return None

    print("Live HTTP: target ready")
    return runner_mod.run_nbz_cases(
        target_path=target_path,
        corpus_path=args.corpus.resolve(),
        priority=args.priority,
        case_id=args.case_id,
        group=args.group,
        limit=args.limit,
        fail_fast=args.fail_fast,
        timeout_seconds=args.case_timeout,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA no-blind-zone regression runner")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--offline", action="store_true", help="Corpus lint + unit tests only")
    parser.add_argument("--live", action="store_true", help="Force live HTTP when target is up")
    parser.add_argument("--priority", choices=("P0", "P1"))
    parser.add_argument("--case-id")
    parser.add_argument("--group")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--case-timeout", type=float, default=5.0)
    args = parser.parse_args()

    if not args.corpus.is_file():
        print(f"FAIL: corpus not found: {args.corpus}", file=sys.stderr)
        return 1

    cases = corpus_mod.load_corpus(args.corpus)
    lint_errors = corpus_mod.validate_corpus(cases)
    if lint_errors:
        print("Corpus lint: FAIL")
        for err in lint_errors:
            print(f"  - {err}")
        return 1
    print(f"Corpus lint: PASS ({len(cases)} cases)")

    pytest_code = _run_pytest()
    if pytest_code != 0:
        print("Unit tests: FAIL")
        return pytest_code
    print("Unit tests: PASS")

    if args.offline and not args.live:
        print("Offline mode: HTTP corpus NOT_RUN (Docker optional)")
        print("NBZ offline summary: PASS")
        return 0

    payload = _try_live_http(args)
    if payload is None:
        if args.live:
            print("FAIL: --live requested but target unavailable", file=sys.stderr)
            return 1
        print("HTTP corpus: NOT_RUN")
        print("NBZ summary: PASS (offline only)")
        return 0

    if payload.get("total_line"):
        print(payload["total_line"])
    if payload.get("p0_line"):
        print(payload["p0_line"])
    if payload.get("p1_line"):
        print(payload["p1_line"])

    for row in payload.get("results") or []:
        if row.get("status") != "PASS":
            print(f"FAIL {row.get('case_id')}: {', '.join(row.get('errors') or [])}")

    return 0 if payload.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
