"""Environment-backed credentials for WHIEDA operational scripts.

Tracked scripts must not embed live passwords. Set variables locally or in CI:

  WHIEDA_N8N_BASE_URL        default https://sysarchn8n.duckdns.org
  WHIEDA_N8N_EMAIL
  WHIEDA_N8N_PASSWORD
  WHIEDA_SSH_HOST            default 185.252.232.93
  WHIEDA_SSH_USER            default root
  WHIEDA_SSH_PASSWORD        or NORDMAN_LIVE_SSH_PASSWORD
  PGPASSWORD                 Supabase advisor runtime password
  PGHOST / PGPORT / PGDATABASE / PGUSER  optional overrides
  WHIEDA_TLS_VERIFY          default 1 (set 0 only for local debug)
"""

from __future__ import annotations

import os
from typing import Any

DEFAULT_PG = {
    "PGHOST": "aws-0-eu-west-1.pooler.supabase.com",
    "PGPORT": "6543",
    "PGDATABASE": "postgres",
    "PGUSER": "postgres.rlrehqqtirwrbaotjcxs",
}


def tls_verify() -> bool:
    return os.environ.get("WHIEDA_TLS_VERIFY", "1").strip().lower() not in {"0", "false", "no"}


def n8n_base_url() -> str:
    return os.environ.get("WHIEDA_N8N_BASE_URL", "https://sysarchn8n.duckdns.org").rstrip("/")


def n8n_login() -> tuple[str, str]:
    email = os.environ.get("WHIEDA_N8N_EMAIL", "").strip()
    password = os.environ.get("WHIEDA_N8N_PASSWORD", "").strip()
    if not email or not password:
        raise RuntimeError("Set WHIEDA_N8N_EMAIL and WHIEDA_N8N_PASSWORD")
    return email, password


def ssh_config() -> dict[str, str]:
    host = os.environ.get("WHIEDA_SSH_HOST", "185.252.232.93")
    user = os.environ.get("WHIEDA_SSH_USER", "root")
    password = (
        os.environ.get("WHIEDA_SSH_PASSWORD")
        or os.environ.get("NORDMAN_LIVE_SSH_PASSWORD")
        or ""
    ).strip()
    if not password:
        raise RuntimeError("Set WHIEDA_SSH_PASSWORD or NORDMAN_LIVE_SSH_PASSWORD")
    return {"host": host, "user": user, "password": password}


def site_ssh_config() -> dict[str, str]:
    """The site VPS (173.249.45.83). Lost in the 31.08.2026 consolidation and
    restored 22.09.2026: two live scripts import it — the staging cabinet smoke
    and the nginx theme-access patch."""
    host = os.environ.get("WHIEDA_SITE_SSH_HOST", "173.249.45.83")
    user = os.environ.get("WHIEDA_SITE_SSH_USER", "root")
    password = (
        os.environ.get("WHIEDA_SITE_SSH_PASSWORD")
        or os.environ.get("WHIEDA_SSH_PASSWORD_SITE")
        or ""
    ).strip()
    if not password:
        raise RuntimeError("Set WHIEDA_SITE_SSH_PASSWORD or WHIEDA_SSH_PASSWORD_SITE")
    return {"host": host, "user": user, "password": password}


def pg_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for key, value in DEFAULT_PG.items():
        env.setdefault(key, value)
    env.setdefault("PGCLIENTENCODING", "UTF8")
    if extra:
        env.update(extra)
    if not env.get("PGPASSWORD"):
        raise RuntimeError("PGPASSWORD is required for direct runtime reads")
    return env


def require_any(*names: str) -> dict[str, str]:
    values: dict[str, str] = {}
    missing = []
    for name in names:
        value = os.environ.get(name, "").strip()
        if not value:
            missing.append(name)
        else:
            values[name] = value
    if missing:
        raise RuntimeError(f"Missing required env: {', '.join(missing)}")
    return values
