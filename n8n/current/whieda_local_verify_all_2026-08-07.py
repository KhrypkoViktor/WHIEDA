#!/usr/bin/env python3
"""Single local verification runner — pytest + optional HTTP smokes."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = ROOT / "backend" / "platform-api"
MANIFEST = ROOT / "WHIEDA_LOCAL_BUILD_BLOCKS_V1.json"

SMOKES = [
    ROOT / "n8n" / "current" / "whieda_core_p0_local_full_smoke_2026-08-07.py",
    ROOT / "n8n" / "current" / "whieda_core_parity_local_smoke_2026-08-07.py",
    ROOT / "n8n" / "current" / "whieda_core_service_media_smoke_2026-08-07.py",
]


def run(cmd: list[str], *, cwd: Path | None = None) -> int:
    print(f"\n>>> {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=cwd or ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA local verify-all")
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--skip-smokes", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--with-e2e", action="store_true", help="Run staging journey E2E (needs Core + DB)")
    args = parser.parse_args()

    rc = 0

    if MANIFEST.exists():
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        s = data.get("summary", {})
        print(f"Build blocks: {data.get('total_blocks')} | done_local={s.get('done_local')} blocked_live={s.get('blocked_live')}")

    if not args.skip_pytest:
        rc = run([sys.executable, "-m", "pytest", "tests/", "-q"], cwd=PLATFORM)
        if rc != 0:
            return rc

    if not args.skip_smokes:
        for smoke in SMOKES:
            if smoke.exists():
                rc = run([sys.executable, str(smoke), "--base-url", args.base_url])
                if rc != 0:
                    return rc

    if args.with_e2e:
        e2e = ROOT / "n8n" / "current" / "whieda_staging_journey_e2e_2026-08-07.py"
        if e2e.exists():
            rc = run([sys.executable, str(e2e), "--base-url", args.base_url])
            if rc != 0:
                return rc

    print("\n=== LOCAL VERIFY ALL: OK ===")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
