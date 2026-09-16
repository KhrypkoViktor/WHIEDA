#!/usr/bin/env python3
"""Zone check for a branch: files changed against the base may belong only to
the branch's zone (`<zone>/<topic>`) or to the shared append-only list.

    python .github/scripts/check_zone.py --base origin/master --branch nsp/canary

Exit 1 with a readable list when something is out of zone, when a shared
file lost lines, or when a forbidden path (pycache, .env, secrets) is added.
Branches without a zone prefix (e.g. `master`, `wip/…`) are checked only for
forbidden paths.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ZONES = json.loads((ROOT / ".github" / "zones.json").read_text(encoding="utf-8"))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def changed_files(base: str) -> list[tuple[str, str]]:
    out = _git("diff", "--name-status", f"{base}...HEAD")
    rows = []
    for line in out.splitlines():
        parts = line.split("\t")
        status, path = parts[0][0], parts[-1]
        rows.append((status, path.replace("\\", "/")))
    return rows


def shared_lost_lines(base: str, path: str) -> bool:
    stat = _git("diff", "--numstat", f"{base}...HEAD", "--", path).strip()
    if not stat:
        return False
    added, deleted, _ = stat.split("\t")
    return deleted not in ("0", "-")


def zone_of(path: str) -> str | None:
    for zone, spec in ZONES["zones"].items():
        if any(path.startswith(prefix) for prefix in spec["paths"]):
            return zone
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/master")
    parser.add_argument("--branch", default=None, help="branch name; default: current")
    args = parser.parse_args()
    branch = args.branch or _git("rev-parse", "--abbrev-ref", "HEAD").strip()
    my_zone = branch.split("/", 1)[0] if "/" in branch else None
    if my_zone not in ZONES["zones"]:
        my_zone = None

    problems: list[str] = []
    for status, path in changed_files(args.base):
        if any(pat in path for pat in ZONES["forbidden"]["patterns"]) and status != "D" and not path.endswith(".example"):
            problems.append(f"forbidden in git: {path}")
            continue
        if my_zone is None:
            continue
        if path in ZONES["shared_append_only"]["paths"]:
            if shared_lost_lines(args.base, path):
                problems.append(f"shared file must only grow (lines deleted): {path}")
            continue
        owner = zone_of(path)
        if owner is not None and owner != my_zone:
            problems.append(f"belongs to zone «{owner}», branch is «{my_zone}»: {path}")

    if problems:
        print(f"ZONE CHECK FAILED for branch {branch!r} ({len(problems)}):")
        for p in problems:
            print("  -", p)
        print("Ask the zone's owner to make the change, or move the file (see AGENTS.md → «Совместная работа агентов»).")
        return 1
    print(f"ZONE CHECK OK for branch {branch!r} (zone: {my_zone or 'none — forbidden paths only'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
