"""Redact secrets from acceptance artifacts."""

from __future__ import annotations

import copy
import re
from typing import Any

SECRET_PATTERNS = (
    re.compile(r"api\.telegram\.org", re.I),
    re.compile(r"duckdns\.org", re.I),
    re.compile(r"supabase", re.I),
    re.compile(r"platform_telegram_bot_token", re.I),
    re.compile(r"postgresql://[^@\s]+:[^@\s]+@", re.I),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]+", re.I),
    re.compile(r"\+375\d{9}"),
)


def redact_text(text: str) -> str:
    out = text
    for pattern in SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_value(v) for k, v in value.items()}
    return value


def redact_run_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(payload)
    for row in data.get("results") or []:
        row.pop("raw_response_path", None)
        req = row.get("request") or {}
        if "headers" in req:
            req["headers"] = redact_value(req["headers"])
        if row.get("answer_text"):
            row["answer_text"] = redact_text(str(row["answer_text"])[:500])
    return data
