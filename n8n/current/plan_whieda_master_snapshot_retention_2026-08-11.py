#!/usr/bin/env python3
"""Dry-run retention planner for WHIEDA structured master snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "n8n" / "current"))

from whieda_master_integrity_lib import (  # noqa: E402
    format_retention_markdown,
    redact_sensitive_text,
    retention_plan_v2,
)

DEFAULT_SNAPSHOT_ROOT = ROOT / "n8n" / "live-exports" / "structured-master"
DEFAULT_INTEGRITY_ROOT = ROOT / "n8n" / "live-exports" / "master-runtime-integrity"
DEFAULT_OUT_ROOT = ROOT / "n8n" / "live-exports" / "master-snapshot-retention"


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA master snapshot retention planner (dry-run only)")
    parser.add_argument("--snapshot-root", type=Path, default=DEFAULT_SNAPSHOT_ROOT)
    parser.add_argument("--integrity-root", type=Path, default=DEFAULT_INTEGRITY_ROOT)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--markdown-out", type=Path, default=None)
    args = parser.parse_args()

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = DEFAULT_OUT_ROOT / run_id
    json_out = args.json_out or (out_dir / "plan.json")
    markdown_out = args.markdown_out or (out_dir / "plan.md")

    try:
        plan = retention_plan_v2(
            args.snapshot_root,
            integrity_root=args.integrity_root,
        )
    except Exception as exc:
        payload = {"error": redact_sensitive_text(str(exc)), "status": "plan_failed"}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    plan["run_id"] = run_id
    plan["dry_run_only"] = True
    plan["apply_supported"] = False

    json_out.parent.mkdir(parents=True, exist_ok=True)
    markdown_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(plan, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    markdown_out.write_text(format_retention_markdown(plan), encoding="utf-8")

    summary = {
        "run_id": run_id,
        "total_snapshots": plan["total_snapshots"],
        "keep_count": plan["keep_count"],
        "removable_candidate_count": plan["removable_candidate_count"],
        "removable_bytes": plan["removable_bytes"],
        "json_out": str(json_out),
        "markdown_out": str(markdown_out),
        "dry_run_only": True,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
