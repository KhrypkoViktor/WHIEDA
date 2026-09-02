"""Local-only database guard for gap operator scripts."""

from __future__ import annotations

import re
from urllib.parse import urlparse

ALLOWED_DB_NAMES = frozenset({"whieda_platform_local_core"})
ALLOWED_VERIFY_DB = re.compile(r"^whieda_platform_staging_verify_[a-z0-9][a-z0-9_]*$", re.I)

FORBIDDEN_URL_FRAGMENTS = (
    "supabase",
    "amazonaws.com",
    "neon.tech",
    "185.252.232.93",
    "wwc.best",
)


def assert_local_database_url(database_url: str) -> None:
    lowered = str(database_url or "").lower()
    for fragment in FORBIDDEN_URL_FRAGMENTS:
        if fragment in lowered:
            raise RuntimeError(f"refusing production-like database URL (matched {fragment!r})")
    parsed = urlparse(database_url)
    db_name = (parsed.path or "").lstrip("/").split("?")[0]
    if not db_name:
        raise RuntimeError("database URL must include a database name")
    if db_name in ALLOWED_DB_NAMES or ALLOWED_VERIFY_DB.match(db_name):
        return
    raise RuntimeError(
        f"refusing database {db_name!r}; allowed: whieda_platform_local_core or staging_verify_*"
    )
