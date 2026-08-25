#!/usr/bin/env python3
"""Read-only Telegram delivery outbox inspector. Never resend, apply, or publish."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_API = _ROOT / "backend" / "platform-api"
if str(_API) not in sys.path:
    sys.path.insert(0, str(_API))

from app.telegram.outbox_inspect import inspect_snapshot  # noqa: E402


def _print_json(payload: dict | str) -> None:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)


def _load_rows(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        rows = data.get("rows") or data.get("deliveries") or data.get("items")
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]
    raise ValueError("offline snapshot must list outbox rows")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect Telegram delivery outbox (read-only)")
    parser.add_argument("--offline-snapshot", type=Path, dest="offline_snapshot")
    parser.add_argument("--report-out", type=Path, dest="report_out")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--retry-all", action="store_true", dest="retry_all")
    parser.add_argument("--dsn", default=None, help="Never opened.")
    args = parser.parse_args(argv)

    if args.apply or args.publish or args.retry_all:
        _print_json(
            {
                "ok": False,
                "code": "action_refused",
                "message": "--apply, --publish and --retry-all are not implemented in Gate K",
            }
        )
        return 1

    if args.dsn:
        _print_json(
            {
                "ok": False,
                "code": "action_refused",
                "message": "live DSN inspect is not implemented; use --offline-snapshot",
            }
        )
        return 1

    if args.offline_snapshot is None:
        _print_json(
            {
                "ok": False,
                "code": "invocation_error",
                "message": "missing required argument: --offline-snapshot",
            }
        )
        return 1

    if not args.offline_snapshot.is_file():
        _print_json({"ok": False, "code": "invocation_error", "message": "offline snapshot not found"})
        return 1

    try:
        rows = _load_rows(args.offline_snapshot)
    except (OSError, ValueError, json.JSONDecodeError):
        _print_json(
            {
                "ok": False,
                "code": "invocation_error",
                "message": "offline snapshot is not a valid outbox dump",
            }
        )
        return 1

    report = inspect_snapshot(rows)
    _print_json(report)
    if args.report_out is not None:
        args.report_out.parent.mkdir(parents=True, exist_ok=True)
        args.report_out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
