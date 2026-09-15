"""Read-only tenant canary preflight. No apply, publish, or live DSN."""

from __future__ import annotations

from tenant_canary.preflight import (
    PreflightReport,
    evaluate_preflight,
    refuse_shared_staging_readonly,
    report_to_json,
    report_to_markdown,
)

__all__ = [
    "PreflightReport",
    "evaluate_preflight",
    "refuse_shared_staging_readonly",
    "report_to_json",
    "report_to_markdown",
]
