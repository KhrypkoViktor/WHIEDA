"""Localhost-only target guard for parity runner."""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

ACCEPTANCE_ROOT = Path(__file__).resolve().parents[2] / "acceptance"
if str(ACCEPTANCE_ROOT) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE_ROOT))

LOCALHOST = re.compile(r"^https?://127\.0\.0\.1:8080$")
FORBIDDEN_HOST_FRAGMENTS = (
    "supabase",
    "duckdns.org",
    "185.252.",
    "wwc.best/api",
    "amazonaws.com",
    "neon.tech",
)


def validate_local_target(base_url: str) -> None:
    normalized = base_url.rstrip("/")
    if not LOCALHOST.match(normalized):
        raise ValueError(f"Refusing non-local parity target: {base_url!r} (required 127.0.0.1:8080)")
    lower = normalized.lower()
    for frag in FORBIDDEN_HOST_FRAGMENTS:
        if frag in lower:
            raise ValueError(f"Forbidden production fragment in target: {frag!r}")


def check_target_ready(
    target_path: Path,
    *,
    timeout_seconds: float = 15.0,
    request_fn: Callable[..., tuple[int, str, dict[str, str]]] | None = None,
) -> dict[str, Any]:
    from lab.target import load_target

    target = load_target(target_path)
    base = str(target["base_url"]).rstrip("/")
    try:
        validate_local_target(base)
    except ValueError as exc:
        return {"status": "FAIL", "errors": [str(exc)]}

    fn = request_fn or _urllib_get
    health_path = str(target.get("health_path") or "/health/ready")
    errors: list[str] = []
    try:
        status, body, _ = fn(f"{base}{health_path}", timeout_seconds=timeout_seconds)
        if status != 200:
            errors.append(f"health {health_path} returned HTTP {status}")
    except Exception as exc:
        errors.append(f"health check failed: {exc}")

    if errors:
        return {"status": "FAIL", "errors": errors, "base_url": base}
    return {"status": "PASS", "base_url": base, "health_path": health_path}


def _urllib_get(url: str, *, timeout_seconds: float) -> tuple[int, str, dict[str, str]]:
    req = urllib.request.Request(url, headers={"Host": "wwc.best"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace"), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body, dict(exc.headers)
