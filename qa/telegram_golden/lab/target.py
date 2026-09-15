"""Golden HTTP target load, localhost guard, and check-target helpers."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ACCEPTANCE = Path(__file__).resolve().parents[2] / "acceptance"
if str(ACCEPTANCE) not in sys.path:
    sys.path.insert(0, str(ACCEPTANCE))

from lab.check_target import check_target_contract  # noqa: E402
from lab.target import TargetConfigError, load_target, validate_target  # noqa: E402
from lab.transport import UrllibTransport, urllib_request_fn  # noqa: E402

ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "host.docker.internal"})
FORBIDDEN_HOST_FRAGMENTS = (
    "duckdns.org",
    "185.252.",
    "supabase",
    "amazonaws.com",
    "neon.tech",
    "api.telegram.org",
)


class GoldenTargetError(Exception):
    pass


def validate_golden_target_url(base_url: str) -> None:
    parsed = urlparse(base_url.rstrip("/"))
    if parsed.scheme == "https":
        raise GoldenTargetError(f"HTTPS targets are forbidden for golden HTTP lab: {base_url!r}")
    if parsed.scheme not in {"http"}:
        raise GoldenTargetError(f"Unsupported target scheme: {parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise GoldenTargetError(f"Invalid target URL: {base_url!r}")
    if host not in ALLOWED_HOSTS:
        raise GoldenTargetError(
            f"Target host {host!r} is not allowed; use 127.0.0.1, localhost, or host.docker.internal"
        )
    lower = base_url.lower()
    for fragment in FORBIDDEN_HOST_FRAGMENTS:
        if fragment in lower:
            raise GoldenTargetError(f"Forbidden production fragment in target: {fragment!r}")


def load_golden_target(path: Path) -> dict[str, Any]:
    try:
        data = load_target(path)
    except TargetConfigError as exc:
        raise GoldenTargetError(str(exc)) from exc
    validate_golden_target_url(str(data["base_url"]))
    return data


def ensure_golden_target(example: Path, local: Path) -> Path:
    if local.is_file():
        return local
    if not example.is_file():
        raise GoldenTargetError(f"Missing golden target example: {example}")
    local.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    return local


def check_golden_target(target_path: Path, *, timeout_seconds: float = 10.0) -> dict[str, Any]:
    target = load_golden_target(target_path)
    request_fn = urllib_request_fn(timeout_seconds=timeout_seconds)
    contract = check_target_contract(target, request_fn)
    if contract.get("status") != "PASS":
        return {"status": "FAIL", "phase": "contract", **contract}

    advisor = target["advisor"]
    base = str(target["base_url"]).rstrip("/")
    url = f"{base}{advisor['path']}"
    headers = {str(k): str(v) for k, v in (advisor.get("headers") or {}).items()}
    body = {
        "question": "привет",
        "session": "golden-target-probe",
        "country": target.get("default_country") or "BY",
        "language": target.get("default_language") or "ru",
        "surface": "telegram",
        "ref": "ladnaya",
    }
    client = UrllibTransport()
    status, text, latency_ms = client.request(
        "POST",
        url,
        headers=headers,
        body=body,
        timeout_seconds=float((target.get("timeouts") or {}).get("request_seconds") or 12.0),
    )
    probe_errors: list[str] = []
    if status != 200:
        probe_errors.append(f"harmless probe HTTP {status}")
    else:
        try:
            payload = json.loads(text) if text else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            probe_errors.append("harmless probe response is not a JSON object")
        elif payload.get("ok") is not True:
            probe_errors.append("harmless probe ok!=true")
        elif not str(payload.get("answer_text") or "").strip():
            probe_errors.append("harmless probe empty answer_text")

    ok = not probe_errors
    return {
        "status": "PASS" if ok else "FAIL",
        "contract": contract,
        "probe": {
            "http_status": status,
            "latency_ms": round(latency_ms, 2),
            "answer_mode": (json.loads(text).get("answer_mode") if text else None),
            "errors": probe_errors,
        },
        "target_identity": {
            "name": target.get("name"),
            "base_url": target.get("base_url"),
            "advisor_path": advisor.get("path"),
        },
    }


def target_identity(target: dict[str, Any]) -> dict[str, Any]:
    advisor = target.get("advisor") or {}
    return {
        "name": target.get("name"),
        "base_url": target.get("base_url"),
        "advisor_path": advisor.get("path"),
        "default_country": target.get("default_country") or "BY",
    }
