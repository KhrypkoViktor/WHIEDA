#!/usr/bin/env python3
"""Check the three approved WHIEDA solution bundles through a local Core API."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = ROOT / "qa" / "whieda_bundle_triage" / "active_bundle_regression_cases_v1.jsonl"
DEFAULT_BASE_URL = "http://127.0.0.1:8080"
EXPECTED_BUNDLES = {
    "bundle_energy_immunity",
    "bundle_vessels_belly",
    "bundle_shape_recovery",
}


def load_cases(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate_cases(cases: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    if len(cases) != 30:
        errors.append(f"expected 30 active-bundle cases, found {len(cases)}")
    by_bundle = {bundle_id: 0 for bundle_id in EXPECTED_BUNDLES}
    for row in cases:
        case_id = str(row.get("case_id") or "?")
        bundle_id = str(row.get("expected_bundle_id") or "")
        if bundle_id not in EXPECTED_BUNDLES:
            errors.append(f"{case_id}: unexpected bundle {bundle_id!r}")
            continue
        by_bundle[bundle_id] += 1
        if not str(row.get("user_text") or "").strip():
            errors.append(f"{case_id}: missing user_text")
        if not row.get("must_contain"):
            errors.append(f"{case_id}: missing must_contain")
        if not str((row.get("source") or {}).get("ref") or "").strip():
            errors.append(f"{case_id}: missing source.ref")
    for bundle_id, count in by_bundle.items():
        if count != 10:
            errors.append(f"{bundle_id}: expected 10 cases, found {count}")
    return errors


def require_local_url(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("Refusing non-local Core URL")
    return base_url.rstrip("/")


def query(base_url: str, case: dict[str, Any], timeout_seconds: float) -> tuple[int, dict[str, Any], float]:
    body = json.dumps(
        {
            "question": case["user_text"],
            "session": f"solution-bundle-acceptance-{case['case_id']}",
            "country": "BY",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url}/v1/advisor/query",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Host": "wwc.best"},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(raw), (time.perf_counter() - started) * 1000
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw}
        return exc.code, payload, (time.perf_counter() - started) * 1000


def evaluate(case: dict[str, Any], status: int, payload: dict[str, Any], latency_ms: float) -> list[str]:
    errors: list[str] = []
    if status != 200:
        errors.append(f"HTTP {status}")
        return errors
    if payload.get("answer_mode") != "structured_solution_bundle":
        errors.append(f"mode={payload.get('answer_mode')!r}")
    product = payload.get("product") or {}
    if product.get("bundle_id") != case.get("expected_bundle_id"):
        errors.append(f"bundle_id={product.get('bundle_id')!r}")
    answer = str(payload.get("answer_text") or "").lower().replace("ё", "е")
    for marker in case.get("must_contain") or []:
        if str(marker).lower().replace("ё", "е") not in answer:
            errors.append(f"missing text {marker!r}")
    if latency_ms > 4000:
        errors.append(f"latency {latency_ms:.0f}ms > 4000ms")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Lint the regression corpus only")
    parser.add_argument("--live", action="store_true", help="Run requests against local Core")
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--case-timeout", type=float, default=5.0)
    args = parser.parse_args()
    if not args.corpus.is_file():
        print(f"FAIL: corpus not found: {args.corpus}", file=sys.stderr)
        return 1
    cases = load_cases(args.corpus)
    errors = validate_cases(cases)
    if errors:
        print("Solution bundle corpus: FAIL")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"Solution bundle corpus: PASS ({len(cases)} cases)")
    if args.offline and not args.live:
        print("Solution bundle HTTP: NOT_RUN (offline)")
        return 0
    if not args.live:
        print("Use --offline or --live", file=sys.stderr)
        return 2
    try:
        base_url = require_local_url(args.base_url)
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    passed = 0
    for case in cases:
        try:
            status, payload, latency_ms = query(base_url, case, args.case_timeout)
            verdict = evaluate(case, status, payload, latency_ms)
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            verdict = [str(exc)]
        if verdict:
            print(f"FAIL {case['case_id']}: {'; '.join(verdict)}")
        else:
            passed += 1
    print(f"Solution bundles: {passed}/{len(cases)} passed")
    return 0 if passed == len(cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
