"""Read-only external runtime loader for partner reconciliation V2."""

from __future__ import annotations

import re
from typing import Any, Sequence

from whieda_partner_runtime_reconciliation_lib import (
    AllowlistConfig,
    MasterSourceError,
    ReconciliationPlan,
    RuntimeActor,
    RuntimeProfile,
    RuntimeState,
    validate_master_rows,
)

SUPPORTED_TENANTS = frozenset({"whieda"})
REQUIRED_RUNTIME_TABLES = ("lead_actors", "referral_profiles")
READONLY_SQL_PREFIX = re.compile(r"^\s*(BEGIN\s+READ\s+ONLY|SELECT|COMMIT)\s*", re.I | re.DOTALL)

DSN_PASSWORD_RE = re.compile(r"://([^:@/]+):([^@/]+)@", re.I)
SENSITIVE_PATTERNS = (
    DSN_PASSWORD_RE,
    re.compile(r"password\s*=\s*\S+", re.I),
    re.compile(r"postgresql://[^\s]+", re.I),
)


class RuntimeReadError(RuntimeError):
    """Read-only runtime access failed."""


class ConfigConflictError(ValueError):
    """Mutually exclusive CLI options."""


def redact_sensitive_text(text: str) -> str:
    redacted = text
    for pattern in SENSITIVE_PATTERNS:
        if pattern is DSN_PASSWORD_RE:
            redacted = pattern.sub(r"://\1:[REDACTED]@", redacted)
        elif "postgresql://" in pattern.pattern:
            redacted = pattern.sub("postgresql://[REDACTED]", redacted)
        else:
            redacted = pattern.sub("password=[REDACTED]", redacted)
    return redacted


def assert_readonly_sql_only(statements: Sequence[str]) -> None:
    for statement in statements:
        stripped = statement.strip()
        if not stripped:
            continue
        if not READONLY_SQL_PREFIX.match(stripped):
            raise RuntimeReadError(f"non-readonly SQL rejected: {stripped.split()[0]!r}")


