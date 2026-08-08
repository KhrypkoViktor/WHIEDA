#!/usr/bin/env python3
"""Read-only doctor for WHIEDA local Core E2E lab prerequisites."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from local_core_lab.doctor import run_all_checks, summarize  # noqa: E402


def main() -> int:
    print("=== WHIEDA Local Core Lab — environment doctor ===\n")
    checks = run_all_checks()
    width = max(len(c.name) for c in checks) + 2
    for check in checks:
        print(f"[{check.status:<4}] {check.name.ljust(width)}{check.message}")

    overall, code = summarize(checks)
    print(f"\nOverall: {overall}")
    if overall == "FAIL":
        print("\nFix FAIL items before running:")
        print("  python backend\\platform-api\\scripts\\run_local_core_lab.py --e2e")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
