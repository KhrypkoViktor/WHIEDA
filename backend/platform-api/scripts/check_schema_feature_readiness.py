#!/usr/bin/env python3
"""Offline schema-feature readiness. Never apply, publish, or open a live DSN."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_API = _ROOT / "backend" / "platform-api"
if str(_API) not in sys.path:
    sys.path.insert(0, str(_API))

from app.db_feature_readiness import (  # noqa: E402
    evaluate_from_relations,
    format_status_lines,
)


def _print_json(payload: dict | str) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)


def _load_relations(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [str(item) for item in data]
    if isinstance(data, dict):
        for key in ("relations", "existing_relations", "tables"):
            if key in data and isinstance(data[key], list):
                return [str(item) for item in data[key]]
    raise ValueError("offline manifest must list relation names")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Schema-feature readiness (offline manifest)")
    parser.add_argument("--offline-manifest", type=Path, dest="offline_manifest")
    parser.add_argument("--report-out", type=Path, dest="report_out")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--dsn", default=None, help="Never opened. Document-only; live probe is not implemented.")
    args = parser.parse_args(argv)

    if args.apply or args.publish:
        _print_json(
            {
                "ok": False,
                "code": "action_refused",
                "message": "--apply and --publish are not implemented in Gate J",
            }
        )
        return 1

    if args.dsn:
        _print_json(
            {
                "ok": False,
                "code": "action_refused",
                "message": "live DSN probe is not implemented; use --offline-manifest",
            }
        )
        return 1

    if args.offline_manifest is None:
        _print_json(
            {
                "ok": False,
                "code": "invocation_error",
                "message": "missing required argument: --offline-manifest",
            }
        )
        return 1

    if not args.offline_manifest.is_file():
        _print_json(
            {
                "ok": False,
                "code": "invocation_error",
                "message": "offline manifest not found",
            }
        )
        return 1

    try:
        relations = _load_relations(args.offline_manifest)
    except (OSError, ValueError, json.JSONDecodeError):
        _print_json(
            {
                "ok": False,
                "code": "invocation_error",
                "message": "offline manifest is not a valid relation snapshot",
            }
        )
        return 1

    report = evaluate_from_relations(relations)
    payload = report.to_json()
    print(format_status_lines(report))
    if args.report_out is not None:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