def _table_exists(cur, table: str) -> bool:
    cur.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = %s
        LIMIT 1
        """,
        (table,),
    )
    return cur.fetchone() is not None


def load_runtime_from_readonly_dsn(dsn: str, tenant_id: str) -> RuntimeState:
    if tenant_id not in SUPPORTED_TENANTS:
        raise RuntimeReadError(f"unsupported tenant: {tenant_id}")

    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover
        raise RuntimeReadError("psycopg required for runtime read-only mode") from exc

    statements = [
        "BEGIN READ ONLY",
        "SELECT actor_id, display_name, active, telegram_username FROM lead_actors WHERE tenant_id = %s",
        "SELECT ref_code, owner_id, enabled, display_mode FROM referral_profiles WHERE tenant_id = %s",
        "COMMIT",
    ]
    assert_readonly_sql_only(statements)

    actors: dict[str, RuntimeActor] = {}
    profiles: list[RuntimeProfile] = []

    try:
        with psycopg.connect(dsn) as conn:
            conn.read_only = True
            with conn.transaction():
                with conn.cursor() as cur:
                    for table in REQUIRED_RUNTIME_TABLES:
                        if not _table_exists(cur, table):
                            raise RuntimeReadError(f"missing required runtime relation: {table}")

                    cur.execute(
                        """
                        SELECT actor_id, display_name, active, coalesce(telegram_username, '')
                        FROM lead_actors
                        WHERE tenant_id = %s
                        ORDER BY actor_id
                        """,
                        (tenant_id,),
                    )
                    for actor_id, display_name, active, telegram_username in cur.fetchall():
                        actors[str(actor_id)] = RuntimeActor(
                            actor_id=str(actor_id),
                            display_name=str(display_name),
                            active=bool(active),
                            telegram_username=str(telegram_username) or None,
                        )

                    cur.execute(
                        """
                        SELECT ref_code, owner_id, enabled, display_mode
                        FROM referral_profiles
                        WHERE tenant_id = %s
                        ORDER BY ref_code
                        """,
                        (tenant_id,),
                    )
                    for ref_code, owner_id, enabled, display_mode in cur.fetchall():
                        profiles.append(
                            RuntimeProfile(
                                ref_code=str(ref_code),
                                owner_id=str(owner_id),
                                enabled=bool(enabled),
                                display_mode=str(display_mode or "named"),
                            )
                        )
    except RuntimeReadError:
        raise
    except Exception as exc:
        raise RuntimeReadError(redact_sensitive_text(str(exc))) from exc

    return RuntimeState(actors=actors, profiles=profiles)


def detect_master_review_rows(rows: Sequence[dict[str, str]]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for row in rows:
        partner_id = (row.get("partner_id") or "").strip()
        ref_code = (row.get("ref_code") or "").strip()
        site_type = (row.get("site_type") or "").strip().lower()
        owner_actor_id = (row.get("owner_actor_id") or partner_id).strip()
        active = str(row.get("active") or "").strip().lower() in {"true", "1", "yes", "y"}
        if active and not ref_code:
            issues.append(
                {
                    "partner_id": partner_id,
                    "issue": "active_partner_missing_ref_code",
                    "reason": "Active master row has no ref_code; owner review required.",
                }
            )
        if site_type == "platform_root" and not owner_actor_id:
            issues.append(
                {
                    "partner_id": partner_id,
                    "issue": "platform_root_missing_owner",
                    "reason": "platform_root row requires owner_actor_id.",
                }
            )
    return issues


def classify_parity_summary(
    plan: ReconciliationPlan,
    *,
    master_review_rows: Sequence[dict[str, str]],
    abort_reason: str | None = None,
) -> str:
    if abort_reason:
        return "abort"
    if master_review_rows:
        return "review_required"
    if plan.proposed_actor_deactivations or plan.proposed_profile_deactivations:
        return "review_required"
    return "safe"


def build_v2_parity_report(
    plan: ReconciliationPlan,
    *,
    master_rows: Sequence[dict[str, str]],
    allowlist: AllowlistConfig,
    master_review_rows: Sequence[dict[str, str]],
    runtime_source: str,
    runtime_profile_count: int,
    abort_reason: str | None = None,
) -> dict[str, Any]:
    validated = validate_master_rows(master_rows) if not abort_reason else []
    master_active = sum(
        1
        for row in validated
        if str(row.get("active") or "").strip().lower() in {"true", "1", "yes", "y", ""}
    )
    active_runtime = sum(1 for actor in plan.active_runtime_actors if actor.get("active"))
    summary = classify_parity_summary(plan, master_review_rows=master_review_rows, abort_reason=abort_reason)

    report = {
        "ok": abort_reason is None,
        "mode": "runtime_readonly_dry_run",
        "runtime_source": runtime_source,
        "tenant_id": allowlist.tenant_id,
        "summary": summary,
        "abort_reason": abort_reason,
        "counts": {
            "master_active_partners": master_active,
            "active_runtime_actors": active_runtime,
            "active_referral_profiles": runtime_profile_count,
        },
        "allowlisted_platform_roots": [entry.actor_id for entry in allowlist.platform_root_allowlist],
        "master_only_needing_upsert": list(plan.master_actor_ids),
        "runtime_only_disable_candidates": [
            {
                "actor_id": item.actor_id,
                "display_name": item.display_name,
                "action": "set_active_false",
            }
            for item in plan.proposed_actor_deactivations
        ],
        "profiles_affected_by_owner_disable": [
            {"ref_code": item.ref_code, "owner_id": item.owner_id}
            for item in plan.proposed_profile_deactivations
        ],
        "master_review_required": list(master_review_rows),
        "would_delete": False,
    }
    return sanitize_public_report(report)


def sanitize_public_report(report: dict[str, Any]) -> dict[str, Any]:
    """Remove chat IDs, phones, raw JSON, DSN fragments from operator report."""
    forbidden_keys = {
        "telegram_chat_id",
        "phone",
        "public_profile",
        "raw_profile",
        "dsn",
        "password",
    }
    text_blob = redact_sensitive_text(str(report))

    def _scrub(obj: Any) -> Any:
        if isinstance(obj, dict):
            cleaned = {}
            for key, value in obj.items():
                if key in forbidden_keys:
                    continue
                if key == "telegram_username" and value:
                    cleaned[key] = "[present]"
                    continue
                cleaned[key] = _scrub(value)
            return cleaned
        if isinstance(obj, list):
            return [_scrub(item) for item in obj]
        if isinstance(obj, str):
            if obj.isdigit() and len(obj) >= 8:
                return "[redacted_id]"
            return redact_sensitive_text(obj)
        return obj

    scrubbed = _scrub(report)
    if "postgresql://" in text_blob or "password=" in text_blob.lower():
        scrubbed["_redaction_applied"] = True
    return scrubbed


def build_operator_markdown(report: dict[str, Any]) -> str:
    counts = report.get("counts") or {}
    lines = [
        "# Partner Runtime Parity (read-only)",
        "",
        f"**Summary:** `{report.get('summary', 'abort')}`",
        f"**Tenant:** {report.get('tenant_id')}",
        f"**Runtime source:** {report.get('runtime_source')}",
        "",
        "## Counts",
        f"- Master active partners: {counts.get('master_active_partners')}",
        f"- Active runtime actors: {counts.get('active_runtime_actors')}",
        f"- Active referral profiles: {counts.get('active_referral_profiles')}",
        "",
        "## Allowlisted platform roots",
    ]
    for root in report.get("allowlisted_platform_roots") or []:
        lines.append(f"- `{root}` (protected — not an error when active outside master)")
    lines.extend(
        [
            "",
            "## Runtime-only disable candidates (disable-only, never DELETE)",
        ]
    )
    candidates = report.get("runtime_only_disable_candidates") or []
    if candidates:
        for item in candidates:
            lines.append(f"- `{item.get('actor_id')}` — {item.get('display_name')}")
    else:
        lines.append("- none")
    lines.extend(["", "## Profiles affected by owner disable"])
    affected = report.get("profiles_affected_by_owner_disable") or []
    if affected:
        for item in affected:
            lines.append(f"- `{item.get('ref_code')}` (owner `{item.get('owner_id')}`)")
    else:
        lines.append("- none")
    review = report.get("master_review_required") or []
    if review:
        lines.extend(["", "## Master rows requiring owner review"])
        for item in review:
            lines.append(f"- `{item.get('partner_id')}`: {item.get('issue')}")
    if report.get("abort_reason"):
        lines.extend(["", f"**Abort reason:** {report.get('abort_reason')}"])
    lines.append("")
    return "\n".join(lines)


def resolve_dsn_from_env(env_name: str) -> str:
    import os

    value = os.environ.get(env_name, "").strip()
    if not value:
        raise RuntimeReadError(f"environment variable {env_name!r} is not set")
    return value


def validate_apply_conflict(*, apply: bool, runtime_dsn_env: str | None) -> None:
    if apply and runtime_dsn_env:
        raise ConfigConflictError(
            "--apply cannot be used with --runtime-dsn-env; runtime mode is read-only dry-run only"
        )
