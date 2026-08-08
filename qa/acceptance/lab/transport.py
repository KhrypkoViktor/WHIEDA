"""HTTP transport for acceptance runs (injectable for tests)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol


class Transport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> tuple[int, str, float]:
        """Return status, response body text, latency_ms."""


class UrllibTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> tuple[int, str, float]:
        data = None
        hdrs = dict(headers or {})
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, method=method.upper(), headers=hdrs)
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                latency_ms = (time.perf_counter() - started) * 1000.0
                return resp.status, text, latency_ms
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            latency_ms = (time.perf_counter() - started) * 1000.0
            return exc.code, text, latency_ms
        except TimeoutError:
            raise TimeoutError(f"request timed out after {timeout_seconds}s") from None


def urllib_request_fn(timeout_seconds: float = 15.0) -> Callable[[str, str, dict[str, str] | None, bytes | None], tuple[int, str]]:
    transport = UrllibTransport()

    def _fn(method: str, url: str, headers: dict[str, str] | None, data: bytes | None) -> tuple[int, str]:
        body = json.loads(data.decode("utf-8")) if data else None
        status, text, _ = transport.request(method, url, headers=headers, body=body, timeout_seconds=timeout_seconds)
        return status, text

    return _fn
