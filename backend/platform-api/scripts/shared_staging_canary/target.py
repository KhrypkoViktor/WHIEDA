"""Shared-staging canary target contract. Extends Gate B2; never writes."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
_POSTGRES = Path(__file__).resolve().parents[4] / "postgres" / "scripts"
if str(_POSTGRES) not in sys.path:
    sys.path.insert(0, str(_POSTGRES))

from shared_staging_release_lib import (  # noqa: E402
    FORBIDDEN_SHARED_HOST_FRAGMENTS,
    LOCAL_VERIFY_DB_RE,
    ParsedTarget,
    parse_dsn,
)

LOCAL_CORE_DB_NAMES = frozenset(
    {
        "whieda_platform_local_core",
        "whieda_platform_telegram_canary",
        "whieda_platform_shared_staging_local",
    }
)
PRODUCTION_DB_NAMES = frozenset({"postgres", "whieda_production", "wwc_production"})
SUPERUSER_ROLES = frozenset({"postgres", "supabase_admin", "rds_superuser"})


class TargetGuardError(ValueError):
    """DSN is not a permitted shared-staging canary write target."""


@dataclass(frozen=True)
class CanaryTarget:
    host: str
    port: int
    database: str
    user: str
    redacted_dsn: str


def _same_target(left: str, right: str) -> bool:
    try:
        a = parse_dsn(left)
        b = parse_dsn(right)
    except Exception:
        return str(left).strip() == str(right).strip()
    return (a.host, a.port, a.database) == (b.host, b.port, b.database)


def validate_canary_target(
    dsn: str | None,
    *,
    expected_db: str | None,
    runtime_readonly_dsn: str | None,
) -> CanaryTarget:
    if not str(dsn or "").strip():
        raise TargetGuardError("missing WHIEDA_SHARED_STAGING_DSN")
    if not str(expected_db or "").strip():
        raise TargetGuardError("missing WHIEDA_SHARED_STAGING_EXPECTED_DB")
    try:
        parsed: ParsedTarget = parse_dsn(dsn)
    except Exception as exc:
        raise TargetGuardError(str(exc)) from exc

    expected = str(expected_db).strip()
    if parsed.database != expected:
        raise TargetGuardError(
            f"DSN database {parsed.database!r} does not match expected database {expected!r}"
        )

    if runtime_readonly_dsn and _same_target(str(dsn), runtime_readonly_dsn):
        raise TargetGuardError("refusing WHIEDA_RUNTIME_READONLY_DSN as a shared-staging write target")

    for fragment in FORBIDDEN_SHARED_HOST_FRAGMENTS:
        if fragment in parsed.host:
            raise TargetGuardError(
                f"refusing host {parsed.host!r} for shared staging (matches {fragment!r})"
            )

    if LOCAL_VERIFY_DB_RE.match(parsed.database):
        raise TargetGuardError(
            f"refusing local verify database {parsed.database!r} as a shared-staging target"
        )
    if parsed.database in LOCAL_CORE_DB_NAMES:
        raise TargetGuardError(
            f"refusing local Core database {parsed.database!r} as a shared-staging target"
        )
    if parsed.database in PRODUCTION_DB_NAMES:
        raise TargetGuardError(
            f"refusing production database name {parsed.database!r}"
        )
    if parsed.user.lower() in SUPERUSER_ROLES:
        raise TargetGuardError(f"refusing role {parsed.user!r}; shared staging forbids superuser aliases")

    return CanaryTarget(
        host=parsed.host,
        port=parsed.port,
        database=parsed.database,
        user=parsed.user,
        redacted_dsn=parsed.redacted_dsn,
    )
