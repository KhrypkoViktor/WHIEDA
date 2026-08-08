#!/usr/bin/env python3
"""Create or verify read-only export snapshots (metadata only, no row content)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DQC_ROOT = ROOT / "qa" / "data_quality"
DEFAULT_MANIFEST = DQC_ROOT / "source_manifest.json"
SNAPSHOTS_DIR = DQC_ROOT / "snapshots"

sys.path.insert(0, str(DQC_ROOT))

from dqc.snapshot import build_snapshot, format_verify_report, save_snapshot, verify_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="WHIEDA export snapshot (read-only metadata)")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--verify", type=Path, help="Compare current exports against snapshot JSON")
    parser.add_argument("--output", type=Path, help="Snapshot output path (default: snapshots/<timestamp>.json)")
    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if args.verify:
        snapshot_path = args.verify.resolve()
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        result = verify_snapshot(root=ROOT, manifest=manifest, snapshot=snapshot)
        print(format_verify_report(result))
        return 0 if result["status"] == "PASS" else 1

    snapshot = build_snapshot(root=ROOT, manifest_path=manifest_path, manifest=manifest)
    if args.output:
        out_path = args.output.resolve()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = SNAPSHOTS_DIR / f"export_snapshot_{stamp}.json"
    save_snapshot(snapshot, out_path)
    rel_out = out_path.relative_to(ROOT) if out_path.is_relative_to(ROOT) else out_path
    print(f"Snapshot saved: {rel_out}")
    found = sum(1 for item in snapshot["layers"].values() if item["status"] == "found")
    missing = sum(1 for item in snapshot["layers"].values() if item["status"] == "missing")
    print(f"Layers found: {found}  missing: {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
