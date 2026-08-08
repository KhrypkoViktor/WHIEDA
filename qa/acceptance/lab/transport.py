"""HTTP transport for acceptance target checks."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Callable


def urllib_request_fn(timeout_seconds: float = 15.0) -> Callable[[str, str, dict[str, str] | None, bytes | None], tuple[int, str]]:
    def _fn(method: str, url: str, headers: dict[str, str] | None, data: bytes | None) -> tuple[int, str]:
        req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", errors="replace")

    return _fn
