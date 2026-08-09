#!/usr/bin/env python3
"""Prove that a guided gap is persisted in the local Core Docker database only."""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import sys
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "postgres" / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "postgres" / "scripts"))

from staging_proof_lib import DOCKER_CONTAINER, LOCAL_STAGING_SUPERPASSWORD  # noqa: E402

LOCAL_CORE_DB = "whieda_platform_local_core"


def _post(base_url: str, question: str, session: str) -> dict:
    payload = json.dumps(
        {
            "question": question,
            "session": session,
            "ref": "ladnaya",
            "country": "RU",
            "language": "ru",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/advisor/query",
        data=payload,
        headers={"Host": "wwc.best", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _query_event(question: str) -> dict:
    escaped_question = question.replace("'", "''")
    sql = f"""
      select json_build_object(
        'repeat_count', payload->>'repeat_count',
        'gap_kind', payload->>'gap_kind',
        'question_normalized', payload->>'question_normalized',
        'session_ref', payload->>'session_ref',
        'first_seen_at', payload->>'first_seen_at',
        'last_seen_at', payload->>'last_seen_at'
      )::text
      from interaction_events
      where tenant_id = 'whieda'
        and event_type = 'advisor_gap'
        and payload->>'question_normalized' = '{escaped_question}'
      order by created_at desc
      limit 1;
    """
    command = [
        "docker",
        "exec",
        "-e",
        f"PGPASSWORD={LOCAL_STAGING_SUPERPASSWORD}",
        DOCKER_CONTAINER,
        "psql",
        "-U",
        "postgres",
        "-d",
        LOCAL_CORE_DB,
        "-At",
        "-c",
        sql,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "local event query failed")
    raw = result.stdout.strip()
    if not raw:
        raise RuntimeError("advisor_gap event was not persisted")
    return json.loads(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify local No Blind Zone gap persistence")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()

    nonce = secrets.token_hex(6)
    # ASCII makes this wire-level proof independent from the Windows console codepage.
    question = f"nbzproof{nonce}"
    session = f"nbz-db-proof-{nonce}"

    first = _post(args.base_url, question, session)
    second = _post(args.base_url, question, session)
    if first.get("gap_kind") != "unknown_product" or second.get("gap_kind") != "unknown_product":
        raise RuntimeError("proof request did not enter unknown_product gap path")

    event = _query_event(question)
    if event.get("repeat_count") != "2":
        raise RuntimeError(f"expected repeat_count=2, got {event.get('repeat_count')!r}")
    if event.get("gap_kind") != "unknown_product":
        raise RuntimeError(f"unexpected gap_kind {event.get('gap_kind')!r}")
    stored_question = str(event.get("question_normalized") or "").strip().casefold()
    expected_question = question.strip().casefold()
    if stored_question != expected_question:
        raise RuntimeError(
            "stored normalized question does not match proof question: "
            f"expected={expected_question!r} actual={stored_question!r}"
        )
    if len(str(event.get("session_ref") or "")) != 16:
        raise RuntimeError("stored session_ref must be a 16-character hash")
    if not event.get("first_seen_at") or not event.get("last_seen_at"):
        raise RuntimeError("gap event misses first_seen_at/last_seen_at")

    print("NBZ DB proof: PASS (one event, repeat_count=2, hashed session reference)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
