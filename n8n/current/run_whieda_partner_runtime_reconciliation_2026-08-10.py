#!/usr/bin/env python3
"""Partner runtime reconciliation CLI (dry-run by default)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CURRENT = Path(__file__).resolve().parent
sys.path.insert(0, str(CURRENT))

from whieda_partner_runtime_readonly_runtime import (  # noqa: E402
    ConfigConflictError,
    RuntimeReadError,
    build_operator_markdown,
    build_v2_parity_report,
    detect_master_review_rows,
    load_runtime_from_readonly_dsn,
    redact_sensitive_text,
    resolve_dsn_from_env,
    validate_apply_conflict,
)
from whieda_partner_runtime_reconciliation_lib import (  # noqa: E402
    DEFAULT_ALLOWLIST_PATH,
    MasterSourceError,
    RuntimeActor,
    RuntimeProfile,
    RuntimeState,
    build_reconciliation_plan,
    load_allowlist_config,
    parse_partners_ref_tsv,
    validate_master_rows,
)

DOCKER_CONTAINER = "whieda-local-staging-postgres"
DOCKER_DB = "whieda_platform_local_core"
DOCKER_USER = "postgres"
DOCKER_PASSWORD = "local_staging_proof"


def load_runtime_from_docker(tenant_id: str) -> RuntimeState:
    def psql(sql: str) -> str:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "-e",
                f"PGPASSWORD={DOCKER_PASSWORD}",
                DOCKER_CONTAINER,
                "psql",
                "-U",
                DOCKER_USER,
                "-d",
                DOCKER_DB,
                "-v",
                "ON_ERROR_STOP=1",
                "-tAc",
                sql,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or "psql failed")
        return (result.stdout or "").strip()

    actors: dict[str, RuntimeActor] = {}
    for line in psql(
        f"""
        SELECT actor_id, display_name, active::text, coalesce(telegram_username, '')
        FROM lead_actors
        WHERE tenant_id = '{tenant_id.replace("'", "''")}'
        ORDER BY actor_id;
        """.strip()
    ).splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        actor_id, display_name, active_text = parts[0], parts[1], parts[2]
        telegram_username = parts[3] if len(parts) > 3 else None
        actors[actor_id] = RuntimeActor(
            actor_id=actor_id,
            display_name=display_name,
            active=active_text.lower() == "t",
            telegram_username=telegram_username or None,
        )

    profiles: list[RuntimeProfile] = []
    for line in psql(
        f"""
        SELECT ref_code, owner_id, enabled::text, display_mode
        FROM referral_profiles
        WHERE tenant_id = '{tenant_id.replace("'", "''")}'
        ORDER BY ref_code;
        """.strip()
    ).splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        profiles.append(
            RuntimeProfile(
                ref_code=parts[0],
                owner_id=parts[1],
                enabled=parts[2].lower() == "t",
                display_mode=parts[3] if len(parts) > 3 else "named",
            )
        )

    return RuntimeState(actors=actors, profiles=profiles)


def apply_sql_docker(sql: str) -> None:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-i",
            "-e",
            f"PGPASSWORD={DOCKER_PASSWORD}",
            DOCKER_CONTAINER,
            "psql",
            "-U",
            DOCKER_USER,
            "-d",
            DOCKER_DB,
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=sql,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "apply failed")


def docker_available() -> bool:
    try:
        subprocess.run(["docker", "info"], check=True, capture_output=True)
        subprocess.run(["docker", "inspect", DOCKER_CONTAINER], check=True, capture_output=True)
        return True
    except Exception:
        return False


def _abort_payload(reason: str) -> dict:
    return {"ok": False, "summary": "abort", "abort_reason": redact_sensitive_text(reason)}


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA partner runtime reconciliation")
    parser.add_argument(
        "--master-tsv",
        required=True,
        help="Local Partners_Ref TSV path (snapshot export, not live Sheets)",
    )
    parser.add_argument(
        "--allowlist",
        default=str(DEFAULT_ALLOWLIST_PATH),
        help="platform_root_allowlist JSON path",
    )
    parser.add_argument(
        "--tenant",
        default="whieda",
        help="Tenant id for runtime read (default: whieda)",
    )
    parser.add_argument(
        "--runtime-json",
        help="Optional runtime snapshot JSON instead of docker postgres",
    )
    parser.add_argument(
        "--runtime-dsn-env",
        help="Environment variable name holding read-only external runtime DSN",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply SQL on local staging docker only (never with --runtime-dsn-env)",
    )
    parser.add_argument(
        "--report-out",
        help="Write JSON review report to this path",
    )
    parser.add_argument(
        "--markdown-out",
        help="Write operator Markdown report to this path (runtime read-only mode)",
    )
    args = parser.parse_args()

    try:
        validate_apply_conflict(apply=args.apply, runtime_dsn_env=args.runtime_dsn_env)
    except ConfigConflictError as exc:
        print(json.dumps(_abort_payload(str(exc)), ensure_ascii=False, indent=2))
        return 1

    try:
        master_rows = parse_partners_ref_tsv(Path(args.master_tsv))
        validated_master = validate_master_rows(master_rows)
        allowlist = load_allowlist_config(Path(args.allowlist))
        if args.tenant != allowlist.tenant_id:
            raise MasterSourceError(
                f"tenant {args.tenant!r} does not match allowlist tenant {allowlist.tenant_id!r}"
            )
        if not allowlist.platform_root_allowlist:
            raise MasterSourceError("allowlist has no platform_root_allowlist entries")
    except (MasterSourceError, ValueError, OSError) as exc:
        print(json.dumps(_abort_payload(str(exc)), ensure_ascii=False, indent=2))
        return 1

    master_review_rows = detect_master_review_rows(validated_master)
    runtime_source = "fixture"
    runtime: RuntimeState | None = None
    abort_reason: str | None = None

    try:
        if args.runtime_dsn_env:
            runtime_source = f"env:{args.runtime_dsn_env}"
            dsn = resolve_dsn_from_env(args.runtime_dsn_env)
            runtime = load_runtime_from_readonly_dsn(dsn, args.tenant)
        elif args.runtime_json:
            runtime_source = "runtime_json"
            payload = json.loads(Path(args.runtime_json).read_text(encoding="utf-8"))
            runtime = RuntimeState(
                actors={
                    item["actor_id"]: RuntimeActor(
                        actor_id=item["actor_id"],
                        display_name=item.get("display_name") or item["actor_id"],
                        active=bool(item.get("active", True)),
                    )
                    for item in payload.get("actors") or []
                },
                profiles=[
                    RuntimeProfile(
                        ref_code=item["ref_code"],
                        owner_id=item["owner_id"],
                        enabled=bool(item.get("enabled", True)),
                        display_mode=item.get("display_mode") or "named",
                    )
                    for item in payload.get("profiles") or []
                ],
            )
        elif docker_available() and not args.runtime_dsn_env:
            runtime_source = "docker_local_staging"
            runtime = load_runtime_from_docker(allowlist.tenant_id)
        else:
            abort_reason = "no runtime source: set --runtime-dsn-env, --runtime-json, or start staging docker"
    except (RuntimeReadError, MasterSourceError, json.JSONDecodeError, OSError) as exc:
        abort_reason = str(exc)

    if abort_reason or runtime is None:
        report = build_v2_parity_report(
            build_reconciliation_plan(validated_master, RuntimeState(), allowlist),
            master_rows=validated_master,
            allowlist=allowlist,
            master_review_rows=master_review_rows,
            runtime_source=runtime_source,
            runtime_profile_count=0,
            abort_reason=abort_reason or "runtime unavailable",
        )
        output = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report_out:
            Path(args.report_out).write_text(output + "\n", encoding="utf-8")
        if args.markdown_out:
            Path(args.markdown_out).write_text(build_operator_markdown(report) + "\n", encoding="utf-8")
        print(output)
        return 1

    assert runtime is not None
    mode = "runtime_readonly_dry_run" if args.runtime_dsn_env else ("apply" if args.apply else "dry_run")
    plan = build_reconciliation_plan(validated_master, runtime, allowlist, mode=mode)

    if args.runtime_dsn_env:
        active_profiles = sum(1 for profile in runtime.profiles if profile.enabled)
        report = build_v2_parity_report(
            plan,
            master_rows=validated_master,
            allowlist=allowlist,
            master_review_rows=master_review_rows,
            runtime_source=runtime_source,
            runtime_profile_count=active_profiles,
        )
        report["network"] = "readonly_external_dsn"
        report["note"] = "Read-only parity report. No mutations performed."
    else:
        report = plan.to_report_dict()
        report["ok"] = True
        report["summary"] = (
            "review_required"
            if plan.proposed_actor_deactivations or plan.proposed_profile_deactivations
            else "safe"
        )
        report["network"] = "not_used" if mode == "dry_run" else "docker_local_staging_only"
        if args.apply:
            if not plan.apply_sql.strip() or plan.apply_sql.strip() == "BEGIN;\nCOMMIT;":
                report["applied"] = False
                report["note"] = "nothing to apply"
            else:
                apply_sql_docker(plan.apply_sql)
                report["applied"] = True
        else:
            report["note"] = "Dry-run review report. Re-run with --apply to mutate staging only."

    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report_out:
        Path(args.report_out).write_text(output + "\n", encoding="utf-8")
    if args.markdown_out:
        Path(args.markdown_out).write_text(build_operator_markdown(report) + "\n", encoding="utf-8")
    print(output)
    return 0 if report.get("ok", True) and report.get("summary") != "abort" else 1


if __name__ == "__main__":
    raise SystemExit(main())
