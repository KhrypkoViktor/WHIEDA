"""Target gate and NOT_RUN payloads for blocked live runs."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab.check_target import check_target_contract
from lab.target import load_target
from lab.transport import urllib_request_fn


def check_target_ready(target_path: Path, *, timeout_seconds: float = 15.0) -> dict[str, Any]:
    target = load_target(target_path)
    request_fn = urllib_request_fn(timeout_seconds=timeout_seconds)
    result = check_target_contract(target, request_fn)
    result["target_path"] = str(target_path)
    return result


def build_target_blocked_payload(
    *,
    target_path: Path,
    corpus_path: Path,
    check_result: dict[str, Any],
) -> dict[str, Any]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    return {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": str(target_path),
        "corpus": str(corpus_path),
        "dry_run": False,
        "live_status": "NOT_RUN",
        "target_check": {
            "status": check_result.get("status"),
            "errors": check_result.get("errors") or [],
        },
        "results": [],
        "summary": {
            "total": 0,
            "pass": 0,
            "fail": 0,
            "skip": 0,
            "unasserted": 0,
            "not_run": 0,
            "by_priority": {},
            "latency_ms": {"p50": None, "p95": None, "max": None},
        },
    }
