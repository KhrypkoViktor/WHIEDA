"""Refuse non-local / apply / publish before any canary network starts."""

from __future__ import annotations

import json
from urllib.parse import urlparse

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "host.docker.internal"})


def refuse_payload(message: str) -> str:
    return json.dumps(
        {"ok": False, "code": "action_refused", "message": message},
        ensure_ascii=False,
    )


def is_local_host(host: str | None) -> bool:
    return str(host or "").strip().lower() in LOCAL_HOSTS


def is_local_url(raw: str | None) -> bool:
    text = str(raw or "").strip()
    if not text:
        return False
    parsed = urlparse(text)
    return parsed.scheme in {"http", "https"} and is_local_host(parsed.hostname)


def is_local_dsn(raw: str | None) -> bool:
    text = str(raw or "").strip()
    if not text.lower().startswith("postgres"):
        return False
    parsed = urlparse(text)
    return is_local_host(parsed.hostname)


def refuse_lab_flags(*, apply: bool, publish: bool, dsn: str | None, base_url: str | None) -> str | None:
    if apply or publish:
        return refuse_payload("--apply and --publish are refused")
    if dsn:
        return refuse_payload("live DSN is refused")
    if base_url and not is_local_url(base_url):
        return refuse_payload("Refusing non-local target")
    return None
