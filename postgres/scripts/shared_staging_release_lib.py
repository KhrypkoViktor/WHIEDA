"""Shared-staging release harness — plan/preflight only, never apply."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse

from staging_proof_lib import APPLY_ORDER, SQL_DIR

SCRIPTS_DIR = Path(__file__).resolve().parent
ROOT = SCRIPTS_DIR.parents[1]
BINDING_CONTEXT_SQL = "platform_bot_binding_context_v1.sql"
BINDING_CONTEXT_PATH = SQL_DIR / BINDING_CONTEXT_SQL
BACKFILL_PLAN = SCRIPTS_DIR / "platform_bot_binding_context_backfill_plan_v1.sql"

# Pinned in Gate B1 manifest. A dirty tree that rewrites the file must fail.
EXPECTED_BINDING_CONTEXT_SHA256 = (
    "f9ab0143d754a9a4cee9817958d084f88665b8548cdbde3f36668a503a6175a5"
)

WHIEDA_BINDING_ID = "whieda-advisor-bot"
NSP_TENANT_ID = "nsp-maxim"
UNKNOWN_BINDING_ID = "unknown-binding-does-not-exist"

SHARED_STAGING_DB_ALLOWLIST = frozenset({"whieda_platform_staging"})
SHARED_STAGING_ROLE_ALLOWLIST = frozenset(
    {"whieda_platform_staging", "whieda_platform_api", "whieda_staging"}
)

FORBIDDEN_SHARED_HOST_FRAGMENTS = (
    "supabase",
    "amazonaws.com",
    "azure",
    "neon.tech",
    "render.com",
    "185.252.232.93",
    "wwc.best",
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "[::1]",
)

LOCAL_VERIFY_DB_RE = re.compile(
    r"^whieda_platform_staging_verify_[a-z0-9][a-z0-9_]*$", re.I
)

MIGRATION_MARKERS: tuple[tuple[str, str], ...] = (
    ("platform_tenant_registry_v1.sql", "to_regclass('public.tenants')"),
    ("platform_tenant_rls_v1.sql", "to_regclass('public.tenants')"),
    ("whieda_website_leads_p0_v1.sql", "to_regclass('public.website_leads')"),
    ("wwc_leads_p01_runtime_migration.sql", "to_regclass('public.referral_profiles')"),
    ("platform_tenant_rls_legacy_leads_v1.sql", "to_regclass('public.website_leads')"),
    ("platform_api_session_context_v1.sql", "to_regclass('public.platform_session_context')"),
    ("platform_identity_journey_v1.sql", "to_regclass('public.visitor_sessions')"),
    ("platform_onboarding_v1.sql", "to_regclass('public.onboarding_programs')"),
    ("platform_user_memory_v1.sql", "to_regclass('public.user_memory_facts')"),
    ("platform_pilot_telemetry_v1.sql", "to_regclass('public.pilot_daily_metrics')"),
    ("platform_retention_export_v1.sql", "to_regclass('public.data_retention_registry')"),
    (
        "platform_whieda_telegram_binding_v1.sql",
        "to_regclass('public.tenant_bot_bindings')",
    ),
    (
        BINDING_CONTEXT_SQL,
        "(SELECT count(*) FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='tenant_bot_bindings' "
        "AND column_name='bot_token_ref')",
    ),
)


class ReleaseGuardError(ValueError):
    """Target or DSN is not a permitted shared-staging release target."""


@dataclass(frozen=True)
class ParsedTarget:
    host: str
    port: int
    database: str
    user: str
    redacted_dsn: str


@dataclass
class BindingRow:
    binding_id: str
    tenant_id: str
    status: str
    processing_mode: str | None = None
    bot_username: str | None = None


@dataclass
class ReleaseReport:
    mode: str
    ok: bool
    would_apply: list[str] = field(default_factory=list)
    preconditions_failed: list[str] = field(default_factory=list)
    bindings: list[dict] = field(default_factory=list)
    tenants_affected: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sha256_binding_context: str = ""
    target: dict | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return sha256_bytes(data)


def binding_context_sha256() -> str:
    return sha256_path(BINDING_CONTEXT_PATH)


def redact_dsn(dsn: str) -> str:
    parsed = urlparse(dsn.strip())
    user = unquote(parsed.username or "") or "user"
    host = parsed.hostname or "host"
    port = parsed.port or 5432
    database = (parsed.path or "/").lstrip("/") or "db"
    return f"postgresql://{user}:***@{host}:{port}/{database}"


def parse_dsn(dsn: str | None) -> ParsedTarget:
    if dsn is None or not str(dsn).strip():
        raise ReleaseGuardError("missing DSN")
    parsed = urlparse(str(dsn).strip())
    if parsed.scheme not in {"postgres", "postgresql"}:
        raise ReleaseGuardError(
            f"unsupported DSN scheme {parsed.scheme!r}; expected postgresql://"
        )
    host = (parsed.hostname or "").strip().lower()
    database = (parsed.path or "").lstrip("/").strip()
    user = unquote(parsed.username or "").strip()
    port = parsed.port or 5432
    if not host:
        raise ReleaseGuardError("DSN host is empty")
    if not database:
        raise ReleaseGuardError("DSN database name is empty")
    if not user:
        raise ReleaseGuardError("DSN user/role is empty")
    return ParsedTarget(
        host=host,
        port=int(port),
        database=database,
        user=user,
        redacted_dsn=redact_dsn(str(dsn)),
    )


def validate_shared_staging_target(dsn: str | None) -> ParsedTarget:
    target = parse_dsn(dsn)
    for fragment in FORBIDDEN_SHARED_HOST_FRAGMENTS:
        if fragment in target.host:
            raise ReleaseGuardError(
                f"refusing host {target.host!r} for shared staging "
                f"(matches {fragment!r})"
            )
    if LOCAL_VERIFY_DB_RE.match(target.database):
        raise ReleaseGuardError(
            f"refusing local verify database {target.database!r} "
            "as a shared-staging target (localhost substitution)"
        )
    if target.database not in SHARED_STAGING_DB_ALLOWLIST:
        raise ReleaseGuardError(
            f"refusing foreign database {target.database!r}; "
            f"shared staging allowlist={sorted(SHARED_STAGING_DB_ALLOWLIST)}"
        )
    if target.user.lower() in {"postgres", "supabase_admin", "rds_superuser"}:
        raise ReleaseGuardError(
            f"refusing role {target.user!r}; shared staging forbids superuser aliases"
        )
    return target


def verify_binding_context_hash(*, expected: str | None = None) -> str:
    actual = binding_context_sha256()
    pin = expected or EXPECTED_BINDING_CONTEXT_SHA256
    if actual != pin:
        raise ReleaseGuardError(
            f"stale binding-context hash: got {actual}, expected {pin}"
        )
    return actual


def local_migration_files() -> list[dict]:
    rows = []
    for name in APPLY_ORDER:
        path = SQL_DIR / name
        rows.append(
            {
                "name": name,
                "exists": path.is_file(),
                "sha256": sha256_path(path) if path.is_file() else None,
            }
        )
    return rows


def build_offline_plan() -> ReleaseReport:
    failed: list[str] = []
    notes = [
        "mode=plan: no network, no writes",
        "apply is disabled in this slice",
        "backfill plan is SQL-only and not in APPLY_ORDER",
    ]
    try:
        sha = verify_binding_context_hash()
    except ReleaseGuardError as exc:
        sha = binding_context_sha256() if BINDING_CONTEXT_PATH.is_file() else ""
        failed.append(str(exc))
    files = local_migration_files()
    missing = [item["name"] for item in files if not item["exists"]]
    if missing:
        failed.append(f"missing SQL files: {missing}")
    if len(APPLY_ORDER) != 13:
        failed.append(f"APPLY_ORDER length {len(APPLY_ORDER)} != 13")
    if APPLY_ORDER[-1] != BINDING_CONTEXT_SQL:
        failed.append("binding context SQL is not last in APPLY_ORDER")
    if BACKFILL_PLAN.name in APPLY_ORDER:
        failed.append("backfill plan must not be in APPLY_ORDER")
    would = [item["name"] for item in files if item["exists"]]
    notes.append(
        "would apply (idempotent, owner-run later): " + ", ".join(would)
    )
    notes.append(f"would not apply: {BACKFILL_PLAN.name}")
    return ReleaseReport(
        mode="plan",
        ok=not failed,
        would_apply=would,
        preconditions_failed=failed,
        bindings=[],
        tenants_affected=["whieda"],
        notes=notes,
        sha256_binding_context=sha,
    )


def evaluate_preflight(
    target: ParsedTarget,
    *,
    current_database: str,
    current_user: str,
    is_superuser: bool,
    tables_present: dict[str, bool],
    binding_columns: list[str],
    bindings: list[BindingRow],
    tenants: list[dict],
) -> ReleaseReport:
    failed: list[str] = []
    notes = [
        "mode=preflight: SELECT only",
        f"target={target.redacted_dsn}",
        f"session_user={current_user} superuser={is_superuser}",
    ]
    try:
        sha = verify_binding_context_hash()
    except ReleaseGuardError as exc:
        sha = binding_context_sha256() if BINDING_CONTEXT_PATH.is_file() else ""
        failed.append(str(exc))
    if current_database != target.database:
        failed.append(
            f"connected database {current_database!r} != DSN database {target.database!r}"
        )
    if current_user != target.user:
        failed.append(
            f"connected role {current_user!r} != DSN role {target.user!r}"
        )
    if is_superuser:
        failed.append("connected role is superuser; shared staging forbids that")
    if target.user not in SHARED_STAGING_ROLE_ALLOWLIST:
        failed.append(
            f"role {target.user!r} is not in shared-staging allowlist "
            f"{sorted(SHARED_STAGING_ROLE_ALLOWLIST)}"
        )
    required_tables = [
        "tenants",
        "tenant_bot_bindings",
        "website_leads",
        "referral_profiles",
        "platform_session_context",
        "visitor_sessions",
        "identity_link_tokens",
        "onboarding_programs",
        "user_memory_facts",
        "pilot_daily_metrics",
        "data_retention_registry",
    ]
    missing_tables = [name for name in required_tables if not tables_present.get(name)]
    if missing_tables:
        failed.append(f"migrations incomplete; missing tables: {missing_tables}")
    required_cols = {
        "binding_id",
        "tenant_id",
        "status",
        "webhook_secret_ref",
        "bot_token_ref",
        "bot_username",
        "processing_mode",
    }
    missing_cols = sorted(required_cols - set(binding_columns))
    if missing_cols:
        failed.append(f"tenant_bot_bindings missing columns: {missing_cols}")

    binding_dicts = [
        {
            "binding_id": row.binding_id,
            "tenant_id": row.tenant_id,
            "status": row.status,
            "processing_mode": row.processing_mode,
            "bot_username": row.bot_username,
        }
        for row in bindings
    ]
    tenant_ids = sorted({item["tenant_id"] for item in tenants} | {row.tenant_id for row in bindings})
    return ReleaseReport(
        mode="preflight",
        ok=not failed,
        would_apply=list(APPLY_ORDER),
        preconditions_failed=failed,
        bindings=binding_dicts,
        tenants_affected=tenant_ids,
        notes=notes,
        sha256_binding_context=sha,
        target={"host": target.host, "database": target.database, "user": target.user},
    )


def evaluate_postcheck(bindings: list[BindingRow]) -> ReleaseReport:
    failed: list[str] = []
    notes = ["mode=postcheck: SELECT only, no writes"]
    by_id = {row.binding_id: row for row in bindings}

    whieda = by_id.get(WHIEDA_BINDING_ID)
    if whieda is None:
        failed.append(f"{WHIEDA_BINDING_ID} is missing")
    else:
        if whieda.tenant_id != "whieda":
            failed.append(
                f"{WHIEDA_BINDING_ID} tenant_id={whieda.tenant_id!r}, expected 'whieda'"
            )
        if whieda.status != "active":
            failed.append(f"{WHIEDA_BINDING_ID} status={whieda.status!r}, expected active")
        notes.append(
            f"{WHIEDA_BINDING_ID}: tenant={whieda.tenant_id} status={whieda.status}"
        )

    nsp_rows = [row for row in bindings if row.tenant_id == NSP_TENANT_ID or row.binding_id.startswith("nsp-")]
    if not nsp_rows:
        notes.append("NSP binding absent")
    else:
        for row in nsp_rows:
            if row.tenant_id != NSP_TENANT_ID:
                failed.append(
                    f"NSP identity {row.binding_id} attached to {row.tenant_id!r}"
                )
            if row.status != "disabled":
                failed.append(
                    f"NSP binding {row.binding_id} status={row.status!r}, expected disabled"
                )
            notes.append(
                f"{row.binding_id}: tenant={row.tenant_id} status={row.status}"
            )

    if UNKNOWN_BINDING_ID in by_id:
        failed.append("unknown binding resolved to a row")
    else:
        notes.append(f"{UNKNOWN_BINDING_ID}: not found")

    hijack = [
        row
        for row in bindings
        if row.tenant_id == "whieda"
        and (row.binding_id.startswith("nsp-") or row.tenant_id == NSP_TENANT_ID)
    ]
    if hijack:
        failed.append("NSP identity fell into WHIEDA tenant")

    return ReleaseReport(
        mode="postcheck",
        ok=not failed,
        would_apply=[],
        preconditions_failed=failed,
        bindings=[
            {
                "binding_id": row.binding_id,
                "tenant_id": row.tenant_id,
                "status": row.status,
                "processing_mode": row.processing_mode,
                "bot_username": row.bot_username,
            }
            for row in bindings
        ],
        tenants_affected=sorted({row.tenant_id for row in bindings}),
        notes=notes,
        sha256_binding_context=binding_context_sha256()
        if BINDING_CONTEXT_PATH.is_file()
        else "",
    )


def refuse_apply() -> ReleaseReport:
    return ReleaseReport(
        mode="apply",
        ok=False,
        would_apply=[],
        preconditions_failed=[
            "apply is not enabled in Core Gate B2; shared staging writes are forbidden"
        ],
        notes=["use --plan or --preflight only"],
        sha256_binding_context=binding_context_sha256()
        if BINDING_CONTEXT_PATH.is_file()
        else "",
    )
