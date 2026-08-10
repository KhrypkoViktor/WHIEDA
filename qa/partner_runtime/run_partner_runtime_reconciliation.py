#!/usr/bin/env python3
"""Partner runtime reconciliation local verification."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
N8N = ROOT / "n8n" / "current"
PLATFORM = ROOT / "backend" / "platform-api"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
CLI = N8N / "run_whieda_partner_runtime_reconciliation_2026-08-10.py"


def _pytest() -> int:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(PLATFORM / "tests" / "test_partner_runtime_reconciliation.py"),
            "-q",
        ],
        cwd=str(PLATFORM),
    ).returncode


def main() -> int:
    rc = _pytest()
    if rc != 0:
        print("pytest: FAIL")
        return rc
    print("pytest: PASS")

    proc = subprocess.run(
        [
            sys.executable,
            str(CLI),
            "--master-tsv",
            str(FIXTURES / "master_six_partners.tsv"),
            "--runtime-json",
            str(FIXTURES / "runtime_with_extras.json"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr)
        print("sample dry-run: FAIL")
        return proc.returncode

    report = json.loads(proc.stdout)
    checks = {
        "dry_run_mode": report.get("mode") == "dry_run",
        "allowlist_present": len(report.get("allowlisted_roots") or []) >= 2,
        "no_delete": report.get("would_delete") is False,
        "retired_proposed": any(
            item.get("actor_id") == "retired-partner"
            for item in report.get("proposed_actor_deactivations") or []
        ),
        "platform_roots_protected": not any(
            item.get("actor_id") in {"viktor", "viktor-test"}
            for item in report.get("proposed_actor_deactivations") or []
        ),
    }
    for name, ok in checks.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}")
        if not ok:
            return 1

    print("PARTNER_RUNTIME_RECONCILIATION: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
