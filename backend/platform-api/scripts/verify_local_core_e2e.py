#!/usr/bin/env python3
"""Non-mock E2E verification against local Core on 127.0.0.1:8080."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from local_core_lab.constants import API_BASE  # noqa: E402
from local_core_lab.e2e_verify import assert_local_base, run_verify, verify_core_down  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify local Core E2E (real HTTP, no mocks)")
    parser.add_argument("--base-url", default=API_BASE)
    parser.add_argument("--skip-p0", action="store_true", help="Skip live P0 acceptance report check")
    parser.add_argument("--expect-down", action="store_true", help="Assert Core is not reachable (pre-lab)")
    args = parser.parse_args()

    try:
        assert_local_base(args.base_url.rstrip("/"))
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    if args.expect_down:
        if verify_core_down(args.base_url.rstrip("/")):
            print("PASS: Core is down as expected")
            return 0
        print("FAIL: Core is reachable — expected connection failure", file=sys.stderr)
        return 1

    print(f"=== verify local Core E2E: {args.base_url} ===")
    result = run_verify(args.base_url.rstrip("/"), include_p0=not args.skip_p0)
    for check in result["checks"]:
        print(f"  [{check['status']}] {check['name']}: {check['message']}")

    print(f"\n=== VERIFY LOCAL CORE E2E: {result['status']} ===")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
