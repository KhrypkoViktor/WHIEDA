"""SHA of the 18 Gate L APPLY_ORDER files. Read-only."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

_POSTGRES = Path(__file__).resolve().parents[4] / "postgres" / "scripts"
if str(_POSTGRES) not in sys.path:
    sys.path.insert(0, str(_POSTGRES))

from shared_staging_release_lib import APPLY_ORDER, EXPECTED_APPLY_COUNT, SQL_DIR, sha256_path  # noqa: E402
from staging_proof_lib import APPLY_ORDER as PROOF_ORDER  # noqa: E402


def migration_files() -> list[dict[str, str | None]]:
    rows = []
    for name in APPLY_ORDER:
        path = SQL_DIR / name
        rows.append(
            {
                "name": name,
                "sha256": sha256_path(path) if path.is_file() else None,
            }
        )
    return rows


def migration_manifest_sha() -> str:
    digest = hashlib.sha256()
    for name in APPLY_ORDER:
        path = SQL_DIR / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\n")
        digest.update((sha256_path(path) if path.is_file() else "").encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def migration_errors() -> list[str]:
    errors: list[str] = []
    if APPLY_ORDER != PROOF_ORDER:
        errors.append("APPLY_ORDER drifted from staging_proof_lib")
    if len(APPLY_ORDER) != EXPECTED_APPLY_COUNT:
        errors.append(f"APPLY_ORDER length {len(APPLY_ORDER)} != {EXPECTED_APPLY_COUNT}")
    missing = [name for name in APPLY_ORDER if not (SQL_DIR / name).is_file()]
    if missing:
        errors.append(f"missing SQL files: {missing}")
    return errors
