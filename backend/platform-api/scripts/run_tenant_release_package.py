#!/usr/bin/env python3
"""Tenant release package firewall. Validate/stage/candidate only; publish always refused."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from tenant_release.package import seal_package, validate_package  # noqa: E402
from tenant_release.postgres_store import PostgresStagingStore  # noqa: E402
from tenant_release.store import (  # noqa: E402
    StageGuardError,
    build_release_candidate,
    refuse_publish,
    stage_package,
    validate_stage_dsn,
)


def _print(payload: dict | str, *, ok: bool) -> int:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    return 0 if ok else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generic tenant release package firewall")
    parser.add_argument("--package", type=Path, help="Directory with manifest.json")
    parser.add_argument("--validate", action="store_true", help="Offline validate. No DB.")
    parser.add_argument("--stage", action="store_true", help="Write staging rows on local verify DB only.")
    parser.add_argument("--build-release-candidate", action="store_true")
    parser.add_argument("--publish", action="store_true", help="Always refused in Gate E.")
    parser.add_argument("--seal", action="store_true", help="Fill manifest file hashes (offline).")
    parser.add_argument("--dsn", default=None, help="Only local verify DSN is accepted for --stage.")
    parser.add_argument(
        "--store",
        choices=("memory", "postgres"),
        default=None,
        help="postgres is the --stage default and requires a local verify DSN.",
    )
    args = parser.parse_args(argv)

    if args.publish:
        return _print(refuse_publish(), ok=False)

    if args.seal:
        if args.package is None:
            return _print({"ok": False, "errors": [{"code": "package_required", "message": "--package required"}]}, ok=False)
        manifest = seal_package(args.package)
        return _print({"ok": True, "mode": "seal", "package_id": manifest.get("package_id")}, ok=True)

    if args.validate:
        if args.package is None:
            return _print({"ok": False, "errors": [{"code": "package_required", "message": "--package required"}]}, ok=False)
        report = validate_package(args.package)
        return _print(report.to_json(), ok=report.ok)

    if args.stage or args.build_release_candidate:
        if args.package is None:
            return _print({"ok": False, "errors": [{"code": "package_required", "message": "--package required"}]}, ok=False)
        store = None
        store_kind = args.store or "postgres"
        if store_kind == "postgres":
            try:
                validate_stage_dsn(args.dsn or "")
            except StageGuardError as exc:
                return _print({"ok": False, "mode": "stage", "errors": [{"code": "stage_dsn_refused", "message": str(exc)}]}, ok=False)
            store = PostgresStagingStore(args.dsn or "")
        if args.stage and not args.build_release_candidate:
            result = stage_package(args.package, store=store)
            return _print(result.to_json(), ok=result.ok)
        result = build_release_candidate(args.package, store=store)
        return _print(result.to_json(), ok=result.ok)

    return _print(
        {
            "ok": False,
            "errors": [
                {
                    "code": "action_required",
                    "message": "pass --validate, --stage, --build-release-candidate, or --publish",
                }
            ],
        },
        ok=False,
    )


if __name__ == "__main__":
    raise SystemExit(main())
