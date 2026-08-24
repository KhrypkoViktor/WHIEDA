#!/usr/bin/env python3
"""Read-only tenant canary preflight. Plan by default; never apply or publish."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from tenant_canary.preflight import (  # noqa: E402
    evaluate_preflight,
    refuse_shared_staging_readonly,
    report_to_json,
    report_to_markdown,
)


def _print_json(payload: dict | str) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generic tenant canary preflight (read-only)")
    parser.add_argument("--tenant", required=False, help="Tenant id to evaluate")
    parser.add_argument("--package", type=Path, help="Tenant release package directory")
    parser.add_argument("--media-manifest", type=Path, dest="media_manifest", help="TSV upload manifest")
    parser.add_argument("--binding-snapshot", type=Path, dest="binding_snapshot")
    parser.add_argument("--runtime-snapshot", type=Path, dest="runtime_snapshot")
    parser.add_argument("--media-base-url", dest="media_base_url")
    parser.add_argument("--report-out", type=Path, dest="report_out")
    parser.add_argument("--plan", action="store_true", default=True, help="Default: offline snapshot mode")
    parser.add_argument(
        "--shared-staging-readonly",
        action="store_true",
        help="Scaffold only. Refuses to open a live DSN.",
    )
    parser.add_argument("--dsn", default=None, help="Never opened. Document-only for the refused readonly mode.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)

    if args.apply or args.publish:
        _print_json(
            {
                "ok": False,
                "code": "action_refused",
                "message": "--apply and --publish are not implemented in Gate I",
            }
        )
        return 1

    if args.shared_staging_readonly:
        _print_json(refuse_shared_staging_readonly(dsn=args.dsn))
        return 1

    required = {
        "--tenant": args.tenant,
        "--package": args.package,
        "--media-manifest": args.media_manifest,
        "--binding-snapshot": args.binding_snapshot,
        "--runtime-snapshot": args.runtime_snapshot,
        "--media-base-url": args.media_base_url,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        _print_json({"ok": False, "code": "invocation_error", "message": f"missing required arguments: {missing}"})
        return 1

    paths = {
        "--package": args.package,
        "--media-manifest": args.media_manifest,
        "--binding-snapshot": args.binding_snapshot,
        "--runtime-snapshot": args.runtime_snapshot,
    }
    for name, path in paths.items():
        if path is None or not path.exists():
            _print_json({"ok": False, "code": "io_error", "message": f"{name} not found: {path}"})
            return 1

    try:
        report = evaluate_preflight(
            tenant_id=str(args.tenant).strip(),
            package_dir=args.package,
            media_manifest=args.media_manifest,
            binding_snapshot=args.binding_snapshot,
            runtime_snapshot=args.runtime_snapshot,
            media_base_url=str(args.media_base_url),
            mode="plan",
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        _print_json({"ok": False, "code": "io_error", "message": str(exc)})
        return 1

    payload = report_to_json(report)
    _print_json(payload)
    if args.report_out:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(payload + "\n", encoding="utf-8")
        markdown_path = args.report_out.with_suffix(".md")
        markdown_path.write_text(report_to_markdown(report), encoding="utf-8")
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
