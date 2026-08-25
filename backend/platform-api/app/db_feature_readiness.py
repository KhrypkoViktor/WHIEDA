"""Schema-feature readiness: required tables present, or fail closed before user actions."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Sequence

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 30.0
ONBOARDING_UNAVAILABLE_TEXT = (
    "Раздел для новичков сейчас обновляется. "
    "Пока могу помочь с товарами, ценами, подбором или бизнес-вопросами."
)

FEATURE_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "onboarding": {
        "tables": [
            "onboarding_programs",
            "onboarding_steps",
            "onboarding_enrollments",
            "onboarding_progress",
            "onboarding_reminders",
            "mentor_escalations",
        ],
        "migration": "platform_onboarding_v1.sql",
    },
    "telegram_durable_inbox": {
        "tables": ["telegram_update_inbox"],
        "migration": "platform_telegram_durable_inbox_v1.sql",
    },
    "telegram_durable_outbox": {
        "tables": ["telegram_delivery_outbox"],
        "columns": [
            "telegram_update_id",
            "sequence_no",
            "kind",
            "payload_json",
            "status",
        ],
        "migration": "platform_telegram_durable_outbox_v1.sql",
    },
}

REPO_ROOT = Path(__file__).resolve().parents[3]
SQL_DIR = REPO_ROOT / "postgres" / "sql"

ProbeFn = Callable[[Sequence[str]], Awaitable[tuple[str, ...] | None]]
ClockFn = Callable[[], float]


@dataclass(frozen=True)
class FeatureStatus:
    feature: str
    state: str
    missing_tables: tuple[str, ...] = ()
    migration: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "ready"


@dataclass(frozen=True)
class ReadinessReport:
    ok: bool
    state: str
    mode: str
    features: list[FeatureStatus] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "state": self.state,
            "mode": self.mode,
            "features": [
                {
                    "feature": item.feature,
                    "state": item.state,
                    "tables": list(FEATURE_REQUIREMENTS[item.feature]["tables"]),
                    "missing_tables": list(item.missing_tables),
                    "migration": item.migration,
                }
                for item in self.features
            ],
        }


class SchemaFeatureUnavailable(Exception):
    def __init__(self, status: FeatureStatus):
        self.status = status
        super().__init__(f"schema_feature_unavailable:{status.feature}:{status.state}")


_cache: dict[str, tuple[float, FeatureStatus]] = {}
_probe_override: ProbeFn | None = None
_clock: ClockFn = time.monotonic


def reset_feature_readiness_cache() -> None:
    _cache.clear()


def set_table_probe_for_tests(probe: ProbeFn | None) -> None:
    global _probe_override
    _probe_override = probe


def set_clock_for_tests(clock: ClockFn | None) -> None:
    global _clock
    _clock = clock or time.monotonic


def reset_feature_readiness_for_tests() -> None:
    reset_feature_readiness_cache()
    set_table_probe_for_tests(None)
    set_clock_for_tests(None)


def required_tables(feature: str) -> tuple[str, ...]:
    return tuple(FEATURE_REQUIREMENTS[feature]["tables"])


def log_feature_unavailable(status: FeatureStatus) -> None:
    logger.warning(
        "schema_feature_unavailable",
        extra={
            "feature": status.feature,
            "missing_tables": list(status.missing_tables),
            "state": status.state,
        },
    )


def evaluate_from_relations(
    relations: Iterable[str],
    *,
    mode: str = "offline-manifest",
) -> ReadinessReport:
    present = {str(name).strip() for name in relations if str(name).strip()}
    features: list[FeatureStatus] = []
    for name, spec in FEATURE_REQUIREMENTS.items():
        missing = tuple(table for table in spec["tables"] if table not in present)
        features.append(
            FeatureStatus(
                feature=name,
                state="degraded" if missing else "ready",
                missing_tables=missing,
                migration=str(spec["migration"]),
            )
        )
    overall = "ready" if all(item.state == "ready" for item in features) else "degraded"
    return ReadinessReport(
        ok=overall == "ready",
        state=overall,
        mode=mode,
        features=features,
    )


def format_status_lines(report: ReadinessReport) -> str:
    if report.state == "ready":
        return "ready"
    if report.state == "unknown":
        return "unknown: database probe failed"
    lines: list[str] = []
    for item in report.features:
        if item.state == "degraded":
            missing = ", ".join(item.missing_tables)
            lines.append(f"degraded: {item.feature} (missing {missing})")
        elif item.state == "unknown":
            lines.append("unknown: database probe failed")
    return "\n".join(lines) if lines else "ready"


def unknown_report(*, mode: str = "probe") -> ReadinessReport:
    features = [
        FeatureStatus(
            feature=name,
            state="unknown",
            missing_tables=(),
            migration=str(spec["migration"]),
        )
        for name, spec in FEATURE_REQUIREMENTS.items()
    ]
    return ReadinessReport(ok=False, state="unknown", mode=mode, features=features)


async def probe_missing_tables(tables: Sequence[str]) -> tuple[str, ...] | None:
    if _probe_override is not None:
        return await _probe_override(tuple(tables))
    try:
        from app.db import fetch_one, get_pool
        from app.settings import get_settings

        pool = get_pool()
        missing: list[str] = []
        async with pool.connection(timeout=get_settings().database_timeout_sec) as conn:
            for table in tables:
                row = await fetch_one(
                    conn,
                    "select to_regclass(%s) as rel",
                    (f"public.{table}",),
                )
                if not row or row.get("rel") is None:
                    missing.append(table)
        return tuple(missing)
    except Exception:
        return None


async def probe_missing_columns(table: str, columns: Sequence[str]) -> tuple[str, ...] | None:
    try:
        from app.db import fetch_all, get_pool
        from app.settings import get_settings

        pool = get_pool()
        async with pool.connection(timeout=get_settings().database_timeout_sec) as conn:
            rows = await fetch_all(
                conn,
                """
                select column_name
                from information_schema.columns
                where table_schema = 'public' and table_name = %s
                """,
                (table,),
            )
        present = {str(row.get("column_name") or "") for row in rows}
        return tuple(name for name in columns if name not in present)
    except Exception:
        return None


async def get_feature_status(feature: str) -> FeatureStatus:
    spec = FEATURE_REQUIREMENTS[feature]
    now = _clock()
    cached = _cache.get(feature)
    if cached is not None and cached[0] > now:
        return cached[1]
    missing = await probe_missing_tables(spec["tables"])
    if missing is None:
        status = FeatureStatus(
            feature=feature,
            state="unknown",
            missing_tables=(),
            migration=str(spec["migration"]),
        )
    else:
        if not missing and _probe_override is None:
            required_columns = spec.get("columns") or []
            table_name = spec["tables"][0] if spec["tables"] else ""
            if required_columns and table_name:
                missing_columns = await probe_missing_columns(table_name, required_columns)
                if missing_columns is None:
                    missing = None
                else:
                    missing = tuple(f"{table_name}.{name}" for name in missing_columns)
        if missing is None:
            status = FeatureStatus(
                feature=feature,
                state="unknown",
                missing_tables=(),
                migration=str(spec["migration"]),
            )
        elif missing:
            status = FeatureStatus(
                feature=feature,
                state="degraded",
                missing_tables=tuple(missing),
                migration=str(spec["migration"]),
            )
        else:
            status = FeatureStatus(
                feature=feature,
                state="ready",
                missing_tables=(),
                migration=str(spec["migration"]),
            )
    _cache[feature] = (now + CACHE_TTL_SECONDS, status)
    return status


def migration_paths() -> dict[str, Path]:
    return {
        name: SQL_DIR / str(spec["migration"])
        for name, spec in FEATURE_REQUIREMENTS.items()
    }
