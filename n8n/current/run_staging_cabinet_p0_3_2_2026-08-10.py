"""DEPRECATED (P0.3.5): use run_staging_cabinet_p0_3_5_2026-08-10.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CURRENT = Path(__file__).resolve().parent
NEW = CURRENT / "run_staging_cabinet_p0_3_5_2026-08-10.py"


def main() -> int:
    print("NOTE: P0.3.2 orchestrator deprecated — delegating to P0.3.5.")
    result = subprocess.run([sys.executable, str(NEW), *sys.argv[1:]], check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
