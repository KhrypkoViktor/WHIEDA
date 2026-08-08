#!/usr/bin/env python3
"""HTTP contract smoke for local Platform Core (localhost only)."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8080"

FORBIDDEN_RESPONSE_FRAGMENTS = (
    "postgresql://",
    "password",
    "traceback",
    "platform_telegram_bot_token",
    "duckdns.org",
    "api.telegram.org",
    "sysarchn8n",
    "185.252.",
    "supabase",
)

BLOCKED_OUTBOUND_MARKERS = (
    "/__local_lab_n8n_blocked__",
    "/__local_lab_lead_delivery_blocked__",
)


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> tuple[int, str, dict[str, str]]:
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body, dict(exc.headers)


def assert_no_secrets(body: str, *, context: str) -> None:
    low = body.lower()
    for frag in FORBIDDEN_RESPONSE_FRAGMENTS:
        if frag in low:
            raise AssertionError(f"{context}: forbidden fragment {frag!r} in response")


def check_health(base: str) -> None:
    for path in ("/health/live", "/health/ready"):
        status, body, _ = request("GET", f"{base}{path}", headers={"Host": "wwc.best"})
        if path.endswith("live"):
            if status != 200:
                raise AssertionError(f"{path} expected 200 got {status}")
        else:
            if status != 200:
                raise AssertionError(f"/health/ready expected 200 got {status} body={body}")
        assert_no_secrets(body, context=path)
    print("  PASS health")


def check_openapi(base: str) -> None:
    status, body, _ = request("GET", f"{base}/openapi.json", headers={"Host": "wwc.best"})
    if status != 200:
        raise AssertionError(f"openapi.json status {status}")
    data = json.loads(body)
    if "openapi" not in data or "paths" not in data:
        raise AssertionError("openapi.json missing expected keys")
    assert_no_secrets(body, context="openapi.json")
    print("  PASS openapi")


def check_invalid_json(base: str) -> None:
    status, body, _ = request(
        "POST",
        f"{base}/api/v1/leads",
        headers={"Host": "wwc.best", "Content-Type": "application/json"},
        data=b"not-json",
    )
    if status not in {400, 422}:
        raise AssertionError(f"invalid JSON expected 400/422 got {status} body={body}")
    assert_no_secrets(body, context="invalid-json")
    print("  PASS invalid-json-4xx")


def check_tenant_isolation(base: str) -> None:
    ok, body, _ = request(
        "GET",
        f"{base}/v1/public/ref/ladnaya",
        headers={"Host": "wwc.best"},
    )
    if ok != 200:
        raise AssertionError(f"whieda ref on wwc.best expected 200 got {ok}")

    missing, body2, _ = request("GET", f"{base}/v1/public/ref/ladnaya", headers={})
    if missing != 404:
        raise AssertionError(f"missing Host expected 404 got {missing}")

    cross, body3, _ = request(
        "GET",
        f"{base}/v1/public/ref/ladnaya",
        headers={"Host": "acme.test.local"},
    )
    if cross != 404:
        raise AssertionError(f"cross-tenant ref expected 404 got {cross} body={body3}")

    assert_no_secrets(body + body2 + body3, context="tenant-isolation")
    print("  PASS tenant-isolation")


def check_no_legacy_paths_in_openapi(base: str) -> None:
    """Smoke does not invoke Telegram webhook or n8n URLs."""
    _, body, _ = request("GET", f"{base}/openapi.json", headers={"Host": "wwc.best"})
    if "api.telegram.org" in body or "duckdns.org" in body:
        raise AssertionError("openapi exposes external legacy URLs")
    print("  PASS no-external-urls-in-openapi")


def check_blocked_integration_config(base: str) -> None:
    """Advisor/leads stay on core path; legacy base URL is localhost blackhole."""
    status, body, _ = request(
        "GET",
        f"{base}/v1/public/ref/ladnaya",
        headers={"Host": "wwc.best"},
    )
    if status != 200:
        raise AssertionError("core route smoke failed")
    for marker in BLOCKED_OUTBOUND_MARKERS:
        if marker in body:
            raise AssertionError(f"unexpected blocked marker in response: {marker}")
    print("  PASS local-core-routes")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    if not re.match(r"^https?://127\.0\.0\.1:\d+$", base) and not re.match(
        r"^https?://localhost:\d+$", base
    ):
        print(f"Refusing non-local base URL: {base}", file=sys.stderr)
        return 1

    print(f"=== local HTTP contract smoke: {base} ===")
    try:
        check_health(base)
        check_openapi(base)
        check_invalid_json(base)
        check_tenant_isolation(base)
        check_no_legacy_paths_in_openapi(base)
        check_blocked_integration_config(base)
    except AssertionError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("=== HTTP CONTRACT SMOKE: PASS ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
