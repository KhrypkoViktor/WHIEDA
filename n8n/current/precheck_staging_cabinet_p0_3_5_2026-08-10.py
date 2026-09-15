"""P0.3.5 pre-deploy check — prerequisites only, no live API required."""

from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOST = "admin-staging.wwc.best"
SITE_VPS = "173.249.45.83"
SECRETS_FILE = "/opt/whieda-platform-staging/secrets/cabinet-staging.env"
CERT_PATH = f"/etc/letsencrypt/live/{HOST}/fullchain.pem"
REQUIRED_SECRET_KEYS = [
    "PLATFORM_TELEGRAM_BOT_TOKEN",
    "PLATFORM_TELEGRAM_BOT_USERNAME",
    "PLATFORM_TELEGRAM_WEBHOOK_SECRET",
    "PLATFORM_ADMIN_SUPER_TELEGRAM_IDS",
    "PLATFORM_ADMIN_CONFIRM_SECRET",
]
LOCAL_REQUIRED = [
    REPO_ROOT / "backend" / "deploy" / "staging" / "admin-staging.wwc.best.nginx.conf",
    REPO_ROOT / "backend" / "deploy" / "staging" / "wwc-cabinet-config.staging.json",
    REPO_ROOT / "backend" / "deploy" / "staging" / "docker-compose.yml",
    REPO_ROOT / "03_Website" / "wwc-best" / "package.json",
]


def check_dns() -> dict:
    try:
        ip = socket.gethostbyname(HOST)
        ok = ip == SITE_VPS
        return {"check": "dns", "host": HOST, "ip": ip, "expected": SITE_VPS, "ok": ok}
    except OSError as exc:
        return {"check": "dns", "ok": False, "error": str(exc)}


def check_local_sources() -> dict:
    missing = [str(p.relative_to(REPO_ROOT)) for p in LOCAL_REQUIRED if not p.is_file()]
    return {"check": "local_sources", "ok": not missing, "missing": missing}


def check_core_secrets() -> dict:
    try:
        from whieda_runtime_env import ssh_config
        from staging_deploy_lib import connect_ssh, ssh_exec

        cfg = ssh_config()
        client = connect_ssh(cfg, timeout=20)
        try:
            exists = ssh_exec(client, f"test -f {SECRETS_FILE} && echo yes || echo no").strip()
            if exists != "yes":
                return {
                    "check": "core_secrets",
                    "ok": False,
                    "code": "missing_server_secret",
                    "file": SECRETS_FILE,
                }

            mode = ssh_exec(client, f"stat -c '%a' {SECRETS_FILE}").strip()
            keys_raw = ssh_exec(
                client,
                f"grep -E '^[A-Z_]+=' {SECRETS_FILE} | cut -d= -f1 | sort -u",
            )
            keys = {line.strip() for line in keys_raw.splitlines() if line.strip()}
            missing = [k for k in REQUIRED_SECRET_KEYS if k not in keys]
            empty = []
            for line in ssh_exec(
                client,
                f"grep -E '^({'|'.join(REQUIRED_SECRET_KEYS)})=' {SECRETS_FILE} || true",
            ).splitlines():
                if "=" in line:
                    key, _, val = line.partition("=")
                    if not val.strip():
                        empty.append(key.strip())

            ok = not missing and not empty and mode == "600"
            result = {
                "check": "core_secrets",
                "ok": ok,
                "mode": mode,
                "keys_present": sorted(keys & set(REQUIRED_SECRET_KEYS)),
                "missing_keys": missing,
                "empty_keys": empty,
            }
            if not ok:
                result["code"] = "missing_server_secret"
            return result
        finally:
            client.close()
    except Exception as exc:
        return {"check": "core_secrets", "ok": False, "code": "core_ssh_failed", "error": type(exc).__name__}


def check_core_ssh() -> dict:
    try:
        from whieda_runtime_env import ssh_config
        from staging_deploy_lib import connect_ssh, ssh_exec

        cfg = ssh_config()
        client = connect_ssh(cfg, timeout=15)
        try:
            ssh_exec(client, "echo ok")
            return {"check": "core_ssh", "ok": True, "host": cfg["host"]}
        finally:
            client.close()
    except Exception as exc:
        return {"check": "core_ssh", "ok": False, "code": "core_ssh_failed", "error": type(exc).__name__}


def check_site_tls() -> dict:
    try:
        from whieda_runtime_env import site_ssh_config
        from staging_deploy_lib import connect_ssh, ssh_exec

        site = site_ssh_config()
        client = connect_ssh(site, timeout=15)
        try:
            cert = ssh_exec(client, f"test -f {CERT_PATH} && echo yes || echo no").strip()
            return {
                "check": "site_tls_cert",
                "ok": cert == "yes",
                "cert_path": CERT_PATH,
                "code": None if cert == "yes" else "missing_tls_cert",
            }
        finally:
            client.close()
    except Exception as exc:
        return {"check": "site_tls_cert", "ok": False, "code": "missing_site_ssh", "error": type(exc).__name__}


def check_site_ssh() -> dict:
    try:
        from whieda_runtime_env import site_ssh_config
        from staging_deploy_lib import connect_ssh, ssh_exec

        site = site_ssh_config()
        client = connect_ssh(site, timeout=15)
        try:
            ssh_exec(client, "echo ok")
            return {"check": "site_ssh", "ok": True, "host": site["host"]}
        finally:
            client.close()
    except Exception as exc:
        return {"check": "site_ssh", "ok": False, "code": "missing_site_ssh", "error": type(exc).__name__}


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-deploy checks only (P0.3.5)")
    args = parser.parse_args()

    checks = [
        check_dns(),
        check_local_sources(),
        check_core_ssh(),
        check_core_secrets(),
        check_site_ssh(),
        check_site_tls(),
    ]

    failed = [c for c in checks if not c.get("ok")]
    codes = sorted({c.get("code") for c in failed if c.get("code")})

    report = {
        "phase": "precheck",
        "host": HOST,
        "ready_for_apply": len(failed) == 0,
        "blocker_codes": codes,
        "note": "Does NOT require /api/v1/admin/me, port 8081, or Docker - use post_deploy_smoke after apply",
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if any(c.get("code") == "missing_server_secret" for c in failed):
        return 1
    if any(c.get("code") == "missing_site_ssh" for c in failed):
        return 2
    if any(c.get("code") == "missing_tls_cert" for c in failed):
        return 4
    return 0 if report["ready_for_apply"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
