"""CLI for controlled shared-staging tenant canary."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from shared_staging_canary.rollback import render_rollback_plan
from shared_staging_canary.target import TargetGuardError, validate_canary_target
from tenant_release.package import load_package


def _print(payload: dict[str, Any], *, code: int) -> int:
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or not str(value).strip():
        return None
    return str(value)


def _package_tenant(package: Path | None, explicit: str | None) -> str:
    if explicit:
        return explicit.strip()
    if package and (package / "manifest.json").is_file():
        loaded = load_package(package)
        return str(loaded.manifest.get("tenant_id") or "").strip()
    return ""


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser(description="Controlled shared-staging tenant canary")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--rollback-plan", action="store_true", dest="rollback_plan")
    parser.add_argument("--local-proof", action="store_true", dest="local_proof")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--package", type=Path)
    parser.add_argument("--tenant", default=None)
    parser.add_argument("--confirm", default=None)
    parser.add_argument("--media-manifest", type=Path, dest="media_manifest")
    parser.add_argument("--binding-snapshot", type=Path, dest="binding_snapshot")
    parser.add_argument("--runtime-snapshot", type=Path, dest="runtime_snapshot")
    args = parser.parse_args(argv)

    if args.publish:
        return _print({"ok": False, "code": "action_refused", "message": "--publish is refused"}, code=1)

    modes = [
        name
        for name, flag in (
            ("preflight", args.preflight),
            ("apply", args.apply),
            ("verify", args.verify),
            ("rollback-plan", args.rollback_plan),
            ("local-proof", args.local_proof),
        )
        if flag
    ]
    if len(modes) != 1:
        return _print(
            {
                "ok": False,
                "code": "mode_required",
                "message": "pass exactly one of --preflight, --apply, --verify, --rollback-plan, --local-proof",
            },
            code=1,
        )

    if args.apply and args.confirm != "APPLY_SHARED_STAGING":
        return _print(
            {
                "ok": False,
                "code": "confirm_required",
                "message": "--apply requires --confirm APPLY_SHARED_STAGING",
            },
            code=1,
        )

    if args.rollback_plan:
        tenant_id = _package_tenant(args.package, args.tenant) or "tenant"
        package_id = tenant_id
        if args.package and (args.package / "manifest.json").is_file():
            package_id = str(load_package(args.package).manifest.get("package_id") or package_id)
        expected_db = _env("WHIEDA_SHARED_STAGING_EXPECTED_DB") or "<WHIEDA_SHARED_STAGING_EXPECTED_DB>"
        print(
            render_rollback_plan(
                database=expected_db,
                tenant_id=tenant_id,
                package_id=package_id,
            ),
            end="",
        )
        return 0

    if args.local_proof:
        from shared_staging_canary.local_proof import run_local_proof

        return run_local_proof(package=args.package)

    dsn = _env("WHIEDA_SHARED_STAGING_DSN")
    expected_db = _env("WHIEDA_SHARED_STAGING_EXPECTED_DB")
    runtime_dsn = _env("WHIEDA_RUNTIME_READONLY_DSN")
    media_base = _env("PLATFORM_TENANT_MEDIA_BASE_URL")
    try:
        target = validate_canary_target(
            dsn,
            expected_db=expected_db,
            runtime_readonly_dsn=runtime_dsn,
        )
    except TargetGuardError as exc:
        return _print(
            {
                "ok": False,
                "state": "blocked",
                "code": "target_refused",
                "message": str(exc),
                "mode": modes[0],
            },
            code=1,
        )

    if args.preflight:
        from shared_staging_canary.preflight import run_preflight

        if args.package is None:
            return _print({"ok": False, "state": "blocked", "code": "package_required", "message": "--package required"}, code=1)
        report = run_preflight(
            target=target,
            dsn=dsn or "",
            package=args.package,
            tenant_id=args.tenant,
            media_base_url=media_base,
            media_manifest=args.media_manifest,
            binding_snapshot=args.binding_snapshot,
            runtime_snapshot=args.runtime_snapshot,
        )
        return _print(report, code=0 if report.get("ok") else 2)

    if args.apply:
        from shared_staging_canary.apply import run_apply

        if args.package is None:
            return _print({"ok": False, "state": "blocked", "code": "package_required", "message": "--package required"}, code=1)
        report = run_apply(
            target=target,
            dsn=dsn or "",
            package=args.package,
            tenant_id=args.tenant,
            media_base_url=media_base,
            media_manifest=args.media_manifest,
            binding_snapshot=args.binding_snapshot,
            runtime_snapshot=args.runtime_snapshot,
        )
        return _print(report, code=0 if report.get("ok") else 2)

    from shared_staging_canary.verify import run_verify

    if args.package is None:
        return _print({"ok": False, "state": "blocked", "code": "package_required", "message": "--package required"}, code=1)
    report = run_verify(
        target=target,
        dsn=dsn or "",
        package=args.package,
        tenant_id=args.tenant,
        media_base_url=media_base,
        media_manifest=args.media_manifest,
        binding_snapshot=args.binding_snapshot,
        runtime_snapshot=args.runtime_snapshot,
    )
    return _print(report, code=0 if report.get("ok") else 2)


if __name__ == "__main__":
    raise SystemExit(main())
