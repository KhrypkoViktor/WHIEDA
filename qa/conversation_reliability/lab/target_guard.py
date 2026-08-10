"""Target guard for conversation reliability runner."""

from __future__ import annotations

import re

LOCALHOST = re.compile(r"^https?://(127\.0\.0\.1|localhost):8080$")
FORBIDDEN = ("duckdns.org", "185.252.", "supabase", "amazonaws.com", "neon.tech")


def validate_local_target(base_url: str) -> None:
    normalized = base_url.rstrip("/")
    if not LOCALHOST.match(normalized):
        raise ValueError(f"Refusing non-local target {base_url!r}; required 127.0.0.1:8080 or localhost:8080")
    lower = normalized.lower()
    for frag in FORBIDDEN:
        if frag in lower:
            raise ValueError(f"Forbidden production fragment in target: {frag!r}")
