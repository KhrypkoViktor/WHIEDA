"""In-process HTTP capture for local Telegram Bot API calls."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Literal
from urllib.parse import urlparse

Behavior = Literal["ok", "fail_502", "fail_403", "drop"]


@dataclass
class CaptureEvent:
    method: str
    token: str
    bot_method: str
    path: str
    payload: dict[str, Any]
    status: int | None
    behavior: str


@dataclass
class TelegramCapture:
    host: str = "0.0.0.0"
    port: int = 18081
    events: list[CaptureEvent] = field(default_factory=list)
    script: list[Behavior] = field(default_factory=list)
    default_behavior: Behavior = "ok"
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _httpd: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    def queue(self, *behaviors: Behavior) -> None:
        with self._lock:
            self.script.extend(behaviors)

    def set_default(self, behavior: Behavior) -> None:
        with self._lock:
            self.default_behavior = behavior

    def clear_events(self) -> None:
        with self._lock:
            self.events.clear()

    def snapshot(self) -> list[CaptureEvent]:
        with self._lock:
            return list(self.events)

    def _next_behavior(self) -> Behavior:
        with self._lock:
            if self.script:
                return self.script.pop(0)
            return self.default_behavior

    def _record(self, event: CaptureEvent) -> None:
        with self._lock:
            self.events.append(event)

    def start(self) -> None:
        capture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path.rstrip("/") == "/health":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":true}')
                    return
                self.send_response(404)
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                parts = [item for item in parsed.path.split("/") if item]
                token = ""
                bot_method = ""
                if len(parts) >= 2 and parts[0].startswith("bot"):
                    token = parts[0][3:]
                    bot_method = parts[1]
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
                behavior = capture._next_behavior()
                if behavior == "drop":
                    capture._record(
                        CaptureEvent(
                            method="POST",
                            token=token,
                            bot_method=bot_method,
                            path=parsed.path,
                            payload=payload,
                            status=None,
                            behavior=behavior,
                        )
                    )
                    self.close_connection = True
                    try:
                        self.connection.close()
                    except OSError:
                        pass
                    return
                if behavior == "fail_502":
                    status = 502
                    body = {"ok": False, "error_code": 502, "description": "temporary"}
                elif behavior == "fail_403":
                    status = 403
                    body = {"ok": False, "error_code": 403, "description": "forbidden"}
                else:
                    status = 200
                    body = {"ok": True, "result": {"message_id": 1}}
                capture._record(
                    CaptureEvent(
                        method="POST",
                        token=token,
                        bot_method=bot_method,
                        path=parsed.path,
                        payload=payload,
                        status=status,
                        behavior=behavior,
                    )
                )
                encoded = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def photo_then_text_order(events: list[CaptureEvent], *, token: str) -> bool:
    outbound = [
        item
        for item in events
        if item.bot_method in {"sendPhoto", "sendMessage"} and item.token == token
    ]
    if len(outbound) < 2:
        return False
    if outbound[0].bot_method != "sendPhoto":
        return False
    if outbound[1].bot_method != "sendMessage":
        return False
    if outbound[0].payload.get("caption"):
        return False
    return True
