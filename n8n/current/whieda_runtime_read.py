"""Direct read-only queries against Supabase advisor runtime (no TEMP n8n workflows)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

from whieda_runtime_env import pg_env

_SELECT_ONLY = re.compile(r"^\s*(with\b|select\b)", re.IGNORECASE | re.DOTALL)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke)\b",
    re.IGNORECASE,
)


def _assert_readonly(query: str) -> None:
    clean = " ".join(query.split())
    if not _SELECT_ONLY.match(clean):
        raise ValueError("Only SELECT/WITH queries are allowed")
    if _FORBIDDEN.search(clean):
        raise ValueError("Mutating SQL is not allowed in runtime_read")


def query_rows(query: str, *, params: list[Any] | None = None) -> list[dict[str, Any]]:
    """Run a single read-only SQL statement and return rows as dicts."""
    _assert_readonly(query)
    binary = shutil.which("psql")
    if not binary:
        raise RuntimeError("psql is not installed or unavailable in PATH")

    wrapped = (
        "SELECT COALESCE(json_agg(row_to_json(x)), '[]'::json) "
        f"FROM ({query.rstrip(';')}) x;"
    )
    completed = subprocess.run(
        [binary, "-v", "ON_ERROR_STOP=1", "-At", "-c", wrapped],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=pg_env(),
        check=False,
    )
    if completed.returncode:
        raise RuntimeError((completed.stderr or completed.stdout).strip())
    raw = (completed.stdout or "").strip()
    if not raw:
        return []
    payload = json.loads(raw)
    if isinstance(payload, list):
        return payload
    return [payload]


def query_scalar(query: str) -> Any:
    rows = query_rows(f"SELECT t.* FROM ({query.rstrip(';')}) t")
    if not rows:
        return None
    return next(iter(rows.values()))
