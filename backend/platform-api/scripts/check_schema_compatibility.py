"""Fail a release before new containers take traffic if the database lacks tables.

Run inside the freshly built image against the target .env, e.g.

    docker compose run --rm --no-deps api python scripts/check_schema_compatibility.py

Exit 0: every table required by the enabled features exists.
Exit 1: something is missing — the JSON on stdout says what, and which features
        are disabled via PLATFORM_DISABLED_FEATURES.
Exit 2: the database could not be reached.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schema_requirements import (  # noqa: E402
    missing_tables,
    parse_disabled_features,
    required_tables,
)
from app.settings import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    disabled = parse_disabled_features(settings.disabled_features)
    try:
        with psycopg.connect(str(settings.database_url), connect_timeout=10) as conn:
            rows = conn.execute("select tablename from pg_tables where schemaname = 'public'").fetchall()
    except psycopg.Error as exc:
        print(json.dumps({"ok": False, "error": f"database unreachable: {exc}"}))
        return 2
    existing = [row[0] for row in rows]
    missing = missing_tables(existing, disabled)
    print(
        json.dumps(
            {
                "ok": not missing,
                "environment": settings.environment,
                "disabled_features": sorted(disabled),
                "required_tables": len(required_tables(disabled)),
                "missing_tables": missing,
            },
            ensure_ascii=False,
        )
    )
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
