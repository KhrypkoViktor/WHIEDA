"""Ensure PGPASSWORD is available for direct runtime reads."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def ensure_pgpassword() -> None:
    if os.environ.get("PGPASSWORD"):
        return
    fetcher = Path(__file__).with_name("fetch_pg_password_from_n8n.py")
    completed = subprocess.run(
        [sys.executable, str(fetcher)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "fetch_pg_password failed").strip())
    os.environ["PGPASSWORD"] = completed.stdout.strip()
