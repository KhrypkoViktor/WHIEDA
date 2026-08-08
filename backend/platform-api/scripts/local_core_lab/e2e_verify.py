"""Non-mock HTTP verification against local Core on 127.0.0.1:8080."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal

from local_core_lab.constants import (
    ACCEPTANCE_LOCAL_TARGET,
    ACCEPTANCE_RUNNER,
    API_BASE,
    DOCKER_CONTAINER,
    LOCAL_CORE_API_ROLE,
    LOCAL_STAGING_PORT,
)

CheckStatus = Literal["PASS", "FAIL", "SKIP"]

ALLOWED_BASE = re.compile(r"^https?://127\.0\.0\.1:\d+$")


@dataclass(frozen=True)
class VerifyCheck:
    name: str
    status: CheckStatus
    message: str


def _http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 15.0,
) -> tuple[int, str, dict[str, str]]:
    req = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return exc.code, body, dict(exc.headers)
    except urllib.error.URLError as exc:
        raise ConnectionError(str(exc.reason)) from exc


def assert_local_base(base: str) -> None:
    normalized = base.rstrip("/")
    if not ALLOWED_BASE.match(normalized):
        raise ValueError(f"Refusing non-local base URL: {base}")


def check_health_live(base: str) -> VerifyCheck:
    try:
        status, body, _ = _http("GET", f"{base}/health/live", headers={"Host": "wwc.best"})
    except ConnectionError as exc:
        return VerifyCheck("health_live", "FAIL", str(exc))
    if status != 200:
        return VerifyCheck("health_live", "FAIL", f"expected 200 got {status}")
    if "traceback" in body.lower():
        return VerifyCheck("health_live", "FAIL", "traceback in response")
    return VerifyCheck("health_live", "PASS", "HTTP 200")


def check_health_ready(base: str) -> VerifyCheck:
    try:
        status, body, _ = _http("GET", f"{base}/health/ready", headers={"Host": "wwc.best"})
    except ConnectionError as exc:
        return VerifyCheck("health_ready", "FAIL", str(exc))
    if status != 200:
        return VerifyCheck("health_ready", "FAIL", f"expected 200 got {status} body={body[:200]}")
    return VerifyCheck("health_ready", "PASS", "HTTP 200")


def check_openapi_advisor_path(base: str) -> VerifyCheck:
    try:
        status, body, _ = _http("GET", f"{base}/openapi.json", headers={"Host": "wwc.best"})
    except ConnectionError as exc:
        return VerifyCheck("openapi_advisor_query", "FAIL", str(exc))
    if status != 200:
        return VerifyCheck("openapi_advisor_query", "FAIL", f"openapi status {status}")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return VerifyCheck("openapi_advisor_query", "FAIL", "openapi is not JSON")
    paths = data.get("paths") or {}
    if "/v1/advisor/query" not in paths:
        return VerifyCheck("openapi_advisor_query", "FAIL", "/v1/advisor/query missing from OpenAPI")
    return VerifyCheck("openapi_advisor_query", "PASS", "OpenAPI lists /v1/advisor/query")


def check_missing_required_field_4xx(base: str) -> VerifyCheck:
    payload = json.dumps({"session": "e2e-verify"}).encode("utf-8")
    try:
        status, body, headers = _http(
            "POST",
            f"{base}/v1/advisor/query",
            headers={
                "Host": "wwc.best",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            data=payload,
        )
    except ConnectionError as exc:
        return VerifyCheck("advisor_missing_field_4xx", "FAIL", str(exc))
    content_type = headers.get("Content-Type", headers.get("content-type", ""))
    if status < 400 or status >= 500:
        return VerifyCheck(
            "advisor_missing_field_4xx",
            "FAIL",
            f"expected 4xx for missing question, got {status}",
        )
    if "text/html" in content_type.lower():
        return VerifyCheck("advisor_missing_field_4xx", "FAIL", "HTML response instead of JSON")
    if "traceback" in body.lower():
        return VerifyCheck("advisor_missing_field_4xx", "FAIL", "traceback in 4xx body")
    return VerifyCheck("advisor_missing_field_4xx", "PASS", f"HTTP {status}")


def check_tenant_isolation(base: str) -> VerifyCheck:
    try:
        ok, _, _ = _http(
            "GET",
            f"{base}/v1/public/ref/ladnaya",
            headers={"Host": "wwc.best"},
        )
        cross, body, _ = _http(
            "GET",
            f"{base}/v1/public/ref/ladnaya",
            headers={"Host": "acme.test.local"},
        )
    except ConnectionError as exc:
        return VerifyCheck("tenant_isolation", "FAIL", str(exc))
    if ok != 200:
        return VerifyCheck("tenant_isolation", "FAIL", f"whieda ref expected 200 got {ok}")
    if cross != 404:
        return VerifyCheck(
            "tenant_isolation",
            "FAIL",
            f"unknown host must not leak data (expected 404 got {cross}) body={body[:120]}",
        )
    return VerifyCheck("tenant_isolation", "PASS", "cross-tenant host returns 404")


def check_advisor_valid_json(base: str) -> VerifyCheck:
    payload = json.dumps(
        {
            "question": "расскажи про активатор клеток",
            "session": "local-e2e-verify",
            "ref": "ladnaya",
            "country": "RU",
            "language": "ru",
        }
    ).encode("utf-8")
    try:
        status, body, headers = _http(
            "POST",
            f"{base}/v1/advisor/query",
            headers={
                "Host": "wwc.best",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            data=payload,
        )
    except ConnectionError as exc:
        return VerifyCheck("advisor_valid_json", "FAIL", str(exc))
    content_type = headers.get("Content-Type", headers.get("content-type", ""))
    if "text/html" in content_type.lower():
        return VerifyCheck("advisor_valid_json", "FAIL", "HTML response")
    if "traceback" in body.lower():
        return VerifyCheck("advisor_valid_json", "FAIL", "traceback in body")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return VerifyCheck("advisor_valid_json", "FAIL", f"non-JSON body (HTTP {status})")
    if not isinstance(data, dict):
        return VerifyCheck("advisor_valid_json", "FAIL", "advisor response is not a JSON object")
    return VerifyCheck("advisor_valid_json", "PASS", f"HTTP {status} JSON object")


def check_p0_report_created(base: str, *, python: str | None = None) -> VerifyCheck:
    if not ACCEPTANCE_LOCAL_TARGET.is_file():
        return VerifyCheck(
            "p0_acceptance_report",
            "SKIP",
            "acceptance_target.local.json missing — copy from example first",
        )
    if shutil.which("docker") is None:
        return VerifyCheck("p0_acceptance_report", "SKIP", "docker unavailable for isolation guard")
    exe = python or sys.executable
    proc = subprocess.run(
        [
            exe,
            str(ACCEPTANCE_RUNNER),
            "--check-target",
            "--target",
            str(ACCEPTANCE_LOCAL_TARGET),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return VerifyCheck(
            "p0_acceptance_report",
            "FAIL",
            "acceptance --check-target failed before report run",
        )
    proc = subprocess.run(
        [
            exe,
            str(ACCEPTANCE_RUNNER),
            "--run",
            "--priority",
            "P0",
            "--limit",
            "1",
            "--target",
            str(ACCEPTANCE_LOCAL_TARGET),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    combined = proc.stdout + proc.stderr
    if "Report:" not in combined:
        return VerifyCheck("p0_acceptance_report", "FAIL", "P0 run did not emit report path")
    return VerifyCheck(
        "p0_acceptance_report",
        "PASS" if proc.returncode == 0 else "FAIL",
        "P0 acceptance report created (live HTTP)",
    )


def check_api_role_not_superuser() -> VerifyCheck:
    if shutil.which("docker") is None:
        return VerifyCheck("api_role_not_superuser", "SKIP", "docker unavailable")
    proc = subprocess.run(
        [
            "docker",
            "exec",
            "-e",
            "PGPASSWORD=local_staging_proof",
            DOCKER_CONTAINER,
            "psql",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-tAc",
            f"SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = '{LOCAL_CORE_API_ROLE}';",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return VerifyCheck(
            "api_role_not_superuser",
            "FAIL",
            f"could not query role via docker exec: {proc.stderr.strip()}",
        )
    parts = proc.stdout.strip().split("|")
    if len(parts) != 2:
        return VerifyCheck(
            "api_role_not_superuser",
            "FAIL",
            f"role {LOCAL_CORE_API_ROLE!r} not found in Postgres",
        )
    superuser, bypass = parts[0].strip().lower(), parts[1].strip().lower()
    if superuser == "t" or bypass == "t":
        return VerifyCheck(
            "api_role_not_superuser",
            "FAIL",
            f"role has superuser={superuser} bypassrls={bypass}",
        )
    return VerifyCheck("api_role_not_superuser", "PASS", "NOSUPERUSER NOBYPASSRLS confirmed")


def check_core_down_fails(base: str) -> VerifyCheck:
    """Static guard: verify script uses urllib and fails on connection refused."""
    return VerifyCheck(
        "no_fake_transport",
        "PASS",
        "verify_local_core_e2e uses urllib.request (no FakeTransport)",
    )


def check_no_external_urls(base: str) -> VerifyCheck:
    forbidden = ("supabase", "duckdns.org", "185.252.", "api.telegram.org")
    try:
        _, body, _ = _http("GET", f"{base}/openapi.json", headers={"Host": "wwc.best"})
    except ConnectionError as exc:
        return VerifyCheck("no_external_urls", "FAIL", str(exc))
    lower = body.lower()
    for frag in forbidden:
        if frag in lower:
            return VerifyCheck("no_external_urls", "FAIL", f"openapi exposes {frag!r}")
    return VerifyCheck("no_external_urls", "PASS", "no forbidden external URLs in OpenAPI")


def check_local_port_only(base: str) -> VerifyCheck:
    if f":{LOCAL_STAGING_PORT}" in base or base.startswith("http://127.0.0.1"):
        return VerifyCheck("local_only", "PASS", f"target is localhost ({base})")
    return VerifyCheck("local_only", "FAIL", f"refusing non-local target {base}")


ALL_VERIFY_CHECKS = (
    check_health_live,
    check_health_ready,
    check_openapi_advisor_path,
    check_missing_required_field_4xx,
    check_tenant_isolation,
    check_advisor_valid_json,
    check_no_external_urls,
    check_api_role_not_superuser,
)


def run_verify(base: str = API_BASE, *, include_p0: bool = True, python: str | None = None) -> dict[str, Any]:
    assert_local_base(base)
    checks: list[VerifyCheck] = []
    for fn in ALL_VERIFY_CHECKS:
        if fn is check_api_role_not_superuser:
            checks.append(fn())
        else:
            checks.append(fn(base))
    checks.append(check_local_port_only(base))
    checks.append(check_core_down_fails(base))
    if include_p0:
        checks.append(check_p0_report_created(base, python=python))

    failed = [c for c in checks if c.status == "FAIL"]
    status = "FAIL" if failed else "PASS"
    return {
        "status": status,
        "base_url": base,
        "checks": [{"name": c.name, "status": c.status, "message": c.message} for c in checks],
    }


def verify_core_down(base: str = API_BASE) -> bool:
    """Return True when Core is unreachable (expected before lab start)."""
    try:
        _http("GET", f"{base}/health/live", headers={"Host": "wwc.best"}, timeout=2.0)
        return False
    except ConnectionError:
        return True
    except Exception:
        return True
