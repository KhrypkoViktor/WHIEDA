#!/usr/bin/env python3
"""P0.3.5 post-deploy smoke — API/tunnel/public checks after apply."""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_N8N_CURRENT = _REPO_ROOT / "n8n" / "current"
if str(_N8N_CURRENT) not in sys.path:
    sys.path.insert(0, str(_N8N_CURRENT))

HOST = "admin-staging.wwc.best"
PUBLIC_BASE = f"https://{HOST}"


def http_fetch(url: str, *, insecure: bool) -> tuple[int, str]:
    ctx = ssl._create_unverified_context() if insecure else None
    try:
        with urllib.request.urlopen(url, timeout=20, context=ctx) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def check_core_health_local() -> dict:
    try:
        from whieda_runtime_env import ssh_config
        from staging_deploy_lib import connect_ssh, ssh_exec

        cfg = ssh_config()
        client = connect_ssh(cfg, timeout=20)
        try:
            code = ssh_exec(
                client,
                "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8081/health/live",
            ).strip()
            return {"check": "core_local_8081_health", "status": code, "ok": code == "200"}
        finally:
            client.close()
    except Exception as exc:
        return {"check": "core_local_8081_health", "ok": False, "error": type(exc).__name__}


def check_site_tunnel_health_local() -> dict:
    try:
        from whieda_runtime_env import site_ssh_config
        from staging_deploy_lib import connect_ssh, ssh_exec

        site = site_ssh_config()
        client = connect_ssh(site, timeout=20)
        try:
            code = ssh_exec(
                client,
                "curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:18081/health/live",
            ).strip()
            return {"check": "site_local_18081_health", "status": code, "ok": code == "200"}
        finally:
            client.close()
    except Exception as exc:
        return {"check": "site_local_18081_health", "ok": False, "error": type(exc).__name__}


def check_tunnel_unit() -> dict:
    try:
        from whieda_runtime_env import ssh_config
        from staging_deploy_lib import connect_ssh, ssh_exec

        cfg = ssh_config()
        client = connect_ssh(cfg, timeout=20)
        try:
            state = ssh_exec(
                client,
                "systemctl is-active wwc-admin-staging-tunnel.service 2>/dev/null || echo inactive",
            ).strip()
            return {
                "check": "core_tunnel_unit",
                "state": state,
                "ok": state == "active",
            }
        finally:
            client.close()
    except Exception as exc:
        return {"check": "core_tunnel_unit", "ok": False, "error": type(exc).__name__}


def check_public_http(*, insecure: bool) -> dict:
    results = []
    status, body = http_fetch(f"{PUBLIC_BASE}/cabinet/", insecure=insecure)
    results.append({"path": "/cabinet/", "status": status, "ok": status == 200})

    status, body = http_fetch(f"{PUBLIC_BASE}/wwc-cabinet-config.json", insecure=insecure)
    cfg_ok = False
    if status == 200:
        try:
            cfg = json.loads(body)
            cfg_ok = cfg.get("enabled") is True
        except json.JSONDecodeError:
            pass
    results.append({"path": "/wwc-cabinet-config.json", "status": status, "ok": cfg_ok})

    status, _ = http_fetch(f"{PUBLIC_BASE}/api/v1/admin/me", insecure=insecure)
    me_ok = status == 401
    results.append({
        "path": "/api/v1/admin/me",
        "status": status,
        "ok": me_ok,
        "hint": "401=proxy+auth OK; 404=proxy/API not connected",
    })

    ok = all(r["ok"] for r in results)
    return {"check": "public_http", "ok": ok, "results": results, "insecure_tls": insecure}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--insecure", action="store_true", help="Skip TLS hostname verify for public checks")
    args = parser.parse_args()

    checks = [
        check_core_health_local(),
        check_tunnel_unit(),
        check_site_tunnel_health_local(),
        check_public_http(insecure=args.insecure),
    ]

    failed = [c for c in checks if not c.get("ok")]
    report = {
        "phase": "post_deploy_smoke",
        "host": HOST,
        "passed": len(failed) == 0,
        "failed_count": len(failed),
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
