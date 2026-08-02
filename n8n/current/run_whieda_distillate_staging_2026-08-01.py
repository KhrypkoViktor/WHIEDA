"""Fetch Supabase password from live n8n credentials and run distillate staging."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
SOURCE_DIR = BASE.parents[1] / "RAG" / "1 компиляция. диалоги с врачами" / "2_SQL_корпус_из_RAW"
IMPORTER = BASE / "run_whieda_distillate_import_v0_1.py"


def run_importer(*extra: str) -> None:
    if not os.environ.get("PGPASSWORD"):
        from fetch_pg_password_from_n8n import fetch_password

        os.environ["PGPASSWORD"] = fetch_password()
    env = os.environ.copy()
    cmd = [sys.executable, str(IMPORTER), "--source-dir", str(SOURCE_DIR), "--no-publish", *extra]
    completed = subprocess.run(
        cmd, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True
    )
    if completed.returncode != 0:
        if completed.stderr:
            print(completed.stderr.strip(), file=sys.stderr)
        raise SystemExit(completed.returncode)
    if completed.stdout and completed.stdout.strip():
        print(completed.stdout.strip())


def main() -> None:
    if not SOURCE_DIR.is_dir():
        raise SystemExit(f"Source dir missing: {SOURCE_DIR}")
    run_importer("--validate")
    run_importer("--full-staging-run", "--migrate")


if __name__ == "__main__":
    main()
