#!/usr/bin/env python3
"""Expose server-backed personal-site theme access through the WWC facade."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import paramiko

from whieda_runtime_env import site_ssh_config

DEPLOY_SCRIPT = Path(__file__).resolve().parents[2] / "03_Website" / "wwc-best" / "scripts" / "deploy_paramiko_2026-08-03.py"


def load_site_password() -> None:
    if os.environ.get("WHIEDA_SITE_SSH_PASSWORD") or os.environ.get("WHIEDA_SSH_PASSWORD_SITE"):
        return
    text = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r'PASSWORD = "([^"]+)"', text)
    if not match:
        raise RuntimeError("site SSH password not found")
    os.environ["WHIEDA_SITE_SSH_PASSWORD"] = match.group(1)

CONF = "/etc/nginx/sites-enabled/wwc.best"
THEME_LOCATION = """
    # WHIEDA Platform Core theme access
    location ^~ /api/v1/theme-access {
        client_max_body_size 16k;
        proxy_pass https://sysarchn8n.duckdns.org/whieda-platform/api/v1/theme-access;
        proxy_ssl_server_name on;
        proxy_set_header Host sysarchn8n.duckdns.org;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host wwc.best;
        proxy_set_header X-WWC-Personal-Host $host;
        proxy_connect_timeout 5s;
        proxy_read_timeout 20s;
    }
"""
CONTENT_ACCESS_BLOCK = re.compile(
    r"(    location \^~ /api/v1/content-access \{.*?^    \})",
    re.MULTILINE | re.DOTALL,
)


def _exec(client: paramiko.SSHClient, command: str, timeout: int = 60) -> str:
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    if code:
        raise RuntimeError(f"{command}\n{out}\n{err}")
    return out


def patch_content(content: str) -> tuple[str, int]:
    if "location ^~ /api/v1/theme-access" in content:
        return content, 0
    return CONTENT_ACCESS_BLOCK.subn(r"\1\n" + THEME_LOCATION, content, count=2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    load_site_password()
    cfg = site_ssh_config()
    plan = {
        "site_host": cfg["host"],
        "nginx_conf": CONF,
        "route": "/api/v1/theme-access",
        "upstream": "https://sysarchn8n.duckdns.org/whieda-platform/api/v1/theme-access",
        "forwards_personal_host": True,
    }
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        cfg["host"],
        username=cfg["user"],
        password=cfg["password"],
        look_for_keys=False,
        allow_agent=False,
        timeout=30,
    )
    try:
        original = _exec(client, f"cat {CONF}")
        patched, replacements = patch_content(original)
        plan["replacements"] = replacements
        plan["already_present"] = replacements == 0 and "location ^~ /api/v1/theme-access" in original
        if not args.apply:
            print(json.dumps({"action": "dry-run", **plan}, ensure_ascii=False))
            if replacements not in {0, 1, 2}:
                raise RuntimeError(f"expected up to two content-access blocks, got {replacements}")
            return 0

        if plan["already_present"]:
            print(json.dumps({"action": "skip", **plan}, ensure_ascii=False))
            return 0
        if replacements not in {1, 2}:
            raise RuntimeError(f"expected one or two content-access blocks, got {replacements}")

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = f"{CONF}.bak-theme-access-{stamp}"
        sftp = client.open_sftp()
        try:
            with sftp.file(backup, "w") as handle:
                handle.write(original)
            with sftp.file(CONF, "w") as handle:
                handle.write(patched)
        finally:
            sftp.close()
        try:
            _exec(client, "nginx -t")
            _exec(client, "nginx -s reload")
            smoke = _exec(
                client,
                "curl -sk -o /tmp/wwc-theme-access-smoke.json -w '%{http_code}' --max-time 20 "
                "'https://sofiya.wwc.best/api/v1/theme-access/public?site_id=sofiya'",
            ).strip()
            if smoke != "200":
                raise RuntimeError(f"theme access facade smoke failed: {smoke}")
        except Exception:
            _exec(client, f"cp {backup} {CONF} && nginx -t && nginx -s reload")
            raise
        print(json.dumps({"action": "patched", "backup": backup, "public_theme_smoke": 200, **plan}, ensure_ascii=False))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
