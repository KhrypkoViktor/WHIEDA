#!/usr/bin/env python3
"""One-command local Core runtime lab (Docker required)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from local_core_lab.orchestrator import OrchestratorConfig, run_lab  # noqa: E402


def _print_step_output(state) -> None:
    for step in state.steps:
        if step.stdout:
            print(step.stdout, end="" if step.stdout.endswith("\n") else "\n")
        if step.stderr:
            print(step.stderr, file=sys.stderr, end="" if step.stderr.endswith("\n") else "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA local Core runtime lab")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--leave-core-up", action="store_true")
    parser.add_argument(
        "--e2e",
        action="store_true",
        help="Full E2E: staging SQL + Core + acceptance P0 + verify + report",
    )
    parser.add_argument(
        "--parity",
        action="store_true",
        help="Run full Core advisor parity corpus (requires --e2e)",
    )
    parser.add_argument(
        "--no-blind-zone",
        action="store_true",
        help="Run no-blind-zone HTTP corpus (requires --e2e)",
    )
    parser.add_argument("--health-timeout", type=int, default=120)
    args = parser.parse_args()
    if args.parity and not args.e2e:
        parser.error("--parity requires --e2e")
    if args.no_blind_zone and not args.e2e:
        parser.error("--no-blind-zone requires --e2e")

    config = OrchestratorConfig(
        skip_build=args.skip_build,
        leave_core_up=args.leave_core_up,
        health_timeout_sec=args.health_timeout,
        e2e_mode=args.e2e,
        parity_mode=args.parity,
        no_blind_zone_mode=args.no_blind_zone,
    )
    code, state = run_lab(config)
    _print_step_output(state)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
